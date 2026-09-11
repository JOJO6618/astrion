// 会话运行时：发消息 → 任务事件轮询 → 时间线渲染；审批事件自动弹出
// 协议依据 docs/runtime_protocol.md：run.start(POST /api/tasks) → run.events(GET ?from=offset)；
// 事件窗口缺口（offset < window_start）按 §5.2 提示并对齐续读（CLI 不做快照对账）。
import type { GatewayClient, TaskPollResult } from './gateway';
import type { PendingApprovalMock } from './data';
import { CPS_INSTANT, type TimelineApi } from './timeline';

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
  /** 审批事件：自动弹出审批面板 */
  onApprovalRequired(approval: PendingApprovalMock): void;
  /** 系统消息（审批回执/错误/窗口缺口提示等），经 menuState 的 sys 通道上屏 */
  onSystemMessage(text: string): void;
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

  constructor(
    private readonly gw: GatewayClient,
    private readonly cb: RuntimeCallbacks,
  ) {}

  destroy(): void {
    this.destroyed = true;
  }

  /** 发送一条用户消息并启动事件轮询（空闲时调用；运行中走排队由上层管理） */
  async send(text: string): Promise<void> {
    this.cb.api.addUser(text);
    let accepted: { task_id: string; conversation_id?: string };
    try {
      accepted = await this.gw.createTask({ message: text, conversation_id: this.conversationId || undefined });
    } catch (err) {
      this.cb.onSystemMessage(`${this.cb.tr('runtime.sendFailed')}${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    this.taskId = accepted.task_id;
    if (accepted.conversation_id) this.conversationId = accepted.conversation_id;
    this.offset = 0;
    this.setRunning(true);
    void this.pollLoop();
  }

  /** 审批裁决（审批面板 Enter 后调用） */
  async decideApproval(approvalId: string, decision: 'approved' | 'rejected'): Promise<void> {
    try {
      await this.gw.decideApproval(approvalId, decision);
    } catch (err) {
      this.cb.onSystemMessage(`${this.cb.tr('runtime.decideFailed')}${err instanceof Error ? err.message : String(err)}`);
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
      case 'tool_approval_required': {
        const a = data.approval ?? data;
        this.cb.onApprovalRequired({
          id: String(a.approval_id ?? ''),
          toolName: String(a.tool_name ?? ''),
          toolLabel: this.toolLabel(String(a.tool_name ?? '')),
          previewTitle: this.cb.tr('approval.params'),
          previewLines: toolParamLines(String(a.tool_name ?? ''), a.arguments),
        });
        break;
      }
      case 'tool_approval_resolved':
        // 本端/他端裁决回执：面板由 onSystemMessage 链路收尾（其他端裁决时也需关闭面板，后续接 approval.list 对账）
        break;
      case 'task_stopped':
        api.addSystem(this.cb.tr('runtime.stopped'));
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

  private toolLabel(toolName: string): string {
    const key = TOOL_LABEL_KEYS[toolName];
    return key ? this.cb.tr(key) : toolName;
  }
}

function isTerminalStatus(status: string): boolean {
  return ['succeeded', 'failed', 'stopped', 'canceled', 'completed', 'done', 'error'].includes((status || '').toLowerCase());
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
