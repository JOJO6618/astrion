/**
 * 通用图标注册表（唯一入口）。
 *
 * `static/icons/*.svg`（不含 providers/ 子目录）在构建期整体内嵌进 JS，模块加载时
 * 向 <head> 注入一份 `:root { --svg-<文件名>: url("data:…") }`，各处图标只引用
 * 这些变量——图标随主脚本同时就绪，不再按需走网络请求，避免进入页面后晚出现。
 *
 * 渲染方式统一为 mask + `background: currentColor`（颜色随文字/主题）：
 * - 模板：`<span class="icon" :style="iconStyle('terminal')">`，或 `<UiIcon name="copy" />`
 * - SCSS：`mask-image: var(--svg-copy)`（变量名 = 文件名去扩展名）
 * - TS：`ICONS.copy` / `iconVar('copy')` 得到的是 CSS 值 `var(--svg-copy)`，直接赋给
 *   `--icon-src` 等自定义属性，**不要再包 `url()`**。
 *
 * 新增图标：把 svg 放进 `static/icons/` 即可被内嵌；需要驼峰别名再补到 ICONS。
 * 禁止在代码里再写 `/static/icons/xxx.svg` 地址（提供商 logo 除外）。
 */
const RAW_SVGS = import.meta.glob('../../icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true
}) as Record<string, string>;

const SVG_DATA: Record<string, string> = {};
for (const [path, svg] of Object.entries(RAW_SVGS)) {
  const stem = path.slice(path.lastIndexOf('/') + 1, -'.svg'.length);
  SVG_DATA[stem] = `url("data:image/svg+xml,${encodeURIComponent(svg.trim())}")`;
}

const STYLE_ELEMENT_ID = 'astrion-icon-vars';

function installIconVars(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ELEMENT_ID)) return;
  const decls = Object.entries(SVG_DATA)
    .map(([stem, value]) => `--svg-${stem}:${value};`)
    .join('');
  const style = document.createElement('style');
  style.id = STYLE_ELEMENT_ID;
  style.textContent = `:root{${decls}}`;
  document.head.prepend(style);
}

installIconVars();

/** 图标文件名（不含扩展名）→ 可直接作为 mask 图像的 CSS 值。 */
export function iconVar(stem: string): string {
  if (import.meta.env.DEV && !SVG_DATA[stem]) {
    console.warn(`[icons] static/icons/${stem}.svg 不存在`);
  }
  return `var(--svg-${stem})`;
}

/** 驼峰别名 → 文件名（不含扩展名）。 */
const ICON_FILES = Object.freeze({
  alignLeft: 'align-left',
  arrowLeft: 'arrow-left',
  atom: 'atom',
  book: 'book',
  bot: 'bot',
  browser: 'browser',
  brain: 'brain',
  brainCog: 'brain-cog',
  camera: 'camera',
  check: 'check',
  chartColumn: 'chart-column',
  chatBubble: 'chat-bubble',
  messageCircleMore: 'message-circle-more',
  messageQuestion: 'message-circle-question-mark',
  checkbox: 'checkbox',
  chevronDown: 'chevron-down',
  circleAlert: 'circle-alert',
  clipboard: 'clipboard',
  clipboardCheck: 'clipboard-check',
  cloud: 'cloud',
  codex: 'codex',
  clock: 'clock',
  copy: 'copy',
  crosshair: 'crosshair',
  download: 'download',
  ellipsis: 'ellipsis',
  eye: 'eye',
  eyeCheck: 'eye-check',
  file: 'file',
  filePen: 'file-pen',
  flag: 'flag',
  folder: 'folder',
  folderClosed: 'folder-closed',
  folderOpen: 'folder-open',
  git: 'git',
  gitBranch: 'git-branch',
  globe: 'globe',
  hammer: 'hammer',
  hash: 'hash',
  info: 'info',
  laptop: 'laptop',
  layers: 'layers',
  keyboard: 'keyboard',
  layoutGrid: 'layout-grid',
  listTodo: 'list-todo',
  logOut: 'log-out',
  menu: 'menu',
  mic: 'mic',
  mcpLogo: 'mcp-logo',
  monitor: 'monitor',
  navigation: 'navigation',
  notebook: 'notebook',
  notebookPen: 'notebook-pen',
  notebookSearch: 'notebook-search',
  octagon: 'octagon',
  palette: 'palette',
  panelLeft: 'panel-left',
  panelRight: 'panel-right',
  pencil: 'pencil',
  play: 'play',
  popOut: 'square-arrow-out-up-right',
  plus: 'plus',
  puzzle: 'puzzle',
  python: 'python',
  recycle: 'recycle',
  refreshCw: 'refresh-cw',
  save: 'save',
  scan: 'scan',
  search: 'search',
  settings: 'settings',
  shield: 'shield',
  sparkles: 'sparkles',
  stickyNote: 'sticky-note',
  terminal: 'terminal',
  timer: 'timer',
  trash: 'trash',
  triangleAlert: 'triangle-alert',
  bell: 'bell',
  user: 'user',
  userPen: 'user-pen',
  workflow: 'workflow',
  wrench: 'wrench',
  x: 'x',
  zap: 'zap'
});

export type IconKey = keyof typeof ICON_FILES;

/** 驼峰别名 → CSS 值 `var(--svg-<文件名>)`。 */
export const ICONS = Object.freeze(
  Object.fromEntries(
    Object.entries(ICON_FILES).map(([key, stem]) => [key, iconVar(stem)])
  ) as Record<IconKey, string>
);

/** 给 `.icon` 等读取 `--icon-src` 的元素用的 style 对象。 */
export function iconSrcStyle(key: IconKey): Record<string, string> {
  return { '--icon-src': ICONS[key] };
}

export const TOOL_ICON_MAP = Object.freeze({
  load_tools: 'wrench',
  enter_worktree: 'gitBranch',
  exit_worktree: 'gitBranch',
  create_file: 'file',
  create_skill: 'sparkles',
  manage_personalization: 'userPen',
  ask_user: 'messageQuestion',
  create_folder: 'folder',
  create_sub_agent: 'bot',
  delete_file: 'trash',
  extract_webpage: 'globe',
  browser_tabs: 'browser',
  browser_navigate: 'browser',
  browser_snapshot: 'browser',
  browser_find: 'browser',
  browser_click: 'browser',
  browser_type: 'browser',
  browser_fill_form: 'browser',
  browser_select_option: 'browser',
  browser_hover: 'browser',
  browser_drag: 'browser',
  browser_press_key: 'browser',
  browser_scroll: 'browser',
  browser_wait_for: 'browser',
  browser_take_screenshot: 'browser',
  browser_handle_dialog: 'browser',
  browser_file_upload: 'browser',
  browser_drop: 'browser',
  browser_evaluate: 'browser',
  browser_fill_saved_login: 'browser',
  browser_console_messages: 'browser',
  browser_network_requests: 'browser',
  browser_network_request: 'browser',
  browser_downloads: 'browser',
  browser_emulate_media: 'browser',
  write_file: 'pencil',
  edit_file: 'pencil',
  vlm_analyze: 'camera',
  ocr_image: 'camera',
  read_file: 'book',
  read_skill: 'book',
  search_skills: 'search',
  rename_file: 'pencil',
  run_command: 'terminal',
  save_webpage: 'save',
  sleep: 'clock',
  todo_create: 'listTodo',
  todo_update_task: 'check',
  terminal_input: 'keyboard',
  terminal_session: 'monitor',
  terminal_snapshot: 'scan',
  unfocus_file: 'eye',
  update_memory: 'brain',
  recall_project_memory: 'notebook',
  search_project_memory: 'notebookSearch',
  update_project_memory: 'notebookPen',
  conversation_search: 'search',
  conversation_review: 'book',
  get_sub_agent_status: 'bot',
  web_search: 'search',
  trigger_easter_egg: 'sparkles',
  list_mcp_servers: 'mcpLogo',
  view_image: 'camera',
  view_video: 'eye',
  // 工作流工具统一使用 workflow 专用图标
  activate_workflow: 'workflow',
  report_workflow_stage: 'workflow',
  choose_workflow_branch: 'workflow',
  get_workflow_status: 'workflow',
  deactivate_workflow: 'workflow',
  list_workflows: 'workflow',
  save_workflow: 'workflow',
  // 补齐原先回落到默认齿轮的原生工具
  submit_plan: 'clipboardCheck',
  terminate_sub_agent: 'bot',
  send_message_to_sub_agent: 'bot',
  stop_sub_agent: 'bot',
  answer_sub_agent_question: 'bot',
  // 定时任务工具统一使用 timer 图标
  submit_schedule: 'timer',
  query_schedules: 'timer',
  manage_schedule: 'timer'
} satisfies Record<string, keyof typeof ICONS>);

export const TOOL_CATEGORY_ICON_MAP = Object.freeze({
  network: 'globe',
  mcp: 'mcpLogo',
  file_edit: 'pencil',
  personalization: 'userPen',
  communication: 'messageQuestion',
  read_focus: 'eye',
  skills: 'sparkles',
  terminal_realtime: 'monitor',
  terminal_command: 'terminal',
  memory: 'brain',
  todo: 'listTodo',
  sub_agent: 'bot',
  easter_egg: 'sparkles'
} satisfies Record<string, keyof typeof ICONS>);
