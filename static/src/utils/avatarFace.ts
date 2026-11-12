// 工具名 → StatusAvatar face key 映射
// 未命中的工具回退到 'command'（终端 > 提示符），保证始终有形象
const TOOL_FACE_MAP: Record<string, string> = {
  web_search: 'search',
  conversation_search: 'search',
  extract_webpage: 'webpage',
  save_webpage: 'save',
  write_file: 'write',
  edit_file: 'write',
  read_file: 'read',
  read_skill: 'read',
  conversation_review: 'read',
  vlm_analyze: 'camera',
  ocr_image: 'camera',
  view_image: 'camera',
  view_video: 'camera',
  create_skill: 'skill',
  trigger_easter_egg: 'skill',
  terminal_session: 'monitor',
  terminal_input: 'keyboard',
  terminal_snapshot: 'clipboard',
  sleep: 'sleep',
  run_command: 'command',
  update_memory: 'brain',
  recall_project_memory: 'notebook',
  search_project_memory: 'search',
  update_project_memory: 'notebook',
  todo_create: 'note',
  todo_update_task: 'check',
  create_sub_agent: 'subagent',
  load_tools: 'search',
  terminate_sub_agent: 'subagent',
  send_message_to_sub_agent: 'subagent',
  stop_sub_agent: 'subagent',
  answer_sub_agent_question: 'subagent',
  get_sub_agent_status: 'subagent',
  manage_personalization: 'persona',
  ask_user: 'ask',
  list_mcp_servers: 'mcp'
};

export function toolFaceKey(toolName: string | undefined | null): string {
  const name = String(toolName || '');
  if (name.startsWith('mcp__')) {
    return 'mcp';
  }
  // 浏览器工具名无法穷举（browser_* 持续增加），按前缀统一归到浏览器窗口形象
  if (name.startsWith('browser_')) {
    return 'browser';
  }
  return TOOL_FACE_MAP[name] || 'command';
}
