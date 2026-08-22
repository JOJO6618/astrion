import type { SlashCommand } from './types.js';

export const slashCommands: SlashCommand[] = [
  { name: '/new', description: '新建对话', kind: 'immediate' },
  { name: '/resume', description: '查看对话列表并切换对话', kind: 'picker' },
  { name: '/model', description: '切换模型与思考模式', kind: 'picker' },
  { name: '/versioning', description: '管理版本控制', aliases: ['/version'], kind: 'picker' },
  { name: '/permission', description: '切换权限模式', aliases: ['/perm'], kind: 'picker' },
  { name: '/execution', description: '切换执行环境', aliases: ['/exec', '/env'], kind: 'picker' },
  { name: '/workspace', description: '选择或切换工作区', kind: 'picker' },
  { name: '/compact', description: '压缩当前对话上下文', kind: 'immediate' },
  { name: '/tools', description: '启用或禁用工具', aliases: ['/tool'], kind: 'picker' },
  { name: '/path', description: '管理路径授权', kind: 'form' },
  { name: '/skill', description: '选择本轮要使用的 skill', kind: 'picker' },
  { name: '/status', description: '查看当前状态', kind: 'panel' },
  { name: '/help', description: '查看命令帮助', kind: 'panel' },
  { name: '/clear', description: '清空当前 CLI 显示', kind: 'immediate' },
  { name: '/exit', description: '退出 CLI', kind: 'immediate' },
];

export function filterCommands(query: string): SlashCommand[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized || normalized === '/') return slashCommands;
  return slashCommands.filter((command) => {
    const candidates = [command.name, ...(command.aliases || [])];
    return candidates.some((name) => name.toLowerCase().startsWith(normalized) || name.toLowerCase().includes(normalized));
  });
}
