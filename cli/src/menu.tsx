// / 菜单分发层：SlashMenu 按 stack 顶面板分发到各面板组件
// 交互区 = 输入栏上方的菜单展示区域（就地展开，非模态）
// 视觉规范：默认前景色；标题/提示行 DIM；选中行整行 INVERSE（反色，亮暗背景自适应）；
//           列对齐按显示宽度（中西文混排不歪）；靠布局与位置区分信息，不用 · 分隔
import { RGBA, TextAttributes } from '@opentui/core';
import { blinkDot, T } from './components';
import { filterCommands, type PanelKind } from './commands';
import { centeredWindowStart, SelectorList, MENU_VISIBLE_ROWS } from './selector';
import { PanelFrame, Line } from './panels/frame';
import { SessionPanel } from './panels/session';
import { ModelPanel, MODE_ITEMS, type ModelStep } from './panels/model';
import { PathsPanel } from './panels/paths';
import { ContextPanel } from './panels/context';
import { InputPanel, ConfirmPanel } from './panels/input';
import { ApprovalsPanel } from './panels/approvals';
import { WorkflowPanel } from './panels/workflow';
import { RewindPanel } from './panels/rewind';
import { HelpPanel, helpMaxOffset } from './panels/help';
import { padEndWidth } from './width';
import {
  BOUNDARY_PANELS,
  MODEL_OPTIONS,
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

const FG = RGBA.defaultForeground();
const DIM = TextAttributes.DIM;
const RED = '#e06c75';

const CMD_COL_WIDTH = 16;
const NAME_COL_WIDTH = 18;

export type BoundaryKind = 'mode' | 'permission' | 'env' | 'network';

export interface SlashMenuProps {
  stack: PanelKind[];
  query: string;
  sel: number;
  elapsed: number;
  width: number;
  // /model
  model: string;
  pendingModel: string;
  modelStep: ModelStep;
  thinking: boolean;
  effort: EffortLevel;
  // 边界当前生效值（value，如 'ask' / 'unrestricted'）
  boundary: Record<BoundaryKind, string>;
  // /path 当前显示的分组
  pathGroup: PathAccess;
  // 数据
  agents: AgentMock[];
  sessions: SessionMock[];
  tasks: TaskMock[];
  pathAuths: PathAuth[];
  pendingApproval: PendingApprovalMock | null;
  activeWorkflow: string | null;
  /** 上下文统计（/context 面板；token_update 事件与打开时查询双源刷新） */
  contextStats: ContextStats;
  /** 工作流库（/workflow；打开时经 API 刷新） */
  workflows: WorkflowMock[];
  /** 版本回溯检查点（/rewind；打开时经 API 刷新） */
  checkpoints: CheckpointMock[];
  /** 当前对话标题（/rename /delete /export 面板显示用） */
  currentSessionTitle: string;
}

function CommandsPanel({ query, sel, width }: { query: string; sel: number; width: number }) {
  // 平铺显示，无分组头（顺序仍按 commands.ts 中的分组排列）
  const filtered = filterCommands(query);
  // 居中窗口（Web 端同款）：高亮移动到中间（第 5 行）后固定，再往下列表滚动
  const start = centeredWindowStart(filtered.length, sel);
  const visible = filtered.slice(start, start + MENU_VISIBLE_ROWS);

  if (filtered.length === 0) {
    return (
      <PanelFrame title={`/ 命令   无匹配「${query}」`} width={width}>
        <T fg={FG} attributes={DIM}>{'  没有匹配的命令'}</T>
      </PanelFrame>
    );
  }
  return (
    <PanelFrame title="/ 命令   ↑↓ 选择   Enter 执行   Esc 关闭" width={width}>
      {visible.map((cmd, i) => (
        <Line
          key={cmd.name}
          width={width}
          selected={start + i === sel}
          text={`  /${padEndWidth(cmd.name, CMD_COL_WIDTH)}${cmd.desc}${cmd.hint ? `  ${cmd.hint}` : ''}`}
        />
      ))}
    </PanelFrame>
  );
}

/** /mode /permission /env /network 共用的上下选择器面板 */
function BoundaryPanel({ kind, sel, width, current }: { kind: BoundaryKind; sel: number; width: number; current: string }) {
  const group = BOUNDARY_PANELS[kind];
  return (
    <PanelFrame title={`${group.title}   ↑↓ 选择   Enter 保存   Esc 返回`} width={width}>
      <SelectorList items={group.options} sel={sel} current={current} width={width} />
    </PanelFrame>
  );
}

function AgentsPanel({ sel, width, agents, elapsed }: { sel: number; width: number; agents: AgentMock[]; elapsed: number }) {
  return (
    <PanelFrame title="子智能体   Enter 停止运行中实例   Esc 返回" width={width}>
      {agents.map((a, i) => {
        const dot =
          a.status === 'running' ? blinkDot(elapsed) : a.status === 'error' ? '•' : a.status === 'idle' ? '•' : '◦';
        const statusText =
          a.status === 'running' ? `运行中 ${a.elapsed}` : a.status === 'idle' ? '空闲' : a.status === 'error' ? '出错' : '已停止';
        const line = ` ${dot} ${padEndWidth(a.name, NAME_COL_WIDTH)}${a.task}   ${statusText}`;
        if (a.status === 'error' && i !== sel) {
          return <T key={a.name} fg={RED}>{line}</T>;
        }
        return <Line key={a.name} width={width} selected={i === sel} text={line} dim={a.status === 'terminated'} />;
      })}
    </PanelFrame>
  );
}

function TasksPanel({ sel, width, tasks, elapsed }: { sel: number; width: number; tasks: TaskMock[]; elapsed: number }) {
  return (
    <PanelFrame title="后台指令   Enter 停止运行中指令   Esc 返回" width={width}>
      {tasks.map((t, i) => {
        const dot = t.status === 'running' ? blinkDot(elapsed) : '•';
        const statusText = t.status === 'running' ? '运行中' : t.status === 'done' ? '已完成' : '出错';
        const line = ` ${dot} ${padEndWidth(t.command, NAME_COL_WIDTH)}${statusText}   ${t.lastLine}`;
        if (t.status === 'error' && i !== sel) {
          return <T key={t.id} fg={RED}>{line}</T>;
        }
        return <Line key={t.id} width={width} selected={i === sel} text={line} />;
      })}
    </PanelFrame>
  );
}

export function SlashMenu(props: SlashMenuProps) {
  const top = props.stack[props.stack.length - 1];
  switch (top) {
    case 'session':
      return <SessionPanel sel={props.sel} width={props.width} sessions={props.sessions} />;
    case 'model':
      return (
        <ModelPanel
          step={props.modelStep}
          sel={props.sel}
          width={props.width}
          model={props.model}
          pendingModel={props.pendingModel}
          thinking={props.thinking}
          effort={props.effort}
        />
      );
    case 'mode':
    case 'permission':
    case 'env':
    case 'network':
      return <BoundaryPanel kind={top} sel={props.sel} width={props.width} current={props.boundary[top]} />;
    case 'path':
      return <PathsPanel sel={props.sel} width={props.width} pathAuths={props.pathAuths} group={props.pathGroup} />;
    case 'context':
      return <ContextPanel width={props.width} stats={props.contextStats} model={props.model} />;
    case 'rename':
      return <InputPanel title="重命名对话" hint={`当前  ${props.currentSessionTitle}`} width={props.width} />;
    case 'export':
      return <InputPanel title="导出对话" hint="将当前对话导出为 Markdown 文本文件" width={props.width} />;
    case 'delete':
      return (
        <ConfirmPanel title="删除对话" hint={`将删除当前对话「${props.currentSessionTitle}」，此操作不可撤销`} width={props.width} />
      );
    case 'approvals':
      return <ApprovalsPanel sel={props.sel} width={props.width} pending={props.pendingApproval} />;
    case 'workflow':
      return <WorkflowPanel sel={props.sel} width={props.width} workflows={props.workflows} active={props.activeWorkflow} />;
    case 'rewind':
      return <RewindPanel sel={props.sel} width={props.width} checkpoints={props.checkpoints} />;
    case 'help':
      return <HelpPanel sel={props.sel} width={props.width} />;
    case 'agents':
      return <AgentsPanel sel={props.sel} width={props.width} agents={props.agents} elapsed={props.elapsed} />;
    case 'tasks':
      return <TasksPanel sel={props.sel} width={props.width} tasks={props.tasks} elapsed={props.elapsed} />;
    default:
      return <CommandsPanel query={props.query} sel={props.sel} width={props.width} />;
  }
}

/** 当前面板可选中项数量（供键盘循环计算）；0 = 该面板无选择概念 */
export function panelItemCount(props: SlashMenuProps): number {
  const top = props.stack[props.stack.length - 1];
  switch (top) {
    case 'session':
      return props.sessions.length;
    case 'model':
      return props.modelStep === 'model' ? MODEL_OPTIONS.length : MODE_ITEMS.length;
    case 'mode':
    case 'permission':
    case 'env':
    case 'network':
      return BOUNDARY_PANELS[top].options.length;
    case 'path':
      return props.pathAuths.filter((p) => p.access === props.pathGroup).length;
    case 'agents':
      return props.agents.length;
    case 'tasks':
      return props.tasks.length;
    case 'approvals':
      return 0; // 单条待审批，无 ↑↓ 列表；←→ 选择操作
    case 'workflow':
      return props.workflows.length;
    case 'rewind':
      return props.checkpoints.length;
    case 'help':
      return helpMaxOffset() + 1; // sel 在此面板作为滚动偏移
    case 'context':
      return 0;
    default:
      return filterCommands(props.query).length;
  }
}
