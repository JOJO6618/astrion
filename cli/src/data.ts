// 运行时数据层（正式版替代 demo 的 mock.ts）：
//   静态定义（档位/边界/标签等）保留，文案经 i18n（模块加载时 OS locale 已就绪）；
//   动态数据（工作区/会话/模型/子智能体/后台指令/路径授权/上下文统计/检查点/工作流）
//   初始为空，由 boot 经 Gateway Bearer 通道加载后填充（见 boot.ts / gateway.ts）。
import { t } from './i18n';

// ── 推理强度（对齐 Web 端 ReasoningEffort：5 档 + 默认=null 不指定） ──
export type EffortLevel = 'default' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export const EFFORT_LEVELS: EffortLevel[] = ['default', 'low', 'medium', 'high', 'xhigh', 'max'];
export const EFFORT_META: Record<EffortLevel, { label: string; desc: string }> = {
  default: { label: t('effort.default.label'), desc: t('effort.default.desc') },
  low: { label: 'low', desc: t('effort.low.desc') },
  medium: { label: 'medium', desc: t('effort.medium.desc') },
  high: { label: 'high', desc: t('effort.high.desc') },
  xhigh: { label: 'xhigh', desc: t('effort.xhigh.desc') },
  max: { label: 'max', desc: t('effort.max.desc') },
};

// ── 启动状态快照（boot 加载后写入；menuState 的 useState 初值来源，App 渲染前已就绪） ──
export const BOOT_STATE = {
  model: '',
  thinking: false,
  effort: 'default' as EffortLevel,
  workMode: 'ask',
  permMode: 'unrestricted',
  execEnv: 'direct',
  network: 'restricted',
  contextUsage: '—',
};

// ── 工作区（/session 面板顶部展示；boot 确定后写入） ──
export interface WorkspaceInfo {
  id: string;
  name: string;
  path: string;
}
export const WORKSPACE: WorkspaceInfo = { id: '', name: '', path: '' };

// ── 模型（boot 从 /api/v1/models 加载后填充） ──
export interface ModelOption {
  name: string;
  meta: string;
}
export const MODEL_OPTIONS: ModelOption[] = [];

// ── 对话（/session 列表；boot 从 session.list 加载后填充） ──
export interface SessionMock {
  id: string;
  title: string;
  when: string;
  current?: boolean;
}
export const SESSIONS: SessionMock[] = [];

// ── 子智能体 / 后台指令（后续接 /api/sub_agents、/api/background_commands） ──
export interface AgentMock {
  name: string;
  task: string;
  status: 'running' | 'idle' | 'terminated' | 'error';
  elapsed: string;
}
export interface TaskMock {
  id: string;
  command: string;
  status: 'running' | 'done' | 'error';
  lastLine: string;
}
export const INITIAL_AGENTS: AgentMock[] = [];
export const INITIAL_TASKS: TaskMock[] = [];

// ── 路径授权（后续接 /api/path-authorization） ──
export type PathAccess = 'rw' | 'ro';
export interface PathAuth {
  path: string;
  access: PathAccess;
}
export const PATH_ACCESS_LABEL: Record<PathAccess, string> = { rw: t('path.access.rw'), ro: t('path.access.ro') };
export const INITIAL_PATH_AUTHS: PathAuth[] = [];

// ── 上下文统计（/context 面板；后续接会话 token 统计端点） ──
export interface ContextStats {
  used: string;
  total: string;
  percent: number;
  totalInput: string;
  totalOutput: string;
  cacheInput: string;
  cacheHitRate: string;
}
export const CONTEXT_STATS: ContextStats = {
  used: '—',
  total: '—',
  percent: 0,
  totalInput: '—',
  totalOutput: '—',
  cacheInput: '—',
  cacheHitRate: '—',
};

// ── 边界四选项（/mode /permission /env /network 共用 Selector 面板；切换后调对应 API） ──
export interface BoundaryOption {
  value: string;
  label: string;
  desc: string;
}
export interface BoundaryGroup {
  title: string;
  options: BoundaryOption[];
}
export const BOUNDARY_PANELS: Record<'mode' | 'permission' | 'env' | 'network', BoundaryGroup> = {
  mode: {
    title: t('boundary.mode.title'),
    options: [
      { value: 'plan', label: t('boundary.mode.plan.label'), desc: t('boundary.mode.plan.desc') },
      { value: 'ask', label: t('boundary.mode.ask.label'), desc: t('boundary.mode.ask.desc') },
      { value: 'execute', label: t('boundary.mode.execute.label'), desc: t('boundary.mode.execute.desc') },
    ],
  },
  permission: {
    title: t('boundary.permission.title'),
    options: [
      { value: 'readonly', label: t('boundary.permission.readonly.label'), desc: t('boundary.permission.readonly.desc') },
      { value: 'approval', label: t('boundary.permission.approval.label'), desc: t('boundary.permission.approval.desc') },
      { value: 'auto', label: t('boundary.permission.auto.label'), desc: t('boundary.permission.auto.desc') },
      { value: 'unrestricted', label: t('boundary.permission.unrestricted.label'), desc: t('boundary.permission.unrestricted.desc') },
    ],
  },
  env: {
    title: t('boundary.env.title'),
    options: [
      { value: 'sandbox', label: t('boundary.env.sandbox.label'), desc: t('boundary.env.sandbox.desc') },
      { value: 'direct', label: t('boundary.env.direct.label'), desc: t('boundary.env.direct.desc') },
    ],
  },
  network: {
    title: t('boundary.network.title'),
    options: [
      { value: 'restricted', label: t('boundary.network.restricted.label'), desc: t('boundary.network.restricted.desc') },
      { value: 'full', label: t('boundary.network.full.label'), desc: t('boundary.network.full.desc') },
    ],
  },
};

// ── 待审批（审批事件自动弹出 + /approvals 面板共用；runtime 收到 tool_approval_required 时填充） ──
export interface PendingApprovalMock {
  /** 审批条目 id（decision 端点用） */
  id: string;
  toolName: string;
  toolLabel: string;
  /** 参数标题（如 命令 / 文件） */
  previewTitle: string;
  /** 完整参数预览（多行原样显示，不截断） */
  previewLines: string[];
}

// ── 工作流（/workflow 面板；后续接工作流库 API） ──
export interface WorkflowMock {
  name: string;
  desc: string;
  stages: number;
}
export const WORKFLOWS: WorkflowMock[] = [];

// ── 版本回溯检查点（/rewind 面板；后续接 versioning API） ──
export interface CheckpointMock {
  id: string;
  when: string;
  summary: string;
  files: number;
}
export const CHECKPOINTS: CheckpointMock[] = [];

// ── 帮助（/help 面板；键位说明） ──
export const HELP_LINES: Array<[string, string]> = [
  ['/', t('help.slash')],
  ['Ctrl+G', t('help.guidance')],
  ['Ctrl+E', t('help.thinking')],
  ['', ''],
  ['↑↓', t('help.updown')],
  ['←→', t('help.leftright')],
  ['Enter', t('help.enter')],
  ['⌫', t('help.backspace')],
  ['Esc', t('help.esc')],
];
