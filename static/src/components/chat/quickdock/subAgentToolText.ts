import { t } from '@/locales';

/**
 * 子智能体工具调用的统一展示：自然语言动作 + 关键参数。
 * 快捷面板详情（RunnerDetailPanel）与子智能体活动弹窗（SubAgentActivityDialog）共用，
 * 新增子智能体工具时在这里补一条，避免两处文案分叉。
 */
export interface SubAgentToolEntry {
  tool?: string;
  args?: Record<string, any> | null;
}

export interface SubAgentToolParts {
  /** 自然语言动作，如「运行指令」「编辑文件」 */
  label: string;
  /** 关键参数（可为空串），如命令、路径、URL */
  detail: string;
}

const str = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');

/** 文本行数：空串为 0；末尾换行不额外计一行。 */
export function countTextLines(value: unknown): number {
  if (typeof value !== 'string' || value === '') return 0;
  const body = value.endsWith('\n') ? value.slice(0, -1) : value;
  return body.split('\n').length;
}

/** edit_file 的行数增减：新内容行数计增、旧内容行数计减（replace_all 不按命中次数放大）。 */
export function summarizeEditStats(args: Record<string, any>): { added: number; removed: number } {
  const replacements = Array.isArray(args.replacements) ? args.replacements : [];
  let added = 0;
  let removed = 0;
  for (const item of replacements) {
    if (!item || typeof item !== 'object') continue;
    added += countTextLines(item.new_string);
    removed += countTextLines(item.old_string);
  }
  return { added, removed };
}

function readFileDetail(args: Record<string, any>): { label: string; detail: string } {
  const path = str(args.path) || str(args.file_path);
  const mode = str(args.type) || 'read';
  if (mode === 'search') {
    const query = str(args.query);
    return {
      label: t('quickdock.toolSearchFile'),
      detail: [path, query ? `"${query}"` : ''].filter(Boolean).join(' ')
    };
  }
  if (mode === 'extract') {
    return { label: t('quickdock.toolExtractFile'), detail: path };
  }
  const start = Number(args.start_line);
  const end = Number(args.end_line);
  let range = '';
  if (Number.isFinite(start) && start > 0) {
    range = Number.isFinite(end) && end >= start ? `L${start}-${end}` : `L${start}`;
  }
  return { label: t('quickdock.toolReadFile'), detail: [path, range].filter(Boolean).join(' ') };
}

export function buildSubAgentToolParts(entry: SubAgentToolEntry): SubAgentToolParts {
  const tool = entry.tool || '';
  const args = entry.args || {};
  switch (tool) {
    case 'read_file':
      return readFileDetail(args);
    case 'write_file':
      return {
        label: args.append ? t('quickdock.toolAppendFile') : t('quickdock.toolWriteFile'),
        detail: str(args.file_path) || str(args.path)
      };
    case 'edit_file': {
      const { added, removed } = summarizeEditStats(args);
      const path = str(args.file_path) || str(args.path);
      const stats = added || removed ? `+${added} -${removed}` : '';
      return {
        label: t('quickdock.toolEditFile'),
        detail: [path, stats].filter(Boolean).join(' ')
      };
    }
    case 'run_command':
      return { label: t('quickdock.toolRunCommand'), detail: str(args.command) };
    case 'web_search':
      return { label: t('quickdock.toolSearch'), detail: str(args.query) || str(args.q) };
    case 'extract_webpage':
      return { label: t('quickdock.toolExtract'), detail: str(args.url) };
    case 'save_webpage':
      return { label: t('quickdock.toolSaveWebpage'), detail: str(args.url) };
    case 'read_mediafile':
      return { label: t('quickdock.toolReadMedia'), detail: str(args.path) || str(args.file_path) };
    case 'read_skill':
      return { label: t('quickdock.toolReadSkill'), detail: str(args.skill_name) };
    case 'recall_project_memory':
      return { label: t('quickdock.toolRecallMemory'), detail: str(args.name) };
    case 'search_project_memory':
      return { label: t('quickdock.toolSearchMemory'), detail: str(args.query) };
    case 'todo_create':
      return { label: t('quickdock.toolTodoCreate'), detail: str(args.overview) };
    case 'todo_update_task': {
      const indices = Array.isArray(args.task_indices)
        ? args.task_indices
        : args.task_index != null
          ? [args.task_index]
          : [];
      return {
        label: t('quickdock.toolTodoUpdate'),
        detail: indices.length ? `#${indices.join(',')}` : ''
      };
    }
    case 'ask_master':
      return { label: t('quickdock.toolAskMaster'), detail: str(args.question) };
    case 'ask_other_agent':
      return { label: t('quickdock.toolAskOther'), detail: str(args.name) };
    case 'answer_other_agent':
      return { label: t('quickdock.toolAnswerOther'), detail: str(args.answer) };
    case 'get_sub_agent_status':
      return { label: t('quickdock.toolSubAgentStatus'), detail: '' };
    default:
      // 未知工具（含 MCP）：原样显示工具名，不编造自然语言
      return { label: tool || t('common.tool'), detail: '' };
  }
}

/** 单行文本（动作 + 参数），供只有一个文本槽的界面使用。 */
export function buildSubAgentToolText(entry: SubAgentToolEntry): string {
  const { label, detail } = buildSubAgentToolParts(entry);
  return detail ? `${label} ${detail}` : label;
}
