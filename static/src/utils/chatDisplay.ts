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

// 工具状态文案映射（准备 / 正在 / 完成 / 失败）：改成函数、在调用时求值 t()，
// 避免模块顶层固化语言（语言切换后重取新译文）。未知工具与 MCP 工具走兜底项。
function getToolStatusTexts(): {
  preparing: Record<string, string>;
  running: Record<string, string>;
  completed: Record<string, string>;
  failed: Record<string, string>;
} {
  return {
    preparing: {
      read_file: t('toolResults.preparingStatus.readFile'),
      read_skill: t('toolResults.preparingStatus.readSkill'),
      view_image: t('toolResults.preparingStatus.viewImage'),
      view_video: t('toolResults.preparingStatus.viewVideo'),
      vlm_analyze: t('toolResults.preparingStatus.vlmAnalyze'),
      run_command: t('toolResults.preparingStatus.runCommand'),
      terminal_input: t('toolResults.preparingStatus.terminalInput'),
      terminal_session: t('toolResults.preparingStatus.terminalSession'),
      terminal_snapshot: t('toolResults.preparingStatus.terminalSnapshot'),
      write_file: t('toolResults.preparingStatus.writeFile'),
      edit_file: t('toolResults.preparingStatus.editFile'),
      web_search: t('toolResults.preparingStatus.webSearch'),
      extract_webpage: t('toolResults.preparingStatus.extractWebpage'),
      save_webpage: t('toolResults.preparingStatus.saveWebpage'),
      update_memory: t('toolResults.preparingStatus.updateMemory'),
      update_project_memory: t('toolResults.preparingStatus.updateProjectMemory'),
      recall_project_memory: t('toolResults.preparingStatus.recallProjectMemory'),
      search_project_memory: t('toolResults.preparingStatus.searchProjectMemory'),
      conversation_search: t('toolResults.preparingStatus.conversationSearch'),
      conversation_review: t('toolResults.preparingStatus.conversationReview'),
      create_sub_agent: t('toolResults.preparingStatus.createSubAgent'),
      terminate_sub_agent: t('toolResults.preparingStatus.terminateSubAgent'),
      get_sub_agent_status: t('toolResults.preparingStatus.getSubAgentStatus'),
      activate_workflow: t('toolResults.preparingStatus.activateWorkflow'),
      report_workflow_stage: t('toolResults.preparingStatus.reportWorkflowStage'),
      choose_workflow_branch: t('toolResults.preparingStatus.chooseWorkflowBranch'),
      get_workflow_status: t('toolResults.preparingStatus.getWorkflowStatus'),
      deactivate_workflow: t('toolResults.preparingStatus.deactivateWorkflow'),
      list_workflows: t('toolResults.preparingStatus.listWorkflows'),
      save_workflow: t('toolResults.preparingStatus.saveWorkflow'),
      todo_create: t('toolResults.preparingStatus.todoCreate'),
      todo_update_task: t('toolResults.preparingStatus.todoUpdateTask'),
      create_skill: t('toolResults.preparingStatus.createSkill'),
      load_tools: t('toolResults.preparingStatus.loadTools'),
      list_mcp_servers: t('toolResults.preparingStatus.listMcpServers'),
      submit_plan: t('toolResults.preparingStatus.submitPlan'),
      manage_personalization: t('toolResults.preparingStatus.managePersonalization'),
      trigger_easter_egg: t('toolResults.preparingStatus.triggerEasterEgg'),
      ask_user: t('toolResults.preparingStatus.askUser'),
      sleep: t('toolResults.preparingStatus.sleep'),
      mcpTool: t('toolResults.preparingStatus.mcpTool'),
      fallback: t('toolResults.preparingStatus.fallback')
    },
    running: {
      read_file: t('toolResults.runningStatus.readFile'),
      read_skill: t('toolResults.runningStatus.readSkill'),
      view_image: t('toolResults.runningStatus.viewImage'),
      view_video: t('toolResults.runningStatus.viewVideo'),
      vlm_analyze: t('toolResults.runningStatus.vlmAnalyze'),
      run_command: t('toolResults.runningStatus.runCommand'),
      terminal_input: t('toolResults.runningStatus.terminalInput'),
      terminal_session: t('toolResults.runningStatus.terminalSession'),
      terminal_snapshot: t('toolResults.runningStatus.terminalSnapshot'),
      write_file: t('toolResults.runningStatus.writeFile'),
      edit_file: t('toolResults.runningStatus.editFile'),
      web_search: t('toolResults.runningStatus.webSearch'),
      extract_webpage: t('toolResults.runningStatus.extractWebpage'),
      save_webpage: t('toolResults.runningStatus.saveWebpage'),
      update_memory: t('toolResults.runningStatus.updateMemory'),
      update_project_memory: t('toolResults.runningStatus.updateProjectMemory'),
      recall_project_memory: t('toolResults.runningStatus.recallProjectMemory'),
      search_project_memory: t('toolResults.runningStatus.searchProjectMemory'),
      conversation_search: t('toolResults.runningStatus.conversationSearch'),
      conversation_review: t('toolResults.runningStatus.conversationReview'),
      create_sub_agent: t('toolResults.runningStatus.createSubAgent'),
      terminate_sub_agent: t('toolResults.runningStatus.terminateSubAgent'),
      get_sub_agent_status: t('toolResults.runningStatus.getSubAgentStatus'),
      activate_workflow: t('toolResults.runningStatus.activateWorkflow'),
      report_workflow_stage: t('toolResults.runningStatus.reportWorkflowStage'),
      choose_workflow_branch: t('toolResults.runningStatus.chooseWorkflowBranch'),
      get_workflow_status: t('toolResults.runningStatus.getWorkflowStatus'),
      deactivate_workflow: t('toolResults.runningStatus.deactivateWorkflow'),
      list_workflows: t('toolResults.runningStatus.listWorkflows'),
      save_workflow: t('toolResults.runningStatus.saveWorkflow'),
      todo_create: t('toolResults.runningStatus.todoCreate'),
      todo_update_task: t('toolResults.runningStatus.todoUpdateTask'),
      create_skill: t('toolResults.runningStatus.createSkill'),
      load_tools: t('toolResults.runningStatus.loadTools'),
      list_mcp_servers: t('toolResults.runningStatus.listMcpServers'),
      submit_plan: t('toolResults.runningStatus.submitPlan'),
      manage_personalization: t('toolResults.runningStatus.managePersonalization'),
      trigger_easter_egg: t('toolResults.runningStatus.triggerEasterEgg'),
      ask_user: t('toolResults.runningStatus.askUser'),
      sleep: t('toolResults.runningStatus.sleep'),
      mcpTool: t('toolResults.runningStatus.mcpTool'),
      fallback: t('toolResults.runningStatus.fallback')
    },
    completed: {
      read_file: t('toolResults.completedStatus.readFile'),
      read_skill: t('toolResults.completedStatus.readSkill'),
      view_image: t('toolResults.completedStatus.viewImage'),
      view_video: t('toolResults.completedStatus.viewVideo'),
      vlm_analyze: t('toolResults.completedStatus.vlmAnalyze'),
      run_command: t('toolResults.completedStatus.runCommand'),
      terminal_input: t('toolResults.completedStatus.terminalInput'),
      terminal_session: t('toolResults.completedStatus.terminalSession'),
      terminal_snapshot: t('toolResults.completedStatus.terminalSnapshot'),
      write_file: t('toolResults.completedStatus.writeFile'),
      edit_file: t('toolResults.completedStatus.editFile'),
      web_search: t('toolResults.completedStatus.webSearch'),
      extract_webpage: t('toolResults.completedStatus.extractWebpage'),
      save_webpage: t('toolResults.completedStatus.saveWebpage'),
      update_memory: t('toolResults.completedStatus.updateMemory'),
      update_project_memory: t('toolResults.completedStatus.updateProjectMemory'),
      recall_project_memory: t('toolResults.completedStatus.recallProjectMemory'),
      search_project_memory: t('toolResults.completedStatus.searchProjectMemory'),
      conversation_search: t('toolResults.completedStatus.conversationSearch'),
      conversation_review: t('toolResults.completedStatus.conversationReview'),
      create_sub_agent: t('toolResults.completedStatus.createSubAgent'),
      terminate_sub_agent: t('toolResults.completedStatus.terminateSubAgent'),
      get_sub_agent_status: t('toolResults.completedStatus.getSubAgentStatus'),
      activate_workflow: t('toolResults.completedStatus.activateWorkflow'),
      report_workflow_stage: t('toolResults.completedStatus.reportWorkflowStage'),
      choose_workflow_branch: t('toolResults.completedStatus.chooseWorkflowBranch'),
      get_workflow_status: t('toolResults.completedStatus.getWorkflowStatus'),
      deactivate_workflow: t('toolResults.completedStatus.deactivateWorkflow'),
      list_workflows: t('toolResults.completedStatus.listWorkflows'),
      save_workflow: t('toolResults.completedStatus.saveWorkflow'),
      todo_create: t('toolResults.completedStatus.todoCreate'),
      todo_update_task: t('toolResults.completedStatus.todoUpdateTask'),
      create_skill: t('toolResults.completedStatus.createSkill'),
      load_tools: t('toolResults.completedStatus.loadTools'),
      list_mcp_servers: t('toolResults.completedStatus.listMcpServers'),
      submit_plan: t('toolResults.completedStatus.submitPlan'),
      manage_personalization: t('toolResults.completedStatus.managePersonalization'),
      trigger_easter_egg: t('toolResults.completedStatus.triggerEasterEgg'),
      ask_user: t('toolResults.completedStatus.askUser'),
      sleep: t('toolResults.completedStatus.sleep'),
      mcpTool: t('toolResults.completedStatus.mcpTool'),
      fallback: t('toolResults.completedStatus.fallback')
    },
    failed: {
      read_file: t('toolResults.failedStatus.readFile'),
      read_skill: t('toolResults.failedStatus.readSkill'),
      view_image: t('toolResults.failedStatus.viewImage'),
      view_video: t('toolResults.failedStatus.viewVideo'),
      vlm_analyze: t('toolResults.failedStatus.vlmAnalyze'),
      run_command: t('toolResults.failedStatus.runCommand'),
      terminal_input: t('toolResults.failedStatus.terminalInput'),
      terminal_session: t('toolResults.failedStatus.terminalSession'),
      terminal_snapshot: t('toolResults.failedStatus.terminalSnapshot'),
      write_file: t('toolResults.failedStatus.writeFile'),
      edit_file: t('toolResults.failedStatus.editFile'),
      web_search: t('toolResults.failedStatus.webSearch'),
      extract_webpage: t('toolResults.failedStatus.extractWebpage'),
      save_webpage: t('toolResults.failedStatus.saveWebpage'),
      update_memory: t('toolResults.failedStatus.updateMemory'),
      update_project_memory: t('toolResults.failedStatus.updateProjectMemory'),
      recall_project_memory: t('toolResults.failedStatus.recallProjectMemory'),
      search_project_memory: t('toolResults.failedStatus.searchProjectMemory'),
      conversation_search: t('toolResults.failedStatus.conversationSearch'),
      conversation_review: t('toolResults.failedStatus.conversationReview'),
      create_sub_agent: t('toolResults.failedStatus.createSubAgent'),
      terminate_sub_agent: t('toolResults.failedStatus.terminateSubAgent'),
      get_sub_agent_status: t('toolResults.failedStatus.getSubAgentStatus'),
      activate_workflow: t('toolResults.failedStatus.activateWorkflow'),
      report_workflow_stage: t('toolResults.failedStatus.reportWorkflowStage'),
      choose_workflow_branch: t('toolResults.failedStatus.chooseWorkflowBranch'),
      get_workflow_status: t('toolResults.failedStatus.getWorkflowStatus'),
      deactivate_workflow: t('toolResults.failedStatus.deactivateWorkflow'),
      list_workflows: t('toolResults.failedStatus.listWorkflows'),
      save_workflow: t('toolResults.failedStatus.saveWorkflow'),
      todo_create: t('toolResults.failedStatus.todoCreate'),
      todo_update_task: t('toolResults.failedStatus.todoUpdateTask'),
      create_skill: t('toolResults.failedStatus.createSkill'),
      load_tools: t('toolResults.failedStatus.loadTools'),
      list_mcp_servers: t('toolResults.failedStatus.listMcpServers'),
      submit_plan: t('toolResults.failedStatus.submitPlan'),
      manage_personalization: t('toolResults.failedStatus.managePersonalization'),
      trigger_easter_egg: t('toolResults.failedStatus.triggerEasterEgg'),
      ask_user: t('toolResults.failedStatus.askUser'),
      sleep: t('toolResults.failedStatus.sleep'),
      mcpTool: t('toolResults.failedStatus.mcpTool'),
      fallback: t('toolResults.failedStatus.fallback')
    }
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
    finance: t('toolResults.search.topicFinance')
  };
}

function getRelativeTimeRangeMap(): Record<string, string> {
  return {
    day: t('toolResults.search.timeLast24h'),
    week: t('toolResults.search.timeLast7d'),
    month: t('toolResults.search.timeLast30d'),
    year: t('toolResults.search.timeLast365d')
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

// read_file / read_skill 的搜索、提取子类型有专属结果文案（信息量更高）；
// 普通读取返回空串，由调用方走四态表（read_file / read_skill 各自文案）。
function describeReadFileResult(tool: any): string {
  if (!tool?.result || typeof tool.result !== 'object') {
    return '';
  }
  const readType = String(tool.result.type || 'read').toLowerCase();
  if (readType === 'search') {
    const query = tool.result.query
      ? t('toolResults.sentences.searchQuote', { text: tool.result.query })
      : '';
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
  return '';
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

// 状态文案取值（不含 intent 优先级）：完整视图与极简摘要共用同一套四态文案，
// 调用方自行决定 intent 是否优先。MCP 工具名不可穷举，走 mcpTool/fallback 兜底。
export function getToolStateText(tool: any): string {
  if (!tool) {
    return '';
  }
  const status = String(tool.status || '').toLowerCase();
  const texts = getToolStatusTexts();
  const key = isMcpTool(tool) ? 'mcpTool' : String(tool.name || '');

  if (status === 'hinted') {
    return t('toolResults.sentences.hinted', { name: tool.name });
  }
  if (status === 'preparing') {
    return texts.preparing[key] || texts.preparing.fallback;
  }
  if (status === 'running') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      const readType = String(
        tool.argumentSnapshot?.type || tool.arguments?.type || 'read'
      ).toLowerCase();
      if (readType === 'search') {
        return t('toolResults.runningStatus.readSearch');
      }
      if (readType === 'extract') {
        return t('toolResults.runningStatus.readExtract');
      }
    }
    return texts.running[key] || texts.running.fallback;
  }
  if (status === 'awaiting_user_answer') {
    return t('toolResults.status.awaitingUserAnswer');
  }
  if (
    status === 'awaiting_approval' ||
    status === 'pending_approval' ||
    status === 'pending'
  ) {
    return t('toolResults.status.awaitingApprovalDots');
  }
  if (status === 'completed') {
    if (tool.name === 'read_file' || tool.name === 'read_skill') {
      const readResultText = describeReadFileResult(tool);
      if (readResultText) {
        return readResultText;
      }
    }
    // MCP 完成态带上具体工具名（可读性优于笼统的“MCP 工具”）
    if (isMcpTool(tool)) {
      return t('toolResults.sentences.mcpDone', { name: getMcpToolDisplayName(tool) });
    }
    return texts.completed[key] || texts.completed.fallback;
  }
  if (status === 'failed' || status === 'error') {
    return texts.failed[key] || texts.failed.fallback;
  }
  if (status) {
    return `${tool.name} - ${tool.status}`;
  }
  return tool.name || '';
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

  // MCP 工具完成态优先于 intent：完成态显示具体工具名，避免刷新前后文案不一致
  if (tool.status === 'completed' && isMcpTool(tool)) {
    return t('toolResults.sentences.mcpDone', { name: getMcpToolDisplayName(tool) });
  }

  // 开启时：有 intent 就只显示 intent
  if (intentEnabled && hasIntent) {
    return intentText;
  }

  // 关闭 intent 或无 intent 时：残留下发文案优先，其余按工具名出四态文案
  if (tool.message) {
    return tool.message;
  }
  return getToolStateText(tool);
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
      end: filters.end_date
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
