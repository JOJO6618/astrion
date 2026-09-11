// / 菜单状态机（交互区）：菜单开关、/ 激活检测、键盘分发、各面板选择与应用
// 从 main.tsx 抽出，保持入口文件精简；渲染分发在 menu.tsx
//
// / 激活规则（对齐 Web 端 findSlashToken）：/ 位于行首，或前一个字符是空格/换行时激活；
// query 不含空格，token 中混入空格或删掉 / 则关闭菜单。
import { useRef, useState } from 'react';
import type { TextareaRenderable } from '@opentui/core';
import { filterCommands, type CommandDef, type PanelKind } from './commands';
import { panelItemCount, type BoundaryKind, type SlashMenuProps } from './menu';
import type { ModelStep } from './panels/model';
import { APPROVAL_ACTIONS } from './panels/approvals';
import {
  BOOT_STATE,
  BOUNDARY_PANELS,
  EFFORT_LEVELS,
  EFFORT_META,
  EMPTY_CONTEXT_STATS,
  INITIAL_AGENTS,
  INITIAL_PATH_AUTHS,
  INITIAL_TASKS,
  MODEL_OPTIONS,
  PATH_ACCESS_LABEL,
  SESSIONS,
  formatContextUsage,
  type AgentMock,
  type CheckpointMock,
  type ContextStats,
  type EffortLevel,
  type PathAccess,
  type PathAuth,
  type PendingApprovalMock,
  type SessionMock,
  type TaskMock,
  type WorkflowMock,
} from './data';
import type { TimelineApi } from './timeline';

/** opentui 键盘事件的最小结构（结构化类型，避免依赖具体导出） */
export interface MenuKey {
  name?: string;
  ctrl?: boolean;
  preventDefault: () => void;
}

/** Web 端 findSlashToken 同款：/ 在行首或空格/换行后，query 不含空白与 / */
const SLASH_TOKEN_RE = /(^|[ \n])\/([^\s/]*)$/;

export interface StatusFields {
  model: string;
  thinking: boolean;
  effort: EffortLevel;
  workMode: string;
  permMode: string;
  execEnv: string;
  contextUsage: string;
}

export function useSlashMenu({
  textareaRef,
  apiRef,
  destroy,
  width,
  elapsed,
  onApprovalAction,
  onSessionLoad,
  onContextRefresh,
  onSaveModelDefaults,
  onSavePathAuths,
  onAgentsRefresh,
  onTasksRefresh,
  onWorkflowsRefresh,
  onCheckpointsRefresh,
  onNewSession,
}: {
  textareaRef: React.RefObject<TextareaRenderable | null>;
  apiRef: React.RefObject<TimelineApi | null>;
  destroy: () => void;
  width: number;
  elapsed: number;
  /** 审批裁决回调（正式版由 runtime 注入，调 Gateway decision 端点）；缺省只上屏系统消息 */
  onApprovalAction?: (action: 'run' | 'reject' | 'unrestricted', approval: PendingApprovalMock) => void;
  /** /session Enter：加载选中对话（runtime.loadSession；缺省只上屏系统消息） */
  onSessionLoad?: (session: SessionMock) => void;
  /** /context 打开：触发一次 token 统计查询（gw.getTokenStats；结果经 setContextStats 回流） */
  onContextRefresh?: () => void;
  /** /model Enter：保存为默认（gw.savePersonalization patch）；失败由注入方上屏提示 */
  onSaveModelDefaults?: (v: { model: string; thinking: boolean; effort: EffortLevel }) => void;
  /** /path 增删后：全量保存两组授权（gw.savePathAuths）；失败由注入方上屏提示 */
  onSavePathAuths?: (auths: PathAuth[]) => void;
  /** /agents /tasks 打开：拉真实列表（gw.listSubAgents/listBackgroundCommands，结果经 setter 回流） */
  onAgentsRefresh?: () => void;
  onTasksRefresh?: () => void;
  /** /workflow /rewind 打开：拉真实列表（gw.listWorkflows/listCheckpoints，结果经 setter 回流） */
  onWorkflowsRefresh?: () => void;
  onCheckpointsRefresh?: () => void;
  /** /new：进入空对话草稿（runtime.enterNewSession；对齐 web /new 页面语义） */
  onNewSession?: () => void;
}) {
  // / 菜单：menuStack=null 关闭；['commands'] 命令层；级联 push（如 ['commands','model']）
  const [menuStack, setMenuStack] = useState<PanelKind[] | null>(null);
  const [query, setQuery] = useState('');
  const [sel, setSel] = useState(0);
  // /model 两级流程（初值 = boot 加载的真实快照 BOOT_STATE）
  const [model, setModel] = useState(BOOT_STATE.model);
  const [pendingModel, setPendingModel] = useState(BOOT_STATE.model);
  const [modelStep, setModelStep] = useState<ModelStep>('model');
  const [thinking, setThinking] = useState(BOOT_STATE.thinking);
  const [effort, setEffort] = useState<EffortLevel>(BOOT_STATE.effort);
  // 面板内编辑中的强度（Enter 才提交到 effort；Esc 放弃不影响已生效值）
  const [pendingEffort, setPendingEffort] = useState<EffortLevel>(BOOT_STATE.effort);
  // 边界（value 与 BOUNDARY_PANELS 对齐）
  const [workMode, setWorkMode] = useState(BOOT_STATE.workMode);
  const [permMode, setPermMode] = useState(BOOT_STATE.permMode);
  const [execEnv, setExecEnv] = useState(BOOT_STATE.execEnv);
  const [network, setNetwork] = useState(BOOT_STATE.network);
  // 路径授权（pathGroup = 当前显示的分组；←→ 切换显示组，不做行内权限切换）
  const [pathAuths, setPathAuths] = useState<PathAuth[]>(INITIAL_PATH_AUTHS.map((p) => ({ ...p })));
  const [pathGroup, setPathGroup] = useState<PathAccess>('rw');
  // 子智能体 / 后台指令（演示用可变状态）
  const [agents, setAgents] = useState<AgentMock[]>(INITIAL_AGENTS.map((a) => ({ ...a })));
  const [tasks, setTasks] = useState<TaskMock[]>(INITIAL_TASKS.map((t) => ({ ...t })));
  // 对话列表（rename/delete 可变）、待审批（同一时刻最多一条，runtime 审批事件注入）、工作流激活态
  const [sessions, setSessions] = useState<SessionMock[]>(SESSIONS.map((s) => ({ ...s })));
  const [pendingApproval, setPendingApproval] = useState<PendingApprovalMock | null>(null);
  const [activeWorkflow, setActiveWorkflow] = useState<string | null>(null);
  // 上下文统计（/context 面板 + 状态栏；token_update 事件与打开时查询双源刷新）
  const [contextStats, setContextStats] = useState<ContextStats>({ ...EMPTY_CONTEXT_STATS });
  // 工作流库与检查点（/workflow /rewind；打开时经 API 刷新）
  const [workflows, setWorkflows] = useState<WorkflowMock[]>([]);
  const [checkpoints, setCheckpoints] = useState<CheckpointMock[]>([]);

  // useKeyboard 回调只注册一次，全部通过 ref 读最新状态，避免过期闭包
  const stateRef = useRef({
    menuStack, query, sel, model, pendingModel, modelStep, thinking, effort, pendingEffort,
    workMode, permMode, execEnv, network, pathAuths, pathGroup, agents, tasks,
    sessions, pendingApproval, activeWorkflow, contextStats, workflows, checkpoints,
  });
  stateRef.current = {
    menuStack, query, sel, model, pendingModel, modelStep, thinking, effort, pendingEffort,
    workMode, permMode, execEnv, network, pathAuths, pathGroup, agents, tasks,
    sessions, pendingApproval, activeWorkflow, contextStats, workflows, checkpoints,
  };

  const currentSessionTitle = sessions.find((s) => s.current)?.title ?? '';

  const slashMenuProps: SlashMenuProps | null = menuStack
    ? {
        stack: menuStack,
        query,
        sel,
        elapsed,
        width,
        model,
        pendingModel,
        modelStep,
        thinking,
        effort: pendingEffort, // 面板显示编辑中的值；已生效值在 statusFields
        boundary: { mode: workMode, permission: permMode, env: execEnv, network },
        pathGroup,
        agents,
        sessions,
        tasks,
        pathAuths,
        pendingApproval,
        activeWorkflow,
        contextStats,
        workflows,
        checkpoints,
        currentSessionTitle,
      }
    : null;

  const currentItemCount = () =>
    panelItemCount({
      stack: stateRef.current.menuStack!,
      query: stateRef.current.query,
      sel: stateRef.current.sel,
      elapsed: 0,
      width,
      model: stateRef.current.model,
      pendingModel: stateRef.current.pendingModel,
      modelStep: stateRef.current.modelStep,
      thinking: stateRef.current.thinking,
      effort: stateRef.current.effort,
      boundary: {
        mode: stateRef.current.workMode,
        permission: stateRef.current.permMode,
        env: stateRef.current.execEnv,
        network: stateRef.current.network,
      },
      pathGroup: stateRef.current.pathGroup,
      agents: stateRef.current.agents,
      sessions: stateRef.current.sessions,
      tasks: stateRef.current.tasks,
      pathAuths: stateRef.current.pathAuths,
      pendingApproval: stateRef.current.pendingApproval,
      activeWorkflow: stateRef.current.activeWorkflow,
      contextStats: stateRef.current.contextStats,
      workflows: stateRef.current.workflows,
      checkpoints: stateRef.current.checkpoints,
      currentSessionTitle: stateRef.current.sessions.find((s) => s.current)?.title ?? '',
    } satisfies SlashMenuProps);

  // 命令层进度记忆：进入级联面板时保存（query+sel），Esc 返回命令层时恢复（继承进入时的位置）
  const commandLayerRef = useRef<{ query: string; sel: number } | null>(null);

  // 程序性 setText 会同步触发 onContentChange（实测），而此时 stateRef 尚未同步新 stack，
  // 会被 / token 检测误判为「用户删光了 /」把刚打开的级联面板关掉。
  // 所有菜单内部的 setText 必须走 setInputText 带抑制标记。
  const suppressChangeRef = useRef(false);
  const setInputText = (text: string) => {
    suppressChangeRef.current = true;
    textareaRef.current?.setText(text);
  };

  // clearText=true 时才清空输入框：backspace 删光文本触发的 closeMenu 绝不能 setText
  // （onContentChange 回调里同步 setText 会与正在进行的删除操作重入，导致 buffer 状态错乱）
  const closeMenu = (opts?: { clearText?: boolean }) => {
    setMenuStack(null);
    setQuery('');
    setSel(0);
    commandLayerRef.current = null;
    if (opts?.clearText) setInputText('');
  };

  const sys = (text: string) => apiRef.current?.addSystem(text);

  const execCommand = (cmd: CommandDef) => {
    switch (cmd.name) {
      case 'exit':
        destroy();
        return;
      case 'compact':
        sys('/compact  压缩上下文（CLI 尚未接入）');
        break;
      case 'new':
        // 对齐 web /new 页面：进入空对话草稿（不立即建会话），首条消息惰性创建
        onNewSession?.();
        break;
      default:
        sys(`/${cmd.name}  ${cmd.desc}（CLI 尚未接入）`);
    }
  };

  /** 进入级联面板时的选中项初始化（定位到当前生效值） */
  const enterPanel = (kind: PanelKind) => {
    const st = stateRef.current;
    // 保存命令层进度，Esc 返回时继承
    commandLayerRef.current = { query: st.query, sel: st.sel };
    setMenuStack([...(st.menuStack ?? []), kind]);
    setQuery('');
    setInputText('');
    switch (kind) {
      case 'model':
        setPendingModel(st.model);
        setModelStep('model');
        setPendingEffort(st.effort); // 编辑副本从已生效值开始
        setSel(Math.max(0, MODEL_OPTIONS.findIndex((m) => m.name === st.model)));
        break;
      case 'session':
        setSel(Math.max(0, st.sessions.findIndex((s) => s.current)));
        break;
      case 'context':
        // 打开时触发一次统计查询（结果经 setContextStats 回流；事件流为运行期另一源）
        onContextRefresh?.();
        break;
      case 'agents':
        onAgentsRefresh?.();
        break;
      case 'tasks':
        onTasksRefresh?.();
        break;
      case 'workflow':
        onWorkflowsRefresh?.();
        setSel(Math.max(0, st.workflows.findIndex((w) => w.name === st.activeWorkflow)));
        break;
      case 'rewind':
        onCheckpointsRefresh?.();
        setSel(0);
        break;
      case 'approvals':
        // 单条待审批：光标落在第一个操作「运行」上
        setSel(0);
        break;
      case 'mode':
      case 'permission':
      case 'env':
      case 'network': {
        const current = { mode: st.workMode, permission: st.permMode, env: st.execEnv, network: st.network }[kind];
        setSel(Math.max(0, BOUNDARY_PANELS[kind].options.findIndex((o) => o.value === current)));
        break;
      }
      default:
        setSel(0);
    }
  };

  const describeModel = (m: string, th: boolean, ef: EffortLevel) =>
    th ? `${m} 思考 ${EFFORT_META[ef].label}` : `${m} 快速`;

  const activateSelection = () => {
    const st = stateRef.current;
    if (!st.menuStack) return;
    const top = st.menuStack[st.menuStack.length - 1];

    if (top === 'commands') {
      const cmd = filterCommands(st.query)[st.sel];
      if (!cmd) return;
      if (cmd.action === 'panel' && cmd.panel) {
        enterPanel(cmd.panel);
      } else {
        execCommand(cmd);
        closeMenu({ clearText: true });
      }
      return;
    }

    if (top === 'session') {
      const s = st.sessions[st.sel];
      if (s && !s.current) {
        setSessions((prev) => prev.map((x) => ({ ...x, current: x.id === s.id })));
        onSessionLoad?.(s);
      }
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'rename') {
      // 输入栏有内容 = 新名称；空则不动
      const text = (textareaRef.current?.plainText ?? '').trim();
      if (text) {
        const before = st.sessions.find((s) => s.current)?.title;
        setSessions((prev) => prev.map((x) => (x.current ? { ...x, title: text } : x)));
        sys(`已重命名对话：「${before ?? ''}」→「${text}」`);
        closeMenu({ clearText: true });
      }
      return;
    }

    if (top === 'export') {
      // 输入栏有内容 = 导出路径；空则不动
      const text = (textareaRef.current?.plainText ?? '').trim();
      if (text) {
        sys(`已导出对话到 ${text}（CLI 尚未接入）`);
        closeMenu({ clearText: true });
      }
      return;
    }

    if (top === 'delete') {
      // 确认删除当前对话：从列表移除，切换到下一条（CLI 尚未接入）
      const idx = st.sessions.findIndex((s) => s.current);
      if (idx >= 0) {
        const title = st.sessions[idx]!.title;
        setSessions((prev) => {
          const next = prev.filter((_, i) => i !== idx);
          if (next.length > 0) next[Math.min(idx, next.length - 1)]!.current = true;
          return next;
        });
        sys(`已删除对话「${title}」（CLI 尚未接入）`);
      }
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'approvals') {
      // 对齐 web 端：同一时刻最多一条待审批；←→ 选操作，Enter 执行后关闭
      // 关闭时是否清空输入框：手动 /approvals 进入要清掉命令文本；审批事件自动弹出要保留用户草稿
      const a = st.pendingApproval;
      if (!a) {
        closeAfterApproval();
        return;
      }
      const action = st.sel === 0 ? 'run' : st.sel === 1 ? 'reject' : 'unrestricted';
      if (onApprovalAction) {
        onApprovalAction(action, a);
      } else if (action === 'run') {
        sys(`已批准并运行：${a.toolLabel}`);
      } else if (action === 'reject') {
        sys(`已拒绝：${a.toolLabel}`);
      } else {
        setPermMode('unrestricted');
        sys(`已切换到无限制模式并运行：${a.toolLabel}`);
      }
      setPendingApproval(null);
      closeAfterApproval();
      return;
    }

    if (top === 'workflow') {
      const w = st.workflows[st.sel];
      if (!w) return;
      if (st.activeWorkflow === w.name) {
        setActiveWorkflow(null);
        sys(`已退出工作流 ${w.name}`);
      } else {
        setActiveWorkflow(w.name);
        sys(`已激活工作流 ${w.name}（${w.desc}，${w.stages} 阶段；CLI 尚未接入）`);
      }
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'rewind') {
      const c = st.checkpoints[st.sel];
      if (c) sys(`已回溯到检查点「${c.summary}」（${c.when}；CLI 尚未接入）`);
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'help') {
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'model') {
      if (st.modelStep === 'model') {
        const m = MODEL_OPTIONS[st.sel];
        if (!m) return;
        // 进入第二级：快速/思考；sel 定位到当前生效模式；强度编辑副本重置为已生效值（Esc 放弃的修改不带入）
        setPendingModel(m.name);
        setModelStep('mode');
        setPendingEffort(st.effort);
        setSel(st.thinking ? 1 : 0);
        return;
      }
      // 第二级 Enter：保存 模型 + 模式 + 强度（编辑副本在此刻提交）
      const toThinking = st.sel === 1;
      const before = describeModel(st.model, st.thinking, st.effort);
      const after = describeModel(st.pendingModel, toThinking, st.pendingEffort);
      setModel(st.pendingModel);
      setThinking(toThinking);
      setEffort(st.pendingEffort);
      if (before !== after) sys(`已切换：${before} → ${after}`);
      // 同步保存为个性化默认（影响新对话；当前会话经 createTask 覆盖参数即时生效）
      onSaveModelDefaults?.({ model: st.pendingModel, thinking: toThinking, effort: st.pendingEffort });
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'mode' || top === 'permission' || top === 'env' || top === 'network') {
      const group = BOUNDARY_PANELS[top];
      const opt = group.options[st.sel];
      if (!opt) return;
      const current = { mode: st.workMode, permission: st.permMode, env: st.execEnv, network: st.network }[top];
      if (opt.value !== current) {
        const oldLabel = group.options.find((o) => o.value === current)?.label ?? current;
        if (top === 'mode') setWorkMode(opt.value);
        else if (top === 'permission') setPermMode(opt.value);
        else if (top === 'env') setExecEnv(opt.value);
        else setNetwork(opt.value);
        sys(`已切换${group.title}：${oldLabel} → ${opt.label}`);
      }
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'path') {
      // 输入栏有内容 = 添加新路径到当前显示的分组；空则不动
      const text = (textareaRef.current?.plainText ?? '').trim();
      if (text) {
        const access = st.pathGroup;
        const next = [...st.pathAuths, { path: text, access }];
        setPathAuths(next);
        setInputText('');
        setSel(st.pathAuths.filter((p) => p.access === access).length); // 选中新添加的行
        sys(`已添加路径授权：${text}（${PATH_ACCESS_LABEL[access]}）`);
        onSavePathAuths?.(next);
      }
      return;
    }

    if (top === 'context') {
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'agents') {
      const a = st.agents[st.sel];
      if (!a) return;
      if (a.status === 'running') {
        setAgents((prev) => prev.map((x) => (x.name === a.name ? { ...x, status: 'terminated' as const } : x)));
        sys(`已停止子智能体 ${a.name}`);
        return; // 面板保持打开，可继续管理
      }
      sys(`${a.name} 当前${a.status === 'idle' ? '空闲' : '已停止'}，无需操作`);
      closeMenu({ clearText: true });
      return;
    }

    if (top === 'tasks') {
      const t = st.tasks[st.sel];
      if (!t) return;
      if (t.status === 'running') {
        setTasks((prev) => prev.map((x) => (x.id === t.id ? { ...x, status: 'done' as const, lastLine: '已手动停止' } : x)));
        sys(`已停止后台指令 ${t.command}`);
        return;
      }
      closeMenu({ clearText: true });
    }
  };

  /** Esc / Ctrl+C：逐级返回（model 第二级 → 第一级 → 命令层 → 关闭） */
  /** 审批面板关闭：手动 /approvals 进入（栈含 commands 层）清掉命令文本；审批事件自动弹出（栈仅 approvals）保留用户草稿 */
  const closeAfterApproval = () => {
    const hasCommandLayer = (stateRef.current.menuStack ?? []).includes('commands');
    closeMenu({ clearText: hasCommandLayer });
  };

  /** 审批事件自动弹出（runtime 注入）：有待审批时把面板压栈（保留用户菜单/输入现场），已打开则只更新内容 */
  const openApproval = (a: PendingApprovalMock) => {
    setPendingApproval(a);
    const st = stateRef.current;
    if (!(st.menuStack ?? []).includes('approvals')) {
      setMenuStack([...(st.menuStack ?? []), 'approvals']);
    }
    setSel(0);
  };

  const escapeLevel = () => {
    const st = stateRef.current;
    if (!st.menuStack) return;
    const top = st.menuStack[st.menuStack.length - 1];
    if (top === 'model' && st.modelStep === 'mode') {
      setModelStep('model');
      setSel(Math.max(0, MODEL_OPTIONS.findIndex((m) => m.name === st.pendingModel)));
      return;
    }
    if (st.menuStack.length > 1) {
      setMenuStack(st.menuStack.slice(0, -1));
      // 恢复进入时的命令层进度（query+sel），没有则回到开头
      const saved = commandLayerRef.current;
      const q = saved?.query ?? '';
      setQuery(q);
      setSel(saved?.sel ?? 0);
      commandLayerRef.current = null;
      // 返回命令层时输入框恢复为 /query（写 '' 会被 onContentChange 当成“删光了斜杠”把菜单关掉）
      setInputText('/' + q);
      // setText 后光标落在文首，手动移到文本末尾
      textareaRef.current?.setCursor(0, 1 + q.length);
    } else {
      // 栈深 1：命令层 Esc=关闭并清掉 / 文本；自动弹出的审批层 Esc=仅关闭（保留输入框草稿）
      closeMenu({ clearText: st.menuStack[0] !== 'approvals' });
    }
  };

  /** 返回 true = 已消费该按键 */
  const handleKey = (key: MenuKey): boolean => {
    const st = stateRef.current;
    if (!st.menuStack) return false;
    const top = st.menuStack[st.menuStack.length - 1];

    if (key.name === 'up' || key.name === 'down') {
      key.preventDefault();
      const count = currentItemCount();
      if (count > 0) {
        setSel((s) => (s + (key.name === 'down' ? 1 : -1) + count) % count);
      }
      return true;
    }

    if (key.name === 'left' || key.name === 'right') {
      // /model 第二级停在「思考」：←→ 移动推理强度滑块（只改编辑副本，Enter 才生效）
      if (top === 'model' && st.modelStep === 'mode' && st.sel === 1) {
        key.preventDefault();
        const idx = EFFORT_LEVELS.indexOf(st.pendingEffort);
        const next = (idx + (key.name === 'right' ? 1 : -1) + EFFORT_LEVELS.length) % EFFORT_LEVELS.length;
        setPendingEffort(EFFORT_LEVELS[next]!);
        return true;
      }
      // /path：←→ 切换当前显示的分组
      if (top === 'path') {
        key.preventDefault();
        setPathGroup((g) => (g === 'rw' ? 'ro' : 'rw'));
        setSel(0);
        return true;
      }
      // /approvals：有待审批时 ←→ 在 运行/拒绝/切换到无限制 间移动
      if (top === 'approvals' && st.pendingApproval) {
        key.preventDefault();
        setSel((s) => (s + (key.name === 'right' ? 1 : -1) + APPROVAL_ACTIONS.length) % APPROVAL_ACTIONS.length);
        return true;
      }
    }

    if (key.name === 'backspace' || key.name === 'delete') {
      // /path：输入框为空时按删除键 = 删除当前组内选中行；有文字时放行正常删字符
      const groupRows = st.pathAuths.filter((p) => p.access === st.pathGroup);
      if (top === 'path' && groupRows.length > 0 && (textareaRef.current?.plainText ?? '') === '') {
        key.preventDefault();
        // sel 是组内下标，换算成全量数组下标再删
        const target = groupRows[st.sel];
        const next = st.pathAuths.filter((p) => p !== target);
        setPathAuths(next);
        setSel((s) => Math.max(0, Math.min(s, groupRows.length - 2)));
        onSavePathAuths?.(next);
        return true;
      }
      return false;
    }

    if (key.name === 'return') {
      key.preventDefault();
      activateSelection();
      return true;
    }

    if (key.name === 'escape' || (key.ctrl && key.name === 'c')) {
      key.preventDefault();
      escapeLevel();
      return true;
    }

    return false;
  };

  /** textarea 内容变化：/ token 检测（仅菜单关闭或命令层时；级联面板里输入路径等不触发） */
  const handleContentChange = () => {
    // 程序性 setText 的同步回调：跳过（见 setInputText 注释）
    if (suppressChangeRef.current) {
      suppressChangeRef.current = false;
      return;
    }
    const st = stateRef.current;
    const top = st.menuStack?.[st.menuStack.length - 1];
    if (st.menuStack && top !== 'commands') return;
    const text = textareaRef.current?.plainText ?? '';
    const match = SLASH_TOKEN_RE.exec(text);
    if (match) {
      if (!st.menuStack) {
        setMenuStack(['commands']);
        setSel(0);
      }
      const q = match[2] ?? '';
      if (q !== st.query) {
        setQuery(q);
        setSel(0);
      }
    } else if (st.menuStack) {
      // 用户把 / 删掉了或 token 混入空格：文本已是删除后状态，这里只关菜单，绝不能碰 buffer
      closeMenu();
    }
  };

  /** Ctrl+R 重播时复位演示状态 */
  const resetMenu = () => {
    if (stateRef.current.menuStack) closeMenu({ clearText: true });
    setAgents(INITIAL_AGENTS.map((a) => ({ ...a })));
    setTasks(INITIAL_TASKS.map((t) => ({ ...t })));
    setPathAuths(INITIAL_PATH_AUTHS.map((p) => ({ ...p })));
    setSessions(SESSIONS.map((s) => ({ ...s })));
    setPendingApproval(null);
    setActiveWorkflow(null);
  };

  const topKind = menuStack?.[menuStack.length - 1];
  /** 输入型面板打开时输入栏 placeholder 覆盖 */
  const inputPlaceholder =
    topKind === 'path'
      ? `输入路径 Enter 添加到「${PATH_ACCESS_LABEL[pathGroup]}」组   ←→ 切换分组   Esc 返回`
      : topKind === 'rename'
        ? '输入新名称   Enter 保存   Esc 返回'
        : topKind === 'export'
          ? '输入导出路径（如 ~/Desktop/chat.md）  Enter 导出   Esc 返回'
          : undefined;

  const statusFields: StatusFields = {
    model,
    thinking,
    effort,
    workMode: BOUNDARY_PANELS.mode.options.find((o) => o.value === workMode)?.label ?? workMode,
    permMode: BOUNDARY_PANELS.permission.options.find((o) => o.value === permMode)?.label ?? permMode,
    execEnv: BOUNDARY_PANELS.env.options.find((o) => o.value === execEnv)?.label ?? execEnv,
    contextUsage: formatContextUsage(contextStats, model),
  };

  return {
    slashMenuProps,
    statusFields,
    inputPlaceholder,
    handleContentChange,
    handleKey,
    closeMenu,
    openApproval,
    resetMenu,
    setContextStats,
    setAgents,
    setTasks,
    setWorkflows,
    setCheckpoints,
    setSessions,
  };
}
