import { TOOL_ICON_MAP } from './icons';
import { t } from '@/locales';

type ToolPayload = Record<string, any> | null | undefined;

const RUNNING_ANIMATIONS: Record<string, string> = {
  create_file: 'file-animation',
  read_file: 'read-animation',
  read_skill: 'read-animation',
  create_skill: 'file-animation',
  delete_file: 'file-animation',
  rename_file: 'file-animation',
  write_file: 'file-animation',
  edit_file: 'file-animation',
  create_folder: 'file-animation',
  web_search: 'search-animation',
  extract_webpage: 'search-animation',
  save_webpage: 'file-animation',
  vlm_analyze: 'file-animation',
  run_command: 'terminal-animation',
  update_memory: 'memory-animation',
  recall_project_memory: 'read-animation',
  search_project_memory: 'search-animation',
  update_project_memory: 'memory-animation',
  sleep: 'wait-animation',
  terminal_session: 'terminal-animation',
  terminal_input: 'terminal-animation',
  terminal_snapshot: 'terminal-animation',
  todo_create: 'file-animation',
  todo_update_task: 'file-animation',
  create_sub_agent: 'terminal-animation',
  ask_user: 'default-animation'
};

// 运行中状态文案映射：改成函数、在调用时求值 t()，避免模块顶层固化语言（语言切换后重取新译文）
function getRunningStatusTexts(): Record<string, string> {
  return {
    create_file: t('toolResults.runningStatus.createFile'),
    sleep: t('toolResults.runningStatus.sleep'),
    delete_file: t('toolResults.runningStatus.deleteFile'),
    rename_file: t('toolResults.runningStatus.renameFile'),
    write_file: t('toolResults.runningStatus.writeFile'),
    edit_file: t('toolResults.runningStatus.editFile'),
    create_folder: t('toolResults.runningStatus.createFolder'),
    web_search: t('toolResults.runningStatus.webSearch'),
    extract_webpage: t('toolResults.runningStatus.extractWebpage'),
    save_webpage: t('toolResults.runningStatus.saveWebpage'),
    run_command: t('toolResults.runningStatus.runCommand'),
    update_memory: t('toolResults.runningStatus.updateMemory'),
    recall_project_memory: t('toolResults.runningStatus.recallProjectMemory'),
    search_project_memory: t('toolResults.runningStatus.searchProjectMemory'),
    update_project_memory: t('toolResults.runningStatus.updateProjectMemory'),
    terminal_session: t('toolResults.runningStatus.terminalSession'),
    terminal_input: t('toolResults.runningStatus.terminalInput'),
    terminal_snapshot: t('toolResults.runningStatus.terminalSnapshot'),
    read_skill: t('toolResults.runningStatus.readSkill'),
    create_skill: t('toolResults.runningStatus.createSkill'),
    ask_user: t('toolResults.runningStatus.askUser'),
  };
}

// 完成状态文案映射：同上，函数化避免语言固化
function getCompletedStatusTexts(): Record<string, string> {
  return {
    create_file: t('toolResults.completedStatus.createFile'),
    delete_file: t('toolResults.completedStatus.deleteFile'),
    sleep: t('toolResults.completedStatus.sleep'),
    rename_file: t('toolResults.completedStatus.renameFile'),
    write_file: t('toolResults.completedStatus.writeFile'),
    edit_file: t('toolResults.completedStatus.editFile'),
    create_folder: t('toolResults.completedStatus.createFolder'),
    web_search: t('toolResults.completedStatus.webSearch'),
    extract_webpage: t('toolResults.completedStatus.extractWebpage'),
    save_webpage: t('toolResults.completedStatus.saveWebpage'),
    vlm_analyze: t('toolResults.completedStatus.vlmAnalyze'),
    run_command: t('toolResults.completedStatus.runCommand'),
    update_memory: t('toolResults.completedStatus.updateMemory'),
    recall_project_memory: t('toolResults.completedStatus.recallProjectMemory'),
    search_project_memory: t('toolResults.completedStatus.searchProjectMemory'),
    update_project_memory: t('toolResults.completedStatus.updateProjectMemory'),
    terminal_session: t('toolResults.completedStatus.terminalSession'),
    terminal_input: t('toolResults.completedStatus.terminalInput'),
    terminal_snapshot: t('toolResults.completedStatus.terminalSnapshot'),
    read_skill: t('toolResults.completedStatus.readSkill'),
    create_skill: t('toolResults.completedStatus.createSkill'),
    ask_user: t('toolResults.completedStatus.askUser'),
  };
}

const LANGUAGE_CLASS_MAP: Record<string, string> = {
  py: 'language-python',
  js: 'language-javascript',
  html: 'language-html',
  css: 'language-css',
  json: 'language-json',
  md: 'language-markdown',
  txt: 'language-plain'
};

function getSearchTopicMap(): Record<string, string> {
  return {
    general: t('toolResults.search.topicGeneral'),
    news: t('toolResults.search.topicNews'),
    finance: t('toolResults.search.topicFinance'),
  };
}

function getRelativeTimeRangeMap(): Record<string, string> {
  return {
    day: t('toolResults.search.timeLast24h'),
    week: t('toolResults.search.timeLast7d'),
    month: t('toolResults.search.timeLast30d'),
    year: t('toolResults.search.timeLast365d'),
  };
}

export function getToolIcon(tool: any): string {
  const toolName = typeof tool === 'string' ? tool : tool?.name;
  if (typeof toolName === 'string' && toolName.startsWith('mcp__')) {
    return 'mcpLogo';
  }
  return TOOL_ICON_MAP[toolName as keyof typeof TOOL_ICON_MAP] || 'settings';
}

export function getToolAnimationClass(tool: any): string {
  if (!tool) {
    return '';
  }
  if (tool.status === 'hinted') {
    return 'hint-animation pulse-slow';
  }
  if (tool.status === 'preparing') {
    return 'preparing-animation';
  }
  if (tool.status === 'running') {
    return RUNNING_ANIMATIONS[tool.name] || 'default-animation';
  }
  return '';
}

function describeReadFileResult(tool: any): string {
  if (!tool?.result || typeof tool.result !== 'object') {
    return t('toolResults.completedStatus.readFile');
  }
  const readType = String(tool.result.type || 'read').toLowerCase();
  if (readType === 'search') {
    const query = tool.result.query ? t('toolResults.sentences.searchQuote', { text: tool.result.query }) : '';
    const count =
      typeof tool.result.returned_matches === 'number'
        ? tool.result.returned_matches
        : tool.result.actual_matches || 0;
    return t('toolResults.sentences.readSearch', { query, count });
  }
  if (readType === 'extract') {
    const segments = Array.isArray(tool.result.segments) ? tool.result.segments : [];
    const totalLines = segments.reduce((sum: number, seg: any) => {
      const start = Number(seg.line_start) || 0;
      const end = Number(seg.line_end) || 0;
      if (!start || !end || end < start) {
        return sum;
      }
      return sum + (end - start + 1);
    }, 0);
    const displayLines = totalLines || tool.result.char_count || 0;
    return t('toolResults.sentences.readExtract', { n: displayLines });
  }
  return t('toolResults.completedStatus.readFile');
}

function isMcpTool(tool: any): boolean {
  const name = String(tool?.name || '');
  return name.startsWith('mcp__');
}

function getMcpToolDisplayName(tool: any): string {
  const customDisplayName = String(tool?.display_name || '').trim();
  if (customDisplayName) {
    return customDisplayName;
  }
  const name = String(tool?.name || '').trim();
  if (!name.startsWith('mcp__')) {
    return name || t('toolResults.mcpTool');
  }
  const parts = name.split('__').filter(Boolean);
  if (parts.length >= 3) {
    const remoteName = parts.slice(2).join('__').trim();
    if (remoteName) {
      return remoteName;
    }
  }
  return name || t('toolResults.mcpTool');
}

export function getToolStatusText(tool: any, opts?: { intentEnabled?: boolean }): string {
  if (!tool) {
    return '';
  }
  const intentEnabled = opts?.intentEnabled !== false;
  const intentText = tool.intent_rendered || tool.intent_full || '';
  const hasIntent = !!intentText;

  // 错误优先展示
  if (
    tool.message &&
    (tool.status === 'failed' || tool.status === 'error' || (tool.result && tool.result.error))
  ) {
    return tool.message;
  }

  // MCP 工具完成态统一显示：<工具名> 执行完成（避免刷新前后文案不一致）
  if (tool.status === 'completed' && isMcpTool(tool)) {
    return t('toolResults.sentences.mcpDone', { name: getMcpToolDisplayName(tool) });
  }

  // 开启时：有 intent 就只显示 intent
  if (intentEnabled && hasIntent) {
    return intentText;
  }

  // 关闭 intent 或无 intent 时，显示状态文案
  if (tool.message) {
    return tool.message;
  }
  if (tool.status === 'hinted') {
    return t('toolResults.sentences.hinted', { name: tool.name });
  }
  if (tool.status === 'preparing') {
    return t('toolResults.sentences.preparing', { name: tool.name });
  }
  if (tool.status === 'running') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      const readType = String(
        tool.argumentSnapshot?.type || tool.arguments?.type || 'read'
      ).toLowerCase();
      const runningMap: Record<string, string> = {
        read: t('toolResults.runningStatus.readFile'),
        search: t('toolResults.runningStatus.readSearch'),
        extract: t('toolResults.runningStatus.readExtract'),
      };
      return runningMap[readType] || t('toolResults.runningStatus.readFile');
    }
    const label = getRunningStatusTexts()[tool.name] || tool.display_name || tool.name || '';
    return label ? label : t('toolResults.runningStatus.fallback');
  }
  if (tool.status === 'awaiting_user_answer') {
    return t('toolResults.status.awaitingUserAnswer');
  }
  if (
    tool.status === 'awaiting_approval' ||
    tool.status === 'pending_approval' ||
    tool.status === 'pending'
  ) {
    return t('toolResults.status.awaitingApprovalDots');
  }
  if (tool.status === 'completed') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      return describeReadFileResult(tool);
    }
    return getCompletedStatusTexts()[tool.name] || t('toolResults.completedStatus.fallback');
  }
  if (tool.status) {
    return `${tool.name} - ${tool.status}`;
  }
  return tool.name || '';
}

export function getToolDescription(tool: any): string {
  if (!tool) {
    return '';
  }
  if (tool.name === 'ask_user') {
    return '';
  }
  const args = tool.argumentSnapshot || tool.arguments;
  const argumentLabel = tool.argumentLabel || buildToolLabel(args);
  if (argumentLabel) {
    return argumentLabel;
  }
  if (tool.statusDetail) {
    return tool.statusDetail;
  }
  if (tool.result && typeof tool.result === 'object' && tool.result.path) {
    return String(tool.result.path).split('/').pop() || '';
  }
  return '';
}

export function cloneToolArguments(args: any): any {
  if (!args || typeof args !== 'object') {
    return null;
  }
  try {
    return JSON.parse(JSON.stringify(args));
  } catch (error) {
    console.warn('无法克隆工具参数:', error);
    return { ...args };
  }
}

export function buildToolLabel(args: any): string {
  if (!args || typeof args !== 'object') {
    return '';
  }
  if (args.command) {
    return args.command;
  }
  if (args.path) {
    return String(args.path).split('/').pop() || '';
  }
  if (args.target_path) {
    return String(args.target_path).split('/').pop() || '';
  }
  if (args.query) {
    return `"${args.query}"`;
  }
  if (args.question) {
    return String(args.question);
  }
  if (typeof args.seconds !== 'undefined') {
    return t('toolResults.duration.seconds', { seconds: args.seconds });
  }
  if (args.name) {
    return args.name;
  }
  return '';
}

export function formatSearchTopic(filters: ToolPayload): string {
  const topic = filters?.topic ? String(filters.topic).toLowerCase() : 'general';
  return getSearchTopicMap()[topic] || t('toolResults.search.topicGeneral');
}

export function formatSearchTime(filters: ToolPayload): string {
  if (!filters) {
    return t('toolResults.search.timeUnlimited');
  }
  if (filters.time_range) {
    const key = String(filters.time_range).toLowerCase();
    return (
      getRelativeTimeRangeMap()[key] ||
      t('toolResults.search.timeRelative', { range: filters.time_range })
    );
  }
  if (typeof filters.days === 'number') {
    return t('toolResults.search.timeLastDays', { n: filters.days });
  }
  if (filters.start_date && filters.end_date) {
    return t('toolResults.search.timeRangeTo', {
      start: filters.start_date,
      end: filters.end_date,
    });
  }
  return t('toolResults.search.timeUnlimited');
}

export function formatSearchDomains(filters: ToolPayload): string {
  const domains = filters?.include_domains;
  if (!Array.isArray(domains) || domains.length === 0) {
    return t('toolResults.search.domainsUnlimited');
  }
  const normalized = domains.map((item) => String(item || '').trim()).filter(Boolean);
  return normalized.length ? normalized.join(', ') : t('toolResults.search.domainsUnlimited');
}

export function getLanguageClass(path: string): string {
  if (!path) {
    return 'language-plain';
  }
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return LANGUAGE_CLASS_MAP[ext] || 'language-plain';
}
