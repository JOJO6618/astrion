import { TOOL_ICON_MAP } from './icons';

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

const RUNNING_STATUS_TEXTS: Record<string, string> = {
  create_file: '正在创建文件...',
  sleep: '正在等待...',
  delete_file: '正在删除文件...',
  rename_file: '正在重命名文件...',
  write_file: '正在写入文件...',
  edit_file: '正在编辑文件...',
  create_folder: '正在创建文件夹...',
  web_search: '正在搜索网络...',
  extract_webpage: '正在提取网页...',
  save_webpage: '正在保存网页...',
  run_command: '调用 run_command',
  update_memory: '正在更新记忆...',
  recall_project_memory: '正在回顾项目记忆...',
  search_project_memory: '正在检索项目记忆...',
  update_project_memory: '正在更新项目记忆...',
  terminal_session: '正在管理终端会话...',
  terminal_input: '调用 terminal_input',
  terminal_snapshot: '正在获取终端快照...',
  read_skill: '正在读取技能...',
  create_skill: '正在归档技能...',
  ask_user: '等待用户回答...'
};

const COMPLETED_STATUS_TEXTS: Record<string, string> = {
  create_file: '文件创建成功',
  delete_file: '文件删除成功',
  sleep: '等待完成',
  rename_file: '文件重命名成功',
  write_file: '文件写入完成',
  edit_file: '文件编辑完成',
  create_folder: '文件夹创建成功',
  web_search: '搜索完成',
  extract_webpage: '网页提取完成',
  save_webpage: '网页保存完成（纯文本）',
  vlm_analyze: '图片解析完成',
  run_command: '命令执行完成',
  update_memory: '记忆更新成功',
  recall_project_memory: '项目记忆已读取',
  search_project_memory: '项目记忆检索完成',
  update_project_memory: '项目记忆已更新',
  terminal_session: '终端操作完成',
  terminal_input: '终端输入完成',
  terminal_snapshot: '终端快照已返回',
  read_skill: '技能读取完成',
  create_skill: '技能归档完成',
  ask_user: '用户已回答'
};

const LANGUAGE_CLASS_MAP: Record<string, string> = {
  py: 'language-python',
  js: 'language-javascript',
  html: 'language-html',
  css: 'language-css',
  json: 'language-json',
  md: 'language-markdown',
  txt: 'language-plain'
};

const SEARCH_TOPIC_MAP: Record<string, string> = {
  general: '通用',
  news: '新闻',
  finance: '金融'
};

const RELATIVE_TIME_RANGE_MAP: Record<string, string> = {
  day: '过去24小时',
  week: '过去7天',
  month: '过去30天',
  year: '过去365天'
};

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
    return '文件读取完成';
  }
  const readType = String(tool.result.type || 'read').toLowerCase();
  if (readType === 'search') {
    const query = tool.result.query ? `「${tool.result.query}」` : '';
    const count =
      typeof tool.result.returned_matches === 'number'
        ? tool.result.returned_matches
        : tool.result.actual_matches || 0;
    return `搜索${query}，得到${count}个结果`;
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
    return `提取了${displayLines}行`;
  }
  return '文件读取完成';
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
    return name || 'MCP 工具';
  }
  const parts = name.split('__').filter(Boolean);
  if (parts.length >= 3) {
    const remoteName = parts.slice(2).join('__').trim();
    if (remoteName) {
      return remoteName;
    }
  }
  return name || 'MCP 工具';
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
    return `${getMcpToolDisplayName(tool)} 执行完成`;
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
    return `可能需要 ${tool.name}...`;
  }
  if (tool.status === 'preparing') {
    return `准备调用 ${tool.name}...`;
  }
  if (tool.status === 'running') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      const readType = String(
        tool.argumentSnapshot?.type || tool.arguments?.type || 'read'
      ).toLowerCase();
      const runningMap: Record<string, string> = {
        read: '正在读取文件...',
        search: '正在执行搜索...',
        extract: '正在提取内容...'
      };
      return runningMap[readType] || '正在读取文件...';
    }
    const label = RUNNING_STATUS_TEXTS[tool.name] || tool.display_name || tool.name || '';
    return label ? label : '调用工具中';
  }
  if (tool.status === 'awaiting_user_answer') {
    return '等待回答';
  }
  if (
    tool.status === 'awaiting_approval' ||
    tool.status === 'pending_approval' ||
    tool.status === 'pending'
  ) {
    return '等待审批...';
  }
  if (tool.status === 'completed') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      return describeReadFileResult(tool);
    }
    return COMPLETED_STATUS_TEXTS[tool.name] || '执行完成';
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
    return `${args.seconds} 秒`;
  }
  if (args.name) {
    return args.name;
  }
  return '';
}

export function formatSearchTopic(filters: ToolPayload): string {
  const topic = filters?.topic ? String(filters.topic).toLowerCase() : 'general';
  return SEARCH_TOPIC_MAP[topic] || '通用';
}

export function formatSearchTime(filters: ToolPayload): string {
  if (!filters) {
    return '未限定时间';
  }
  if (filters.time_range) {
    const key = String(filters.time_range).toLowerCase();
    return RELATIVE_TIME_RANGE_MAP[key] || `相对范围：${filters.time_range}`;
  }
  if (typeof filters.days === 'number') {
    return `过去${filters.days}天`;
  }
  if (filters.start_date && filters.end_date) {
    return `${filters.start_date} 至 ${filters.end_date}`;
  }
  return '未限定时间';
}

export function formatSearchDomains(filters: ToolPayload): string {
  const domains = filters?.include_domains;
  if (!Array.isArray(domains) || domains.length === 0) {
    return '未限定网站';
  }
  const normalized = domains.map((item) => String(item || '').trim()).filter(Boolean);
  return normalized.length ? normalized.join(', ') : '未限定网站';
}

export function getLanguageClass(path: string): string {
  if (!path) {
    return 'language-plain';
  }
  const ext = path.split('.').pop()?.toLowerCase() || '';
  return LANGUAGE_CLASS_MAP[ext] || 'language-plain';
}
