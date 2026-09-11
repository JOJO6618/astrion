// / 命令注册表：数据驱动（参考 opencode palette entry schema）
// 每个命令 = 名称 / 别名 / 描述 / 分组 / 行为（exec=立即执行 | panel=级联面板，在交互区展开）

export type PanelKind =
  | 'commands'
  | 'session'
  | 'model'
  | 'mode'
  | 'permission'
  | 'env'
  | 'network'
  | 'path'
  | 'context'
  | 'agents'
  | 'tasks'
  | 'rename'
  | 'delete'
  | 'export'
  | 'approvals'
  | 'workflow'
  | 'rewind'
  | 'help';

export interface CommandDef {
  /** 命令名（不含 /） */
  name: string;
  /** 别名（过滤时也参与匹配） */
  aliases?: string[];
  /** 描述（面板中紧随命令名显示；状态信息直接拼在描述里） */
  desc: string;
  /** 分组名（面板分组头） */
  group: string;
  /** 参数提示，如 '[名称]'，仅展示用 */
  hint?: string;
  /** exec = 立即执行并关闭面板；panel = 进入级联面板 */
  action: 'exec' | 'panel';
  /** action=panel 时的目标面板 */
  panel?: PanelKind;
}

export const COMMAND_GROUPS = ['会话', '模型', '边界', '上下文', '智能体', '流程', '系统'] as const;

export const COMMANDS: CommandDef[] = [
  // ── 会话 ──
  { name: 'new', desc: '开始新对话', group: '会话', action: 'exec' },
  { name: 'session', aliases: ['sessions', 'resume'], desc: '切换到其他对话', group: '会话', action: 'panel', panel: 'session' },
  { name: 'rename', desc: '重命名当前对话', group: '会话', action: 'panel', panel: 'rename' },
  { name: 'delete', desc: '删除当前对话', group: '会话', action: 'panel', panel: 'delete' },
  { name: 'export', desc: '导出对话为文本文件', group: '会话', action: 'panel', panel: 'export' },
  // ── 模型 ──
  { name: 'model', aliases: ['mo'], desc: '切换模型 / 思考模式 / 推理强度', group: '模型', action: 'panel', panel: 'model' },
  // ── 边界 ──
  { name: 'mode', desc: '工作模式（计划 / 询问 / 执行）', group: '边界', action: 'panel', panel: 'mode' },
  { name: 'permission', aliases: ['perm', 'permissions'], desc: '权限模式（只读 / 批准 / 自动审核 / 无限制）', group: '边界', action: 'panel', panel: 'permission' },
  { name: 'env', desc: '执行环境（沙箱 / 直接执行）', group: '边界', action: 'panel', panel: 'env' },
  { name: 'network', aliases: ['net'], desc: '网络权限（受限 / 完整）', group: '边界', action: 'panel', panel: 'network' },
  { name: 'path', aliases: ['paths'], desc: '路径授权（可读 / 可写白名单）', group: '边界', action: 'panel', panel: 'path' },
  // ── 上下文 ──
  { name: 'compact', desc: '压缩当前对话上下文', group: '上下文', action: 'exec' },
  { name: 'context', desc: '查看上下文用量构成', group: '上下文', action: 'panel', panel: 'context' },
  // ── 智能体 ──
  { name: 'agents', desc: '子智能体列表与管理', group: '智能体', action: 'panel', panel: 'agents' },
  { name: 'tasks', desc: '后台指令列表与输出', group: '智能体', action: 'panel', panel: 'tasks' },
  { name: 'approvals', desc: '审批记录面板', group: '智能体', action: 'panel', panel: 'approvals' },
  // ── 流程 ──
  { name: 'workflow', aliases: ['wf'], desc: '工作流（激活 / 状态 / 退出）', group: '流程', action: 'panel', panel: 'workflow' },
  { name: 'rewind', desc: '版本回溯（对话与工作区检查点）', group: '流程', action: 'panel', panel: 'rewind' },
  // ── 系统 ──
  { name: 'review', desc: '生成对话回顾', group: '系统', action: 'exec' },
  { name: 'help', aliases: ['?'], desc: '帮助与快捷键', group: '系统', action: 'panel', panel: 'help' },
  { name: 'exit', aliases: ['quit', 'q'], desc: '退出', group: '系统', action: 'exec' },
];

/** 过滤规则（参考 Claude Code）：精确匹配 > 命令名/别名前缀 > 子串 > 描述子串兜底 */
export function filterCommands(query: string): CommandDef[] {
  const q = query.trim().toLowerCase();
  if (!q) return COMMANDS;
  const exact: CommandDef[] = [];
  const starts: CommandDef[] = [];
  const substr: CommandDef[] = [];
  const descHit: CommandDef[] = [];
  for (const cmd of COMMANDS) {
    const names = [cmd.name, ...(cmd.aliases ?? [])];
    if (names.some((n) => n.toLowerCase() === q)) {
      exact.push(cmd);
    } else if (names.some((n) => n.toLowerCase().startsWith(q))) {
      starts.push(cmd);
    } else if (names.some((n) => n.toLowerCase().includes(q))) {
      substr.push(cmd);
    } else if (cmd.desc.toLowerCase().includes(q) || cmd.group.toLowerCase().includes(q)) {
      descHit.push(cmd);
    }
  }
  // 精确命中排最前（如 /mode 不会再被 /model 抢先），其余前缀项跟随后面（分支互斥不重复）
  return [...exact, ...starts, ...substr, ...descHit];
}

/** 按分组组织（保持 COMMAND_GROUPS 顺序；过滤后组可能为空则跳过） */
export function groupCommands(list: CommandDef[]): Array<{ group: string; items: CommandDef[] }> {
  const out: Array<{ group: string; items: CommandDef[] }> = [];
  for (const g of COMMAND_GROUPS) {
    const items = list.filter((c) => c.group === g);
    if (items.length > 0) out.push({ group: g, items });
  }
  return out;
}
