// 会话运行时：发消息 → 任务事件轮询 → 时间线渲染；审批事件自动弹出
// 协议依据 docs/runtime_protocol.md：run.start(POST /api/tasks) → run.events(GET ?from=offset)；
// 事件窗口缺口（offset < window_start）按 §5.2 提示并对齐续读（CLI 不做快照对账）。
import type { GatewayClient, TaskPollResult } from './gateway';
import type { ContextStats, PendingApprovalMock } from './data';
import type { TimelineApi } from './timeline';
import { approvalActions, approvalArguments, mergeApprovalState, type ToolApprovalItem, type AutoReviewProgress } from './approval';

const POLL_INTERVAL_MS = 500;
/** 事件轮询空闲退避上限（连续空响应时） */
const POLL_MAX_BACKOFF_MS = 2000;

/** 工具名 → 显示标签（i18n；未映射的用原名） */
const TOOL_LABEL_KEYS: Record<string, string> = {
  run_command: 'tool.run_command',
  write_file: 'tool.write_file',
  edit_file: 'tool.edit_file',
  read_file: 'tool.read_file',
};

export interface RuntimeCallbacks {
  api: TimelineApi;
  /** 运行态变化（状态栏「运行中/空闲」、Enter 排队语义） */
  onRunningChange(running: boolean): void;
  /** 审批创建或同 ID 状态更新；更新不重新打开用户已关闭的面板。 */
  onApprovalRequired(approval: PendingApprovalMock): void;
  /** 仅服务端终态回执清除审批（包括其他客户端裁决）。 */
  onApprovalResolved(approvalId: string): void;
  /** 系统消息（审批回执/错误/窗口缺口提示等），经 menuState 的 sys 通道上屏 */
  onSystemMessage(text: string): void;
  /** token 统计更新（token_update 事件流）：刷新状态栏与 /context 面板 */
  onTokenUpdate?(stats: ContextStats): void;
  /** 首条消息触发服务端惰性建会话后回调（/new 草稿 → 真实会话，列表插入用） */
  onConversationCreated?(id: string): void;
  /** 审批操作翻译函数（i18n t()） */
  tr(key: string): string;
}

export class ChatRuntime {
  conversationId = '';
  running = false;
  private taskId = '';
  private offset = 0;
  private polling = false;
  private destroyed = false;
  // 同一对话串行审批，仅保存当前请求和最近终结 ID。
  private pendingApproval: PendingApprovalMock | null = null;
  private resolvedApprovalId = '';

  constructor(
    private readonly gw: GatewayClient,
    private readonly cb: RuntimeCallbacks,
  ) {}

  destroy(): void {
    this.destroyed = true;
  }

  /** /new（对齐 web 端 /new 页面语义）：进入空对话草稿状态——不立即创建会话，
   *  清空时间线并置空 conversationId；下一条 send 不带 cid，服务端惰性创建。 */
  enterNewSession(): void {
    this.conversationId = '';
    this.taskId = '';
    this.offset = 0;
    if (this.pendingApproval) this.cb.onApprovalResolved(this.pendingApproval.id);
    this.pendingApproval = null;
    this.resolvedApprovalId = '';
    this.cb.api.reset();
  }

  /** 发送一条用户消息并启动事件轮询（空闲时调用；运行中走排队由上层管理）。
   *  opts 为会话级模型/模式覆盖（/model 面板生效值），缺省由服务端用对话/个性化默认。 */
  async send(text: string, opts?: { model_key?: string; run_mode?: string; thinking_mode?: boolean }): Promise<void> {
    this.cb.api.addUser(text);
    const wasDraft = !this.conversationId;
    let accepted: { task_id: string; conversation_id?: string };
    try {
      accepted = await this.gw.createTask({
        message: text,
        conversation_id: this.conversationId || undefined,
        model_key: opts?.model_key,
        run_mode: opts?.run_mode,
        thinking_mode: opts?.thinking_mode,
      });
    } catch (err) {
      this.cb.onSystemMessage(`${this.cb.tr('runtime.sendFailed')}${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    this.taskId = accepted.task_id;
    if (accepted.conversation_id) this.conversationId = accepted.conversation_id;
    // /new 草稿的首条消息：服务端已建会话，通知上层把新会话插入列表
    if (wasDraft && accepted.conversation_id) this.cb.onConversationCreated?.(accepted.conversation_id);
    this.offset = 0;
    this.setRunning(true);
    void this.pollLoop();
  }

  /** 提交人工项；接口成功仍等待 tool_approval_resolved，不宣称工具已执行。 */
  async decideApproval(approvalId: string, decision: 'approved' | 'rejected'): Promise<void> {
    const pending = this.pendingApproval;
    if (!pending || pending.id !== approvalId
      || !approvalActions(pending).includes(decision === 'approved' ? 'run' : 'reject')) return;
    this.pendingApproval = { ...pending, decisionPending: true };
    this.cb.onApprovalRequired(this.pendingApproval);
    try {
      const item = await this.gw.decideApproval(approvalId, decision);
      // 终态回执或下一条请求可能先于 HTTP 响应到达，不能重新创建旧审批。
      if (this.pendingApproval?.id !== approvalId) return;
      this.pendingApproval = { ...this.pendingApproval, decisionPending: false };
      if (item?.approval_id === approvalId) this.updateApproval(item);
      else this.cb.onApprovalRequired(this.pendingApproval);
    } catch (err) {
      if (this.pendingApproval?.id === approvalId) {
        this.pendingApproval = { ...this.pendingApproval, decisionPending: false };
        this.cb.onApprovalRequired(this.pendingApproval);
      }
      this.cb.onSystemMessage(`${this.cb.tr('runtime.decideFailed')}${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** 加载既有对话（/session Enter）：清空时间线 → 拉历史 → 按序渲染 user/assistant 文本。
   *  历史中的工具/思考块不重建（保持轻量）；切换后发送消息即挂到该对话。 */
  async loadSession(conversationId: string): Promise<void> {
    this.conversationId = conversationId;
    this.taskId = '';
    this.offset = 0;
    if (this.pendingApproval) this.cb.onApprovalResolved(this.pendingApproval.id);
    this.pendingApproval = null;
    this.resolvedApprovalId = '';
    this.cb.api.reset();
    try {
      const res = await this.gw.getSessionHistory(conversationId);
      const messages = Array.isArray(res?.messages) ? res.messages : Array.isArray(res) ? res : [];
      for (const m of messages) {
        const role = m?.role;
        const text = extractText(m?.content);
        if (!text) continue;
        if (role === 'user') {
          this.cb.api.addUser(text);
        } else if (role === 'assistant') {
          this.cb.api.startAssistant();
          this.cb.api.appendAssistant(text);
        }
      }
      this.cb.onSystemMessage(`${this.cb.tr('session.loaded')}${conversationId}`);
    } catch (err) {
      this.cb.onSystemMessage(`${this.cb.tr('session.loadFailed')}${err instanceof Error ? err.message : String(err)}`);
    }
  }

  /** 闲时拉一次会话 token 统计（/context 面板打开时调用；失败静默，面板保留上次值） */
  async refreshTokenStats(): Promise<void> {
    if (!this.conversationId) return;
    try {
      const stats = await this.gw.getTokenStats(this.conversationId);
      this.cb.onTokenUpdate?.({
        currentTokens: Number(stats.current_context_tokens ?? 0),
        totalInput: Number(stats.total_input_tokens ?? 0),
        totalOutput: Number(stats.total_output_tokens ?? 0),
        cacheInput: Number(stats.total_cached_input_tokens ?? 0),
        cacheExemptInput: Number(stats.cache_exempt_input_tokens ?? 0),
      });
    } catch {
      // 静默：新对话无统计属正常
    }
  }

  private setRunning(v: boolean): void {
    if (this.running === v) return;
    this.running = v;
    this.cb.onRunningChange(v);
  }

  private async pollLoop(): Promise<void> {
    if (this.polling) return;
    this.polling = true;
    let emptyStreak = 0;
    try {
      while (!this.destroyed && this.running) {
        let res: TaskPollResult;
        try {
          res = await this.gw.pollTask(this.taskId, this.offset);
        } catch (err) {
          this.cb.onSystemMessage(`${this.cb.tr('runtime.pollFailed')}${err instanceof Error ? err.message : String(err)}`);
          break;
        }
        // 事件窗口缺口检测（协议 §5.2）：中间事件已被裁剪，提示并对齐窗口续读
        if (typeof res.window_start === 'number' && this.offset < res.window_start) {
          this.cb.onSystemMessage(this.cb.tr('runtime.windowGap'));
          this.offset = res.window_start;
        }
        for (const ev of res.events ?? []) this.handleEvent(ev.type, ev.data ?? {});
        this.offset = Math.max(this.offset, res.next_offset ?? this.offset);
        if (isTerminalStatus(res.status)) {
          this.setRunning(false);
          break;
        }
        emptyStreak = (res.events?.length ?? 0) > 0 ? 0 : emptyStreak + 1;
        const wait = Math.min(POLL_INTERVAL_MS * (1 + emptyStreak * 0.5), POLL_MAX_BACKOFF_MS);
        await new Promise((r) => setTimeout(r, wait));
      }
    } finally {
      this.polling = false;
      this.setRunning(false);
    }
  }

  /** 事件 → 时间线（字段形状对齐旧版 eventMapper.ts 实测） */
  private handleEvent(type: string, data: any): void {
    const { api } = this.cb;
    switch (type) {
      case 'thinking_start':
        api.startThinking();
        break;
      case 'thinking_chunk':
        api.appendThinking(String(data.content ?? ''));
        break;
      case 'thinking_end':
        api.endThinking();
        break;
      case 'text_start':
      case 'ai_message_start':
        api.startAssistant();
        break;
      case 'text_chunk':
        api.appendAssistant(String(data.content ?? ''));
        break;
      case 'tool_preparing':
      case 'tool_start': {
        const name = String(data.name ?? 'tool');
        api.startTool({ title: this.toolLabel(name), params: toolParamLines(name, data.arguments) });
        break;
      }
      case 'tool_update_action':
      case 'update_action': {
        const status = String(data.status ?? '').toLowerCase();
        if (status === 'completed' || status === 'done' || status === 'success') {
          api.finishTool(toolResultSummary(data.result));
        } else if (status === 'failed' || status === 'error') {
          api.failTool(toolResultSummary(data.result));
        }
        break;
      }
      case 'tool_approval_required':
        this.updateApproval(data.approval ?? data);
        break;
      case 'auto_approval_progress': {
        const pending = this.pendingApproval;
        if (!pending || pending.id !== String(data.approval_id ?? '') || !data.progress) break;
        const progress = data.progress as AutoReviewProgress;
        // 普通自动审核保留人工接管语义；双重审批的自动项只能来自条目字段。
        let autoReviewStatus = pending.autoReviewStatus;
        if (pending.approvalType !== 'full_access' && progress.stage !== 'done'
          && autoReviewStatus !== 'approved' && autoReviewStatus !== 'rejected') {
          autoReviewStatus = 'reviewing';
        }
        // 普通审核的 done.decision 也可能来自人工接管，不能冒充自动审核结论。
        this.pendingApproval = { ...pending, autoReviewProgress: progress, autoReviewStatus };
        this.cb.onApprovalRequired(this.pendingApproval);
        break;
      }
      case 'tool_approval_resolved': {
        const item = data.approval ?? data.item ?? data;
        const id = String(item.approval_id ?? data.approval_id ?? '');
        const decision = String(item.decision ?? data.decision ?? item.status ?? '');
        // pending 回执不是最终授权，不清除面板。
        if (!id || !['approved', 'rejected', 'expired', 'cancelled', 'canceled', 'stopped', 'timeout'].includes(decision)) break;
        if (this.pendingApproval?.id !== id) break;
        const pending = this.pendingApproval;
        const reason = String(item.reason ?? data.reason ?? pending.reason ?? '').trim();
        const key = decision === 'approved' ? 'approval.approved'
          : decision === 'rejected' ? 'approval.rejected' : 'approval.expired';
        this.pendingApproval = null;
        this.resolvedApprovalId = id;
        this.cb.onApprovalResolved(id);
        this.cb.onSystemMessage(`${this.cb.tr(key)}${pending.toolLabel}${reason ? `\n${reason}` : ''}`);
        break;
      }
      case 'task_stopped':
        api.addSystem(this.cb.tr('runtime.stopped'));
        break;
      case 'token_update':
        // 运行期 token 统计推送（任务期间 context_manager 回调切入事件流）
        this.cb.onTokenUpdate?.({
          currentTokens: Number(data.current_context_tokens ?? 0),
          totalInput: Number(data.cumulative_input_tokens ?? 0),
          totalOutput: Number(data.cumulative_output_tokens ?? 0),
          cacheInput: Number(data.cumulative_cached_input_tokens ?? 0),
          cacheExemptInput: Number(data.cache_exempt_input_tokens ?? 0),
        });
        break;
      case 'error':
        api.addSystem(`${this.cb.tr('runtime.error')}${String(data.message ?? data.error ?? '')}`);
        break;
      case 'system_message':
        if (data.message) api.addSystem(String(data.message));
        break;
      default:
        break;
    }
  }

  private updateApproval(item: ToolApprovalItem): void {
    const id = String(item.approval_id ?? '');
    if (!id || id === this.resolvedApprovalId) return;
    const previous = this.pendingApproval?.id === id ? this.pendingApproval : null;
    const state = mergeApprovalState(item, previous);
    const toolName = item.tool_name ?? previous?.toolName ?? '';
    this.pendingApproval = {
      id,
      toolName,
      toolLabel: this.toolLabel(toolName),
      previewTitle: this.cb.tr('approval.params'),
      previewLines: item.arguments === undefined ? previous?.previewLines ?? []
        : toolParamLines(toolName, approvalArguments(item.arguments, state.approvalType)),
      ...state,
    };
    this.cb.onApprovalRequired(this.pendingApproval);
  }

  private toolLabel(toolName: string): string {
    const key = TOOL_LABEL_KEYS[toolName];
    return key ? this.cb.tr(key) : toolName;
  }
}

function isTerminalStatus(status: string): boolean {
  return ['succeeded', 'failed', 'stopped', 'canceled', 'completed', 'done', 'error'].includes((status || '').toLowerCase());
}

/** 历史消息 content → 纯文本（string 直返；多段数组拼接 text 段；其余空） */
function extractText(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((seg) => (typeof seg === 'string' ? seg : typeof seg?.text === 'string' ? seg.text : ''))
      .filter(Boolean)
      .join('\n');
  }
  return '';
}

/** 工具参数逐行展示（审批/工具块共用）：每个参数一行，多行值展开原样 */
function toolParamLines(toolName: string, args: any): string[] {
  if (!args || typeof args !== 'object') return [];
  const lines: string[] = [];
  for (const [key, value] of Object.entries(args)) {
    const text = typeof value === 'string' ? value : JSON.stringify(value);
    const parts = String(text).split('\n');
    lines.push(`${key}: ${parts[0] ?? ''}`);
    for (let i = 1; i < parts.length; i++) lines.push(`  ${parts[i]}`);
  }
  return lines;
}

/** 工具结果摘要（update_action 的 result 字段形状不一，取首行文本） */
function toolResultSummary(result: any): string {
  if (result == null) return '';
  if (typeof result === 'string') return result.split('\n')[0] ?? '';
  if (typeof result === 'object') {
    const cand = (result as any).summary ?? (result as any).message ?? (result as any).output;
    if (typeof cand === 'string') return cand.split('\n')[0] ?? '';
  }
  return '';
}
