// 工具增强显示的渲染函数
//
// 结构约定（§5.6）：说明区 meta 只放调用参数；结果、统计、输出只放在 content 侧。
// 公共拼装件见 toolResultHtml.ts；失败原因由 renderEnhancedToolResult 统一追加，
// 各渲染器不再各自输出 result.error，避免漏显示或重复显示。

import { t } from '@/locales';
import { withWorkspaceScope } from '@/app/methods/workspaceRequestScope';
import { renderSubAgentResult } from './subAgentResults';
import { renderEditFileDiff } from './editFileDiff';
import {
  body,
  box,
  empty,
  errorBlock,
  escapeHtml,
  formatBytes,
  lv,
  meta,
  param,
  pendingStatus,
  preBox,
  resultErrorText,
  seconds,
  summary,
  warning,
  yesNo
} from './toolResultHtml';

export { escapeHtml, formatBytes } from './toolResultHtml';

const L = (key: string) => t(`toolResults.labels.${key}`);

export function renderEnhancedToolResult(
  action: any,
  formatSearchTopic: (filters: Record<string, any>) => string,
  formatSearchTime: (filters: Record<string, any>) => string,
  formatSearchDomains: (filters: Record<string, any>) => string
): string {
  const tool = action.tool;
  const result = tool?.result;
  const args = tool?.arguments || {};
  const name = tool?.name;

  if (!result) {
    return '';
  }

  // 如果 result 已经被格式化为文本字符串，直接显示
  if (typeof result === 'string') {
    return body(preBox(result));
  }

  const subAgent = renderSubAgentResult(name, result, args);
  const inner =
    subAgent !== null
      ? subAgent
      : renderByTool(name, result, args, formatSearchTopic, formatSearchTime, formatSearchDomains);
  return body(inner + errorBlock(resultErrorText(result)));
}

function renderByTool(
  name: string,
  result: any,
  args: any,
  formatSearchTopic: (filters: Record<string, any>) => string,
  formatSearchTime: (filters: Record<string, any>) => string,
  formatSearchDomains: (filters: Record<string, any>) => string
): string {
  switch (name) {
    // 网络检索类
    case 'web_search':
      return renderWebSearch(
        result,
        args,
        formatSearchTopic,
        formatSearchTime,
        formatSearchDomains
      );
    case 'extract_webpage':
      return renderExtractWebpage(result, args);
    case 'save_webpage':
      return renderSaveWebpage(result, args);
    // 文件编辑类
    case 'create_file':
    case 'create_folder':
      return meta(param(L('path'), args.path || result.path), pendingStatus(result));
    case 'write_file':
      return renderWriteFile(result, args);
    case 'edit_file':
      return renderEditFile(result, args);
    case 'delete_file':
      return meta(param(L('path'), args.path || result.path), pendingStatus(result));
    case 'rename_file':
      return renderRenameFile(result, args);
    // 阅读聚焦 / Skills 类
    case 'read_file':
    case 'read_skill':
      return renderReadFile(result, args);
    case 'create_skill':
      return renderCreateSkill(result, args);
    case 'list_workflows':
      return renderListWorkflows(result, args);
    case 'save_workflow':
      return renderSaveWorkflow(result, args);
    case 'vlm_analyze':
      return renderVlmAnalyze(result, args);
    case 'ocr_image':
      return renderOcrImage(result, args);
    case 'view_image':
      return renderViewImage(result, args);
    // 终端类
    case 'terminal_session':
      return renderTerminalSession(result, args);
    case 'terminal_input':
      return renderTerminalInput(result, args);
    case 'terminal_snapshot':
      return renderTerminalSnapshot(result, args);
    case 'sleep':
      return renderSleep(result, args);
    case 'run_command':
      return renderRunCommand(result, args);
    // 记忆类
    case 'update_memory':
      return renderUpdateMemory(result, args);
    case 'recall_project_memory':
      return renderRecallProjectMemory(result, args);
    case 'search_project_memory':
      return renderSearchProjectMemory(result, args);
    case 'update_project_memory':
      return renderUpdateProjectMemory(result, args);
    case 'conversation_search':
      return renderConversationSearch(result, args);
    case 'conversation_review':
      return renderConversationReview(result, args);
    // 用户沟通 / 个性化 / 待办 / 彩蛋 / 动态加载
    case 'ask_user':
      return renderAskUser(result, args);
    case 'manage_personalization':
      return renderManagePersonalization(result, args);
    case 'todo_create':
      return renderTodoList(result, args, true);
    case 'todo_update_task':
      return renderTodoList(result, args, false);
    case 'trigger_easter_egg':
      return renderEasterEgg(result, args);
    case 'load_tools':
      return renderLoadTools(result, args);
    default:
      // 默认回退：显示重要参数 + 结果
      return renderDefaultResult(result, args);
  }
}

// 默认回退可识别的参数键 → 字段名（fields.* 不含冒号，拼接 values.colon）
const DEFAULT_PARAM_KEYS: Array<[string, string]> = [
  ['path', 'toolResults.fields.path'],
  ['file_path', 'toolResults.fields.filePath'],
  ['name', 'toolResults.fields.name'],
  ['question', 'toolResults.fields.question'],
  ['url', 'toolResults.fields.url']
];

function renderDefaultResult(result: any, args: any): string {
  const colon = t('toolResults.values.colon');
  const rows = DEFAULT_PARAM_KEYS.map(([key, labelKey]) =>
    param(t(labelKey) + colon, args?.[key] ?? result?.[key])
  );
  let html = meta(...rows, pendingStatus(result));

  const readable =
    result?.message ??
    result?.summary ??
    result?.answer ??
    result?.output ??
    result?.content ??
    result?.data ??
    '';
  if (typeof readable === 'string' && readable.trim()) {
    html += preBox(readable);
  } else if (readable && typeof readable === 'object') {
    // MCP 等未知工具常返回结构化数据，原样展示比「无内容」更有用
    html += preBox(JSON.stringify(readable, null, 2));
  } else if (result?.success) {
    html += empty(t('toolResults.sentences.emptyResult'));
  }
  return html;
}

// 网络检索类渲染函数
function renderWebSearch(
  result: any,
  args: any,
  formatSearchTopic: (filters: Record<string, any>) => string,
  formatSearchTime: (filters: Record<string, any>) => string,
  formatSearchDomains: (filters: Record<string, any>) => string
): string {
  const filters = result.filters || {};
  const results = Array.isArray(result.results) ? result.results : [];

  // 服务商来源（filters.provider 由后端 SearchEngine 写入；未知 id 原样显示）
  const KNOWN_SEARCH_PROVIDERS = ['tavily', 'bocha', 'exa', 'parallel', 'searxng'];
  const providerName =
    typeof filters.provider === 'string' && filters.provider
      ? KNOWN_SEARCH_PROVIDERS.includes(filters.provider)
        ? t(`toolResults.searchProviders.${filters.provider}`)
        : filters.provider
      : '';

  let html = meta(
    param(L('query'), result.query || args.query),
    param(L('provider'), providerName),
    param(L('topic'), formatSearchTopic(filters)),
    param(L('timeRange'), formatSearchTime(filters)),
    param(L('domains'), formatSearchDomains(filters))
  );

  if (!results.length) {
    return result.success ? html + empty(t('toolResults.sentences.noSearchResults')) : html;
  }
  html += summary(lv(L('resultCount'), result.total_results || results.length));
  const items = results
    .map((item: any) => {
      const link = item.url
        ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a>`
        : `<span>${escapeHtml(t('toolResults.values.noLink'))}</span>`;
      return `<div class="search-result-item"><div class="search-result-title">${escapeHtml(item.title || t('toolResults.values.noTitle'))}</div><div class="search-result-url">${link}</div></div>`;
    })
    .join('');
  return html + box(`<div class="search-result-list">${items}</div>`);
}

function renderExtractWebpage(result: any, args: any): string {
  return (
    meta(param(L('url'), args.url || result.url), pendingStatus(result)) +
    preBox(result.content || result.text)
  );
}

function renderSaveWebpage(result: any, args: any): string {
  return meta(
    param(L('url'), args.url || result.url),
    param(L('savePath'), args.save_path || result.path),
    pendingStatus(result)
  );
}

// 文件编辑类渲染函数
function renderWriteFile(result: any, args: any): string {
  const path = args.file_path || args.path || result.path;
  const appendFlag = typeof args.append === 'boolean' ? args.append : null;
  const modeRaw = String(result.mode || '').toLowerCase();
  const isAppend = appendFlag === true || modeRaw === 'a';
  const isOverwrite = appendFlag === false || modeRaw === 'w';
  const writeMode = isAppend
    ? t('toolResults.values.append')
    : isOverwrite
      ? t('toolResults.values.overwrite')
      : '';
  const content = isAppend ? args.content || '' : (result.new_file ?? args.content ?? '');

  let html = meta(param(L('path'), path), param(L('mode'), writeMode), pendingStatus(result));

  // 显示写入的内容
  if (result.success && content) {
    html += '<div class="tool-result-diff scrollable">';
    String(content)
      .split('\n')
      .forEach((line: string, idx: number) => {
        html += `<div class="diff-line diff-add"><span class="diff-line-number">${idx + 1}</span><span class="diff-marker">+</span><span class="diff-content">${escapeHtml(line)}</span></div>`;
      });
    html += '</div>';
  }
  return html;
}

function renderEditFile(result: any, args: any): string {
  const path = args.file_path || args.path || result.path;
  let html = meta(param(L('path'), path), pendingStatus(result));
  if (!result.success) {
    return html;
  }

  const details = Array.isArray(result.details)
    ? result.details.filter((d: any) => d && d.status === 'success')
    : [];
  const foundCount =
    typeof result.found_matches === 'number'
      ? result.found_matches
      : typeof result.replacements === 'number'
        ? result.replacements
        : null;
  const replacedCount = typeof result.replacements === 'number' ? result.replacements : null;
  const groups = result.replacement_groups ?? (details.length || null);

  // 替换统计属于执行结果，放在 content 侧摘要行（§5.6）
  html += summary(
    groups !== null ? lv(L('replacementGroups'), groups) : '',
    foundCount !== null
      ? lv(L('found'), t('toolResults.counts.foundValue', { n: foundCount }))
      : '',
    replacedCount !== null
      ? lv(L('replaced'), t('toolResults.counts.replacedValue', { n: replacedCount }))
      : ''
  );
  if (details.length > 0) {
    html += renderEditFileDiff(details);
  }
  return html;
}

function renderRenameFile(result: any, args: any): string {
  const oldPath = args.old_path || args.source || result.old_path || '';
  const newPath = args.new_path || args.destination || result.new_path || '';
  return meta(
    param(L('rename'), oldPath || newPath ? `${oldPath} → ${newPath}` : ''),
    pendingStatus(result)
  );
}

// 阅读聚焦类渲染函数
function renderReadFile(result: any, args: any): string {
  const readType = result.type || args.type || 'read';
  const size = result.size || result.file_size || 0;
  const query = result.query || args.query;

  let html = meta(
    param(L('path'), args.path || args.name || result.path),
    readType !== 'read' ? param(L('mode'), readType) : '',
    readType === 'search' ? param(L('searchKeyword'), query) : '',
    pendingStatus(result)
  );
  if (!result.success) return html;

  if (readType === 'read') {
    const content = result.content || result.text || '';
    html += summary(size > 0 ? lv(L('size'), formatBytes(size)) : '');
    html += preBox(content);
  } else if (readType === 'search') {
    const matches = Array.isArray(result.matches) ? result.matches : [];
    if (!matches.length) return html + empty(t('toolResults.sentences.noMatches'));
    html += summary(lv(L('matchCount'), matches.length));
    const items = matches
      .map((match: any) => {
        const lineLabel =
          match.line_start === match.line_end
            ? `${match.line_start}`
            : `${match.line_start}-${match.line_end}`;
        return `<div class="search-match-item"><div class="search-match-line">${escapeHtml(t('toolResults.values.line', { line: lineLabel }))}</div><pre>${escapeHtml(match.snippet || match.content || '')}</pre></div>`;
      })
      .join('<div class="search-match-separator">⋯</div>');
    html += box(items);
  } else if (readType === 'extract') {
    const segments = Array.isArray(result.segments) ? result.segments : [];
    if (!segments.length) return html + empty(t('toolResults.sentences.noExtractedContent'));
    html += summary(lv(L('segmentCount'), segments.length));
    const items = segments
      .map((segment: any) => {
        const startLine = segment.start_line || segment.line_start || '?';
        const endLine = segment.end_line || segment.line_end || '?';
        return `<div class="segment-item"><div class="segment-range">${escapeHtml(t('toolResults.values.line', { line: `${startLine}-${endLine}` }))}</div><pre>${escapeHtml(segment.content || segment.text || '')}</pre></div>`;
      })
      .join('<div class="segment-separator">⋯</div>');
    html += box(items);
  }
  return html;
}

function renderVlmAnalyze(result: any, args: any): string {
  return (
    meta(param(L('path'), args.image_path || args.path || result.path), pendingStatus(result)) +
    preBox(result.analysis || result.result, L('analysis'))
  );
}

function imageBlock(url: string, alt: string): string {
  if (!url) return '';
  // 直接渲染 <img>，由聊天消息区的事件委托统一承接点击灯箱预览
  return `<div class="tool-result-image"><img src="${escapeHtml(url)}" alt="${escapeHtml(alt)}" loading="lazy" /></div>`;
}

function renderOcrImage(result: any, args: any): string {
  const size = result.size || result.file_size || 0;
  return (
    meta(param(L('path'), args.image_path || args.path || result.path), pendingStatus(result)) +
    summary(size > 0 ? lv(L('size'), formatBytes(size)) : '') +
    imageBlock(result.image_url || result.url || '', t('toolResults.media.ocrImgAlt')) +
    preBox(result.text || result.ocr_text, L('ocrText'))
  );
}

function renderViewImage(result: any, args: any): string {
  const path = args.image_path || args.path || result.path || '';
  const size = result.size || result.file_size || 0;
  // 优先用结果自带 URL；否则按 path 走文件内容接口加载原图
  // （host 模式支持绝对路径，docker/web 模式限工作区相对路径，与工具本身的路径约束一致）
  let imageUrl = result.image_url || result.url || '';
  if (!imageUrl && path && result.success !== false) {
    imageUrl = withWorkspaceScope(`/api/file/content?path=${encodeURIComponent(path)}`);
  }
  return (
    meta(param(L('path'), path), pendingStatus(result)) +
    summary(size > 0 ? lv(L('size'), formatBytes(size)) : '') +
    imageBlock(imageUrl, t('toolResults.media.viewImgAlt'))
  );
}

// 终端类渲染函数
function terminalActionLabel(action: string): string {
  return ['open', 'close', 'list', 'reset'].includes(action)
    ? t(`toolResults.terminalActions.${action}`)
    : action;
}

function renderTerminalSession(result: any, args: any): string {
  const action = String(args.action || args.operation || '');
  let html = meta(
    param(L('operation'), terminalActionLabel(action)),
    param(L('terminalName'), args.session_name || result.session_name),
    param(L('workingDir'), args.working_dir),
    pendingStatus(result)
  );
  const sessions = Array.isArray(result.sessions) ? result.sessions : null;
  if (sessions) {
    if (!sessions.length) return html + empty(t('toolResults.sentences.noTerminals'));
    const lines = sessions.map((session: any) => {
      const active = session.is_active ? ` (${t('toolResults.values.activeMark')})` : '';
      const dir = session.working_dir ? `  ${session.working_dir}` : '';
      return `${session.session_name || ''}${active}${dir}`;
    });
    html += preBox(lines.join('\n'));
  } else if (result.success && typeof result.message === 'string') {
    html += summary(result.message);
  }
  return html;
}

function renderTerminalInput(result: any, args: any): string {
  return (
    meta(
      param(L('terminalName'), args.session_name),
      param(L('command'), args.command || args.input, { mono: true }),
      param(L('waitTime'), seconds(args.output_wait)),
      pendingStatus(result)
    ) + preBox(result.output || result.result)
  );
}

function renderTerminalSnapshot(result: any, args: any): string {
  const startLine = args.start_line || 0;
  const endLine = args.end_line || 0;
  return (
    meta(
      param(L('terminalName'), args.session_name || args.name || result.session_name),
      param(L('lineRange'), startLine || endLine ? `${startLine} - ${endLine}` : ''),
      pendingStatus(result)
    ) + preBox(result.content || result.output)
  );
}

function renderSleep(result: any, args: any): string {
  const waitId = args.wait_runcommand_id ?? result.command_id;
  const isWaitCommand = result?.mode === 'wait_runcommand_id' || Boolean(args.wait_runcommand_id);
  let html = meta(
    isWaitCommand
      ? param(L('backgroundCommandId'), waitId)
      : param(L('waitTime'), seconds(args.seconds)),
    param(L('reason'), args.reason),
    pendingStatus(result)
  );
  if (!result.success) return html;
  const nested = result.result;
  if (isWaitCommand && nested && typeof nested === 'object') {
    const exitCode = nested.return_code ?? nested.exit_code;
    html += summary(exitCode !== undefined ? lv(L('exitCode'), exitCode) : '');
    html += nested.output
      ? preBox(nested.output, L('backgroundCommandOutput'))
      : empty(t('toolResults.sentences.noOutput'));
  } else if (typeof result.message === 'string' && result.message.trim()) {
    html += result.message.includes('\n') ? preBox(result.message) : summary(result.message);
  }
  return html;
}

function renderRunCommand(result: any, args: any): string {
  const output = result.output || result.stdout || '';
  const exitCode = result.exit_code !== undefined ? result.exit_code : result.returncode;
  const fullAccess =
    args.request_full_access === true ? args.full_access_reason || t('toolResults.values.yes') : '';

  let html = meta(
    param(L('command'), args.command, { mono: true }),
    param(L('workingDir'), args.working_dir),
    param(L('timeout'), seconds(args.timeout)),
    args.run_in_background === true ? param(L('background'), yesNo(true)) : '',
    param(L('fullAccessRequest'), fullAccess),
    pendingStatus(result)
  );

  const authorityKey = result.full_access_granted
    ? 'toolResults.access.onceGranted'
    : result.full_access_already_active
      ? 'toolResults.access.alreadyActive'
      : '';
  html += summary(
    authorityKey ? t(authorityKey) : '',
    exitCode !== undefined && exitCode !== null ? lv(L('exitCode'), exitCode) : '',
    result.command_id ? lv(L('backgroundCommandId'), result.command_id) : ''
  );
  html += preBox(output);
  return html;
}

// 记忆类渲染函数
function renderUpdateMemory(result: any, args: any): string {
  const operation = args.operation || result.operation || '';
  const index = args.index || result.index;
  const sentenceKey =
    operation === 'append'
      ? 'memoryAdd'
      : operation === 'replace'
        ? 'memoryUpdate'
        : operation === 'delete'
          ? 'memoryDelete'
          : '';
  const position = operation === 'append' ? result.count : index;
  const content = args.content || (operation === 'delete' ? t('toolResults.values.deleted') : '');
  return meta(
    param(L('operation'), sentenceKey ? t(`toolResults.sentences.${sentenceKey}`) : operation),
    param(L('memoryContent'), content ? `${position ? `${position}. ` : ''}${content}` : ''),
    pendingStatus(result)
  );
}

function renderRecallProjectMemory(result: any, args: any): string {
  return (
    meta(param(L('memory'), args.name || result.memory_name), pendingStatus(result)) +
    (result.success ? preBox(result.content || result.text) : '')
  );
}

function renderSearchProjectMemory(result: any, args: any): string {
  const keywords = Array.isArray(result?.keywords)
    ? result.keywords
    : Array.isArray(args?.keywords)
      ? args.keywords
      : [];
  const results = Array.isArray(result?.results) ? result.results : [];

  let html = meta(
    param(L('keywords'), keywords.join(' / ') || t('toolResults.values.notSpecified')),
    pendingStatus(result)
  );
  if (!result?.success) return html;
  if (!results.length) return html + empty(t('toolResults.sentences.noMatchesMemory'));

  html += summary(t('toolResults.counts.memoryHits', { n: results.length }));
  const items = results
    .map((item: any) => {
      let entry = `<div class="search-result-item"><div class="search-result-title">${escapeHtml(item.name || item.file || '')}</div>`;
      if (item.description) {
        entry += `<div class="search-result-detail">${escapeHtml(item.description)}</div>`;
      }
      const snippets = Array.isArray(item.snippets) ? item.snippets : [];
      // 每条匹配片段独立成行，过长直接省略号截断
      snippets.forEach((snippet: any) => {
        entry += `<div class="search-match-snippet">${escapeHtml(`L${snippet.line ?? '?'}: ${snippet.text || ''}`)}</div>`;
      });
      return entry + '</div>';
    })
    .join('');
  return html + box(`<div class="search-result-list">${items}</div>`);
}

function renderUpdateProjectMemory(result: any, args: any): string {
  return (
    meta(
      param(L('memory'), args.name || result.memory_name),
      param(L('description'), args.description),
      pendingStatus(result)
    ) + (result.success ? preBox(args.content, L('memoryContent')) : '')
  );
}

function renderConversationSearch(result: any, args: any): string {
  const results = Array.isArray(result?.results) ? result.results : [];
  const keywords = Array.isArray(result?.keywords)
    ? result.keywords
    : Array.isArray(args?.keywords)
      ? args.keywords
      : [];
  const keywordText = keywords.length
    ? keywords.join(' / ')
    : result?.query || args?.query || t('toolResults.values.notSpecified');
  const unlimited = t('toolResults.values.unlimited');
  const startDate = result?.start_date || args?.start_date;
  const endDate = result?.end_date || args?.end_date;

  let html = meta(
    param(L('keywords'), keywordText),
    startDate || endDate
      ? param(L('dateRange'), `${startDate || unlimited} ~ ${endDate || unlimited}`)
      : '',
    pendingStatus(result)
  );
  if (!result?.success) return html;
  if (!results.length) return html + empty(t('toolResults.sentences.noMatchesConversation'));

  html += summary(lv(L('resultCount'), results.length));
  const items = results
    .map((item: any) => {
      const details = [
        lv(L('id'), item.id || ''),
        t('toolResults.counts.conversationScale', {
          n: String(item.total_messages || 0),
          m: String(item.total_tools || 0)
        }),
        item.updated_at ? lv(L('updatedAt'), item.updated_at) : ''
      ].filter(Boolean);
      let entry = `<div class="search-result-item"><div class="search-result-title">${escapeHtml(item.title || t('toolResults.values.unnamedConversation'))}</div>`;
      entry += `<div class="search-result-detail">${details.map(escapeHtml).join(' · ')}</div>`;
      if (item.first_user_message) {
        entry += `<div class="search-result-detail search-result-detail--clamp">${escapeHtml(lv(L('firstUserMessage'), item.first_user_message))}</div>`;
      }
      return entry + '</div>';
    })
    .join('');
  return html + box(`<div class="search-result-list">${items}</div>`);
}

function renderConversationReview(result: any, args: any): string {
  const reviewContentMode = result?.content_mode || args?.content_mode || '';
  const contentModeLabel = reviewContentMode
    ? reviewContentMode === 'full'
      ? t('toolResults.values.reviewContentModeFull')
      : t('toolResults.values.reviewContentModeDialogue')
    : '';
  let html = meta(
    param(L('conversationId'), args?.conversation_id || result?.conversation_id),
    param(L('mode'), args?.mode || result?.mode),
    param(L('contentMode'), contentModeLabel),
    pendingStatus(result)
  );
  if (!result?.success) return html;
  html += summary(
    result.title ? lv(L('title'), result.title) : '',
    result.path ? lv(L('reviewFile'), result.path) : '',
    result.char_count !== undefined ? lv(L('charCount'), result.char_count) : ''
  );
  if (result.too_long && result.path) {
    html += warning(t('toolResults.sentences.tooLongSaved'));
  }
  return html + preBox(result.content);
}

function renderCreateSkill(result: any, args: any): string {
  return (
    meta(
      param(L('skill'), result?.skill_name || args?.name),
      param(L('sourceDir'), args?.source_dir),
      pendingStatus(result)
    ) + warning(result?.sync_warning)
  );
}

function renderListWorkflows(result: any, args: any): string {
  const detailName = String(args?.name || result?.workflow_name || '');
  if (!result?.success) {
    // 有 error 时由外层统一追加；没有任何原因时给出兜底文案
    return (
      meta(param(L('workflow'), detailName)) +
      (result?.error ? '' : errorBlock(t('toolResults.sentences.queryFailed')))
    );
  }

  // name 形态：展示该工作流的完整定义文档
  if (detailName) {
    return meta(param(L('workflow'), detailName)) + preBox(result?.message);
  }

  // 列表形态
  const items = Array.isArray(result?.workflows) ? result.workflows : [];
  if (!items.length) return empty(t('toolResults.sentences.noWorkflows'));
  const list = items
    .map((item: any) => {
      const source =
        item?.source === 'builtin' ? t('toolResults.values.builtin') : t('toolResults.values.user');
      const detail = `${lv(L('source'), source)} · ${lv(L('nodeCount'), Number(item?.nodeCount || 0))}`;
      return `<div class="search-result-item"><div class="search-result-title">${escapeHtml(item?.name || t('toolResults.values.unnamed'))}</div><div class="search-result-detail">${escapeHtml(item?.description || t('toolResults.values.noDescription'))}</div><div class="search-result-detail">${escapeHtml(detail)}</div></div>`;
    })
    .join('');
  return (
    summary(lv(L('workflowCount'), items.length)) +
    box(`<div class="search-result-list">${list}</div>`)
  );
}

function renderSaveWorkflow(result: any, args: any): string {
  let html = meta(param(L('sourceDir'), args?.source_dir), pendingStatus(result));
  if (!result?.success) return html;
  const modeText = result.overwritten
    ? t('toolResults.sentences.overwrittenVersion')
    : result.shadows_builtin
      ? t('toolResults.sentences.userCopyShadow')
      : '';
  html += summary(
    result.workflow_name ? lv(L('workflow'), result.workflow_name) : '',
    typeof result.node_count === 'number' ? lv(L('nodeCountTotal'), result.node_count) : '',
    modeText
  );
  if (result.summary) {
    html += `<div class="tool-result-text">${escapeHtml(String(result.summary))}</div>`;
  }
  return html;
}

// 个性化字段名 → i18n key（函数内 t() 按调用时求值）
const personalizationFieldLabelKeys: Record<string, string> = {
  self_identify: 'toolResults.personalization.fieldLabels.selfIdentify',
  user_name: 'toolResults.personalization.fieldLabels.userName',
  profession: 'toolResults.personalization.fieldLabels.profession',
  tone: 'toolResults.personalization.fieldLabels.tone',
  considerations: 'toolResults.personalization.fieldLabels.considerations',
  theme: 'toolResults.personalization.fieldLabels.theme',
  communication_style: 'toolResults.personalization.fieldLabels.communicationStyle',
  conversation_continuity: 'toolResults.personalization.fieldLabels.conversationContinuity',
  enabled: 'toolResults.personalization.fieldLabels.enabled'
};

function formatPersonalizationFieldLabel(field: string): string {
  const key = personalizationFieldLabelKeys[field];
  return key ? t(key) : field;
}

function formatPersonalizationFieldValue(field: string, value: any): string {
  if (value === null || value === undefined || value === '') {
    return t('common.unset');
  }
  if (field === 'enabled') {
    return value
      ? t('toolResults.personalization.values.enabled')
      : t('toolResults.personalization.values.disabled');
  }
  if (field === 'considerations') {
    if (typeof value !== 'string' || value.trim() === '') {
      return t('common.unset');
    }
    return value.trim();
  }
  if (field === 'communication_style') {
    const styleMap: Record<string, string> = {
      default: t('toolResults.personalization.values.styleDefault'),
      human_like: t('toolResults.personalization.values.styleHumanLike'),
      auto: t('toolResults.personalization.values.styleAuto')
    };
    return styleMap[String(value)] || String(value);
  }
  if (field === 'conversation_continuity') {
    const independenceMap: Record<string, string> = {
      low: t('toolResults.personalization.values.independenceLow'),
      medium: t('toolResults.personalization.values.independenceMedium'),
      high: t('toolResults.personalization.values.independenceHigh')
    };
    return independenceMap[String(value)] || String(value);
  }
  return String(value);
}

function renderAskUser(result: any, args: any): string {
  const options = Array.isArray(args.options) ? args.options : [];
  const optionText = options
    .map((option: any, idx: number) => {
      const label = String(
        option?.label || option?.id || t('toolResults.values.optionLabel', { n: idx + 1 })
      );
      const desc = String(option?.description || '').trim();
      return `${idx + 1}. ${label}${desc ? ` — ${desc}` : ''}`;
    })
    .join('\n');

  let html = meta(
    param(L('question'), args.question || result.question),
    param(L('context'), args.context || result.context),
    param(L('providedOptions'), optionText, { block: true }),
    pendingStatus(result)
  );

  const answerText =
    result.status === 'answered' ? String(result.answer_text || result.message || '').trim() : '';
  if (answerText) {
    const rows = answerText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const separator = line.indexOf('：');
        return separator > 0
          ? param(line.slice(0, separator + 1), line.slice(separator + 1))
          : param(L('userAnswer'), line);
      })
      .join('');
    html += `<div class="tool-result-kv">${rows}</div>`;
  }
  return html;
}

function renderManagePersonalization(result: any, args: any): string {
  const action = String(args.action || result.action || 'read');
  const field = String(result.updated_field || args.field || '');
  const isUpdate = action === 'update';
  let html = meta(
    param(
      L('operation'),
      isUpdate
        ? t('toolResults.personalization.actionUpdate')
        : t('toolResults.personalization.actionRead')
    ),
    isUpdate && field ? param(L('updatedField'), formatPersonalizationFieldLabel(field)) : '',
    isUpdate && field
      ? param(
          L('updatedValue'),
          formatPersonalizationFieldValue(field, result.updated_value ?? args.value)
        )
      : '',
    pendingStatus(result)
  );
  if (!result?.success && Array.isArray(result?.validation_errors)) {
    html += errorBlock(result.validation_errors.join('；'));
  }
  return html;
}

// 待办事项类渲染函数（复选标记为自绘元素，不使用原生 checkbox）
function renderTodoList(result: any, args: any, isCreate: boolean): string {
  const todoList = result.todo_list || {};
  const title = todoList.title || (isCreate ? args.title : '') || '';
  const tasks = todoList.tasks || (isCreate ? args.tasks : null) || [];

  let html = meta(param(L('title'), title), pendingStatus(result));
  if (!tasks.length) return html;
  html += '<div class="todo-list">';
  tasks.forEach((task: any) => {
    const taskText =
      typeof task === 'string' ? task : task.text || task.title || task.content || '';
    const completed =
      typeof task === 'object' &&
      (task.status === 'done' || task.completed || task.done || task.checked);
    html += `<div class="todo-item${completed ? ' is-done' : ''}"><span class="todo-check" aria-hidden="true"></span><span class="todo-text">${escapeHtml(taskText)}</span></div>`;
  });
  return html + '</div>';
}

// 彩蛋类渲染函数
function renderEasterEgg(result: any, args: any): string {
  const content = result.content || result.message || '';
  return (
    meta(param(L('type'), result.type || args.type), pendingStatus(result)) +
    (content ? `<div class="easter-egg-content">${escapeHtml(content)}</div>` : '')
  );
}

// 工具动态加载
function renderLoadTools(result: any, args: any): string {
  const requested: string[] = Array.isArray(args?.tool_names) ? args.tool_names : [];
  const loadedNow: string[] = Array.isArray(result?.loaded_now) ? result.loaded_now : [];
  const already: string[] = Array.isArray(result?.already_loaded) ? result.already_loaded : [];
  const unavailable: string[] = Array.isArray(result?.unavailable) ? result.unavailable : [];
  const toolDefs: any[] = Array.isArray(result?.tools) ? result.tools : [];

  let html = meta(param(L('requestedTools'), requested.join(', ')), pendingStatus(result));
  if (!result?.success) return html;

  html += summary(
    loadedNow.length ? lv(L('loadedNow'), loadedNow.join(', ')) : '',
    already.length ? lv(L('alreadyLoaded'), already.join(', ')) : '',
    unavailable.length ? lv(L('unavailableTools'), unavailable.join(', ')) : ''
  );
  if (toolDefs.length) {
    const rows = toolDefs
      .map((def) => {
        const fn = def?.function || {};
        // 只展示首行描述，完整 JSON 定义留给模型上下文，不上界面
        const desc = String(fn.description || '').split('\n')[0];
        return `<div class="search-result-item"><div class="search-result-title">${escapeHtml(fn.name || '')}</div>${desc ? `<div class="search-result-detail">${escapeHtml(desc)}</div>` : ''}</div>`;
      })
      .join('');
    html += box(`<div class="search-result-list">${rows}</div>`);
  }
  return html;
}
