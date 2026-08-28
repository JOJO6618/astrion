// 工具增强显示的渲染函数

import { t } from '@/locales';

export function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

function formatDuration(seconds: number): string {
  if (seconds <= 0) return t('toolResults.duration.zero');
  if (seconds < 60) return t('toolResults.duration.seconds', { seconds });
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) {
    return remainingSeconds > 0
      ? t('toolResults.duration.minutesSeconds', { minutes, seconds: remainingSeconds })
      : t('toolResults.duration.minutes', { minutes });
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  if (remainingMinutes === 0 && remainingSeconds === 0) {
    return t('toolResults.duration.hours', { hours });
  }
  return t('toolResults.duration.hoursMinutesSeconds', {
    hours,
    minutes: remainingMinutes,
    seconds: remainingSeconds,
  });
}

function formatToolStatusLabel(
  result: any,
  successLabel: string,
  failedLabel = t('toolResults.status.failedMark')
): string {
  const state = String(result?.status || '').toLowerCase();
  if (state === 'awaiting_user_answer') {
    return t('toolResults.status.awaitingUserAnswer');
  }
  if (state === 'awaiting_approval' || state === 'pending_approval' || state === 'pending') {
    return t('toolResults.status.awaitingApproval');
  }
  if (state === 'rejected') {
    return t('toolResults.status.rejected');
  }
  if (state === 'cancelled') {
    return t('toolResults.status.cancelled');
  }
  return result?.success ? successLabel : failedLabel;
}

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
    return `<div class="tool-result-content scrollable"><pre>${escapeHtml(result)}</pre></div>`;
  }

  // 网络检索类
  if (name === 'web_search') {
    return renderWebSearch(result, args, formatSearchTopic, formatSearchTime, formatSearchDomains);
  } else if (name === 'extract_webpage') {
    return renderExtractWebpage(result, args);
  } else if (name === 'save_webpage') {
    return renderSaveWebpage(result, args);
  }

  // 文件编辑类
  else if (name === 'create_file' || name === 'create_folder') {
    return renderCreateFile(result, args);
  } else if (name === 'write_file') {
    return renderWriteFile(result, args);
  } else if (name === 'edit_file') {
    return renderEditFile(result, args);
  } else if (name === 'delete_file') {
    return renderDeleteFile(result, args);
  } else if (name === 'rename_file') {
    return renderRenameFile(result, args);
  }

  // 阅读聚焦 / Skills 类
  else if (name === 'read_file' || name === 'read_skill') {
    return renderReadFile(result, args);
  } else if (name === 'create_skill') {
    return renderCreateSkill(result);
  } else if (name === 'list_workflows') {
    return renderListWorkflows(result, args);
  } else if (name === 'save_workflow') {
    return renderSaveWorkflow(result, args);
  } else if (name === 'vlm_analyze') {
    return renderVlmAnalyze(result, args);
  } else if (name === 'ocr_image') {
    return renderOcrImage(result, args);
  } else if (name === 'view_image') {
    return renderViewImage(result, args);
  }

  // 终端类
  else if (name === 'terminal_session') {
    return renderTerminalSession(result, args);
  } else if (name === 'terminal_input') {
    return renderTerminalInput(result, args);
  } else if (name === 'terminal_snapshot') {
    return renderTerminalSnapshot(result, args);
  } else if (name === 'sleep') {
    return renderSleep(result, args);
  }

  // 终端指令类
  else if (name === 'run_command') {
    return renderRunCommand(result, args);
  }

  // 记忆类
  else if (name === 'update_memory') {
    return renderUpdateMemory(result, args);
  } else if (name === 'recall_project_memory') {
    return renderRecallProjectMemory(result, args);
  } else if (name === 'search_project_memory') {
    return renderSearchProjectMemory(result, args);
  } else if (name === 'update_project_memory') {
    return renderUpdateProjectMemory(result, args);
  } else if (name === 'conversation_search') {
    return renderConversationSearch(result, args);
  } else if (name === 'conversation_review') {
    return renderConversationReview(result, args);
  }
  // 用户沟通类
  else if (name === 'ask_user') {
    return renderAskUser(result, args);
  }

  // 个性化管理类
  else if (name === 'manage_personalization') {
    return renderManagePersonalization(result, args);
  }

  // 待办事项类
  else if (name === 'todo_create') {
    return renderTodoCreate(result, args);
  } else if (name === 'todo_update_task') {
    return renderTodoUpdate(result);
  }

  // 彩蛋类
  else if (name === 'trigger_easter_egg') {
    return renderEasterEgg(result, args);
  }

  // 子智能体类
  else if (name === 'create_sub_agent') {
    return renderCreateSubAgent(result, args);
  } else if (name === 'terminate_sub_agent') {
    return renderTerminateSubAgent(result, args);
  } else if (name === 'send_message_to_sub_agent') {
    return renderSendMessageToSubAgent(result, args);
  } else if (name === 'answer_sub_agent_question') {
    return renderAnswerSubAgentQuestion(result, args);
  } else if (name === 'create_custom_agent') {
    return renderCreateCustomAgent(result, args);
  } else if (name === 'list_agents') {
    return renderListAgents(result);
  } else if (name === 'list_active_sub_agents') {
    return renderListActiveSubAgents(result);
  } else if (name === 'get_sub_agent_status') {
    return renderGetSubAgentStatus(result);
  }

  // 默认回退：显示重要参数 + 结果摘要，不再直接返回空字符串
  return renderDefaultResult(result, args, name);
}

function renderDefaultResult(result: any, args: any, name: string): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.completed'), t('toolResults.status.failedMark'));
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.tool'))}</strong>${escapeHtml(name)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(status)}</div>`;

  const importantKeys = [
    'path',
    'file_path',
    'agent_id',
    'target_agent_id',
    'display_name',
    'target_display_name',
    'question',
    'role_id',
    'url'
  ];
  importantKeys.forEach((key) => {
    const value = args?.[key] ?? result?.[key];
    if (value !== undefined && value !== '' && value !== null) {
      // 结构性字段名映射：值为完整 i18n key（含冒号语义，见 fields.* 与 values.colon）
      const fieldLabelKeys: Record<string, string> = {
        path: 'toolResults.fields.path',
        file_path: 'toolResults.fields.filePath',
        agent_id: 'toolResults.fields.agentId',
        target_agent_id: 'toolResults.fields.targetAgentId',
        display_name: 'toolResults.fields.displayName',
        target_display_name: 'toolResults.fields.targetDisplayName',
        question: 'toolResults.fields.question',
        role_id: 'toolResults.fields.roleId',
        url: 'toolResults.fields.url',
      };
      const displayValue = typeof value === 'string' ? value : JSON.stringify(value);
      const preview = String(displayValue).slice(0, 200);
      const suffix = String(displayValue).length > 200 ? '…' : '';
      const colon = t('toolResults.values.colon');
      const label = fieldLabelKeys[key]
        ? t(fieldLabelKeys[key]) + colon
        : key + colon;
      html += `<div><strong>${escapeHtml(label)}</strong>${escapeHtml(preview + suffix)}</div>`;
    }
  });

  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  const readable =
    result?.message ??
    result?.summary ??
    result?.answer ??
    result?.output ??
    result?.content ??
    result?.data ??
    '';
  if (readable && typeof readable === 'string' && readable.trim()) {
    html += '<div class="tool-result-content scrollable">';
    html += `<pre>${escapeHtml(readable.trim())}</pre>`;
    html += '</div>';
  } else if (result?.success) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.emptyResult'))}</div>`;
    html += '</div>';
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
  const query = result.query || args.query || '';
  const filters = result.filters || {};
  const totalResults = result.total_results || 0;
  const results = result.results || [];

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.query'))}</strong>${escapeHtml(query)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.topic'))}</strong>${escapeHtml(formatSearchTopic(filters))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.timeRange'))}</strong>${escapeHtml(formatSearchTime(filters))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.domains'))}</strong>${escapeHtml(formatSearchDomains(filters))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.resultCount'))}</strong>${totalResults}</div>`;
  html += '</div>';

  if (results.length > 0) {
    html += '<div class="search-result-list">';
    results.forEach((item: any) => {
      html += '<div class="search-result-item">';
      html += `<div class="search-result-title">${escapeHtml(item.title || t('toolResults.values.noTitle'))}</div>`;
      html += '<div class="search-result-url">';
      if (item.url) {
        html += `<a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.url)}</a>`;
      } else {
        html += `<span>${escapeHtml(t('toolResults.values.noLink'))}</span>`;
      }
      html += '</div></div>';
    });
    html += '</div>';
  } else {
    html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noSearchResults'))}</div>`;
  }

  return html;
}

function renderExtractWebpage(result: any, args: any): string {
  const url = args.url || result.url || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const content = result.content || result.text || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.url'))}</strong>${escapeHtml(url)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  if (content) {
    html += '<div class="tool-result-content scrollable">';
    html += `<pre>${escapeHtml(content)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderSaveWebpage(result: any, args: any): string {
  const url = args.url || result.url || '';
  const path = result.path || args.save_path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.saved'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.url'))}</strong>${escapeHtml(url)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.savePath'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  return html;
}

// 文件编辑类渲染函数
function renderCreateFile(result: any, args: any): string {
  const path = args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.created'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  return html;
}

function renderWriteFile(result: any, args: any): string {
  const path = args.file_path || args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.written'));
  const appendFlag = typeof args.append === 'boolean' ? args.append : null;
  const modeRaw = String(result.mode || '').toLowerCase();
  const writeMode =
    appendFlag === true || modeRaw === 'a'
      ? t('toolResults.values.append')
      : appendFlag === false || modeRaw === 'w'
        ? t('toolResults.values.overwrite')
        : t('toolResults.values.none');
  const content =
    appendFlag === true || modeRaw === 'a'
      ? args.content || ''
      : (result.new_file ?? args.content ?? '');

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${writeMode}</div>`;
  if (!result.success && result.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  // 显示写入的内容
  if (result.success && content) {
    html += '<div class="tool-result-diff scrollable">';
    const lines = content.split('\n');
    lines.forEach((line: string, idx: number) => {
      html += `<div class="diff-line diff-add"><span class="diff-line-number">${idx + 1}</span><span class="diff-marker">+</span><span class="diff-content">${escapeHtml(line)}</span></div>`;
    });
    html += '</div>';
  }

  return html;
}

function renderEditFile(result: any, args: any): string {
  const path = args.file_path || args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.edited'));
  const foundCount =
    typeof result.found_matches === 'number'
      ? result.found_matches
      : typeof result.replacements === 'number'
        ? result.replacements
        : null;
  const replacedCount = typeof result.replacements === 'number' ? result.replacements : null;

  const details = Array.isArray(result.details)
    ? result.details.filter((d: any) => d && d.status === 'success')
    : [];

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.replacementGroups'))}</strong>${escapeHtml((result.replacement_groups ?? details.length) || t('toolResults.values.none'))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.found'))}</strong>${escapeHtml(t('toolResults.counts.foundValue', { n: foundCount ?? t('toolResults.values.none') }))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.replaced'))}</strong>${escapeHtml(t('toolResults.counts.replacedValue', { n: replacedCount ?? t('toolResults.values.none') }))}</div>`;
  if (!result.success && result.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  if (!result.success) {
    return html;
  }

  if (details.length > 0) {
    html += '<div class="tool-result-diff scrollable">';
    const renderDiffLine = (
      lineNo: number | null,
      marker: string,
      text: string,
      className = ''
    ) => {
      const lineNoText =
        typeof lineNo === 'number' && Number.isFinite(lineNo) ? String(lineNo) : '';
      html += `<div class="diff-line${className ? ` ${className}` : ''}"><span class="diff-line-number">${escapeHtml(lineNoText)}</span><span class="diff-marker">${escapeHtml(marker)}</span><span class="diff-content">${escapeHtml(text)}</span></div>`;
    };
    const hunks: any[] = [];
    details.forEach((detail: any) => {
      const oldText = detail.old_string || '';
      const newText = detail.new_string || '';
      const contexts = Array.isArray(detail.contexts) ? detail.contexts : [];
      const oldLines = oldText ? oldText.split('\n') : [];
      const newLines = newText ? newText.split('\n') : [];
      const buildPairRows = (context?: any) => {
        const rows: any[] = [];
        const startLineCandidate = Number(context?.old_start_line ?? detail.matched_lines?.[0]);
        const startLine = Number.isFinite(startLineCandidate) ? startLineCandidate : null;
        if (oldText) {
          oldLines.forEach((line: string, lineIdx: number) => {
            rows.push({
              lineNo: startLine === null ? null : startLine + lineIdx,
              marker: '-',
              text: line,
              className: 'diff-remove'
            });
          });
        }

        if (newText) {
          newLines.forEach((line: string, lineIdx: number) => {
            rows.push({
              lineNo: startLine === null ? null : startLine + lineIdx,
              marker: '+',
              text: line,
              className: 'diff-add'
            });
          });
        }
        return rows;
      };

      if (contexts.length > 0) {
        contexts.forEach((context: any) => {
          const rows: any[] = [];
          const rawBeforeLines = Array.isArray(context.before_lines) ? context.before_lines : [];
          const rawAfterLines = Array.isArray(context.after_lines) ? context.after_lines : [];
          const beforeLines =
            rawBeforeLines.length >= 2 &&
            rawBeforeLines.every((item: any) => String(item?.text ?? '') === '')
              ? []
              : rawBeforeLines.length >= 2 && String(rawBeforeLines[0]?.text ?? '') === ''
                ? rawBeforeLines.slice(1)
                : rawBeforeLines;
          const afterLines =
            rawAfterLines.length >= 2 &&
            rawAfterLines.every((item: any) => String(item?.text ?? '') === '')
              ? []
              : rawAfterLines.length >= 2 &&
                  String(rawAfterLines[rawAfterLines.length - 1]?.text ?? '') === ''
                ? rawAfterLines.slice(0, -1)
                : rawAfterLines;

          const contextOldStartLine = Number(context?.old_start_line ?? detail.matched_lines?.[0]);
          const contextOldEndLine = Number(context?.old_end_line ?? contextOldStartLine);
          const oldLineCount =
            Number.isFinite(contextOldStartLine) && Number.isFinite(contextOldEndLine)
              ? Math.max(1, contextOldEndLine - contextOldStartLine + 1)
              : Math.max(1, oldText ? oldText.split('\n').length : 1);
          const newLineCount = newText === '' ? 0 : newText.split('\n').length;
          const afterContextOffset = newLineCount - oldLineCount;

          beforeLines.forEach((item: any) => {
            rows.push({
              lineNo: Number(item.line),
              marker: ' ',
              text: String(item.text ?? ''),
              className: ''
            });
          });
          rows.push(...buildPairRows(context));
          afterLines.forEach((item: any) => {
            const rawLineNo = Number(item.line);
            const lineNo = Number.isFinite(rawLineNo) ? rawLineNo + afterContextOffset : rawLineNo;
            rows.push({ lineNo, marker: ' ', text: String(item.text ?? ''), className: '' });
          });
          const numberedRows = rows.filter(
            (row) => typeof row.lineNo === 'number' && Number.isFinite(row.lineNo)
          );
          hunks.push({
            rows,
            minLine: numberedRows.length
              ? Math.min(...numberedRows.map((row) => row.lineNo))
              : null,
            maxLine: numberedRows.length ? Math.max(...numberedRows.map((row) => row.lineNo)) : null
          });
        });
      } else {
        const rows = buildPairRows();
        const numberedRows = rows.filter(
          (row) => typeof row.lineNo === 'number' && Number.isFinite(row.lineNo)
        );
        hunks.push({
          rows,
          minLine: numberedRows.length ? Math.min(...numberedRows.map((row) => row.lineNo)) : null,
          maxLine: numberedRows.length ? Math.max(...numberedRows.map((row) => row.lineNo)) : null
        });
      }
    });
    const sortedHunks = hunks.sort(
      (a, b) => (a.minLine ?? Number.MAX_SAFE_INTEGER) - (b.minLine ?? Number.MAX_SAFE_INTEGER)
    );
    const mergedHunks: any[] = [];
    sortedHunks.forEach((hunk) => {
      const last = mergedHunks[mergedHunks.length - 1];
      if (
        last &&
        hunk.minLine !== null &&
        last.maxLine !== null &&
        hunk.minLine <= last.maxLine + 1
      ) {
        last.rows.push(...hunk.rows);
        last.maxLine = Math.max(last.maxLine, hunk.maxLine ?? last.maxLine);
      } else {
        mergedHunks.push({ ...hunk, rows: [...hunk.rows] });
      }
    });
    const markerOrder: Record<string, number> = { ' ': 0, '-': 1, '+': 2 };
    mergedHunks.forEach((hunk, hunkIdx) => {
      if (hunkIdx > 0) renderDiffLine(null, ' ', '⋮', 'diff-separator');
      const seenContextLines = new Set<number>();
      const filteredRows = hunk.rows
        .filter((row: any) => {
          if (row.marker !== ' ' || typeof row.lineNo !== 'number') return true;
          if (seenContextLines.has(row.lineNo)) return false;
          seenContextLines.add(row.lineNo);
          return true;
        })
        .sort((a: any, b: any) => {
          const aIsChange = a.marker === '-' || a.marker === '+';
          const bIsChange = b.marker === '-' || b.marker === '+';
          // 仅变更行之间优先按 marker 分组（- 在前 + 在后），再按行号
          if (aIsChange && bIsChange) {
            const markerDelta = (markerOrder[a.marker] ?? 9) - (markerOrder[b.marker] ?? 9);
            return (
              markerDelta ||
              (a.lineNo ?? Number.MAX_SAFE_INTEGER) - (b.lineNo ?? Number.MAX_SAFE_INTEGER)
            );
          }
          // 上下文行或跨类型：按行号优先
          const lineDelta =
            (a.lineNo ?? Number.MAX_SAFE_INTEGER) - (b.lineNo ?? Number.MAX_SAFE_INTEGER);
          return lineDelta || (markerOrder[a.marker] ?? 9) - (markerOrder[b.marker] ?? 9);
        });
      filteredRows.forEach((row: any) =>
        renderDiffLine(row.lineNo, row.marker, row.text, row.className)
      );
    });
    html += '</div>';
  }

  return html;
}

function renderDeleteFile(result: any, args: any): string {
  const path = args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.deleted'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  return html;
}

function renderRenameFile(result: any, args: any): string {
  const oldPath = args.old_path || args.source || result.old_path || '';
  const newPath = args.new_path || args.destination || result.new_path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.renamed'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(oldPath)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.rename'))}</strong>${escapeHtml(oldPath)} → ${escapeHtml(newPath)}</div>`;
  html += '</div>';

  return html;
}

// 阅读聚焦类渲染函数
function renderReadFile(result: any, args: any): string {
  const path = args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const size = result.size || result.file_size || 0;
  const readType = result.type || 'read';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (size > 0) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.size'))}</strong>${formatBytes(size)}</div>`;
  }
  if (readType !== 'read') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${escapeHtml(readType)}</div>`;
  }
  html += '</div>';

  // 处理普通读取模式
  if (readType === 'read') {
    const content = result.content || result.text || '';
    if (content) {
      html += '<div class="tool-result-content scrollable">';
      html += `<pre>${escapeHtml(content)}</pre>`;
      html += '</div>';
    }
  }
  // 处理搜索模式
  else if (readType === 'search') {
    const matches = result.matches || [];
    const query = result.query || args.query || '';

    if (query) {
      html += '<div class="tool-result-meta">';
      html += `<div><strong>${escapeHtml(t('toolResults.labels.searchKeyword'))}</strong>${escapeHtml(query)}</div>`;
      html += `<div><strong>${escapeHtml(t('toolResults.labels.matchCount'))}</strong>${matches.length}</div>`;
      html += '</div>';
    }

    if (matches.length > 0) {
      html += '<div class="tool-result-content scrollable">';
      matches.forEach((match: any, idx: number) => {
        if (idx > 0) html += '<div class="search-match-separator">⋯</div>';
        html += '<div class="search-match-item">';
        const lineLabel =
          match.line_start === match.line_end
            ? `${match.line_start}`
            : `${match.line_start}-${match.line_end}`;
        html += `<div class="search-match-line">${escapeHtml(t('toolResults.values.line', { line: lineLabel }))}</div>`;
        html += `<pre>${escapeHtml(match.snippet || match.content || '')}</pre>`;
        html += '</div>';
      });
      html += '</div>';
    } else {
      html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noMatches'))}</div>`;
    }
  }
  // 处理提取模式
  else if (readType === 'extract') {
    const segments = result.segments || [];

    if (segments.length > 0) {
      html += '<div class="tool-result-meta">';
      html += `<div><strong>${escapeHtml(t('toolResults.labels.segmentCount'))}</strong>${segments.length}</div>`;
      html += '</div>';

      html += '<div class="tool-result-content scrollable">';
      segments.forEach((segment: any, idx: number) => {
        if (idx > 0) html += '<div class="segment-separator">⋯</div>';
        html += '<div class="segment-item">';
        const startLine = segment.start_line || segment.line_start || '?';
        const endLine = segment.end_line || segment.line_end || '?';
        html += `<div class="segment-range">${escapeHtml(t('toolResults.values.line', { line: `${startLine}-${endLine}` }))}</div>`;
        html += `<pre>${escapeHtml(segment.content || segment.text || '')}</pre>`;
        html += '</div>';
      });
      html += '</div>';
    } else {
      html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noExtractedContent'))}</div>`;
    }
  }

  return html;
}

function renderVlmAnalyze(result: any, args: any): string {
  const path = args.image_path || args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const analysis = result.analysis || result.result || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  if (analysis) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.analysis'))}</div>`;
    html += `<pre>${escapeHtml(analysis)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderOcrImage(result: any, args: any): string {
  const path = args.image_path || args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const size = result.size || result.file_size || 0;
  const text = result.text || result.ocr_text || '';
  const imageUrl = result.image_url || result.url || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (size > 0) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.size'))}</strong>${formatBytes(size)}</div>`;
  }
  html += '</div>';

  if (imageUrl) {
    html += `<div class="tool-result-image"><img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(t('toolResults.media.ocrImgAlt'))}" style="max-width: 100%; height: auto;" /></div>`;
  }

  if (text) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.ocrText'))}</div>`;
    html += `<pre>${escapeHtml(text)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderViewImage(result: any, args: any): string {
  const path = args.image_path || args.path || result.path || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const size = result.size || result.file_size || 0;
  // 优先用结果自带 URL；否则按 path 走文件内容接口加载原图
  // （host 模式支持绝对路径，docker/web 模式限工作区相对路径，与工具本身的路径约束一致）
  let imageUrl = result.image_url || result.url || '';
  if (!imageUrl && path && result.success !== false) {
    imageUrl = `/api/file/content?path=${encodeURIComponent(path)}`;
  }

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.path'))}</strong>${escapeHtml(path)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (size > 0) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.size'))}</strong>${formatBytes(size)}</div>`;
  }
  html += '</div>';

  if (imageUrl) {
    const safeUrl = escapeHtml(imageUrl);
    html += `<div class="tool-result-image"><a href="${safeUrl}" target="_blank" rel="noopener" title="${escapeHtml(t('toolResults.media.viewImgTitle'))}"><img src="${safeUrl}" alt="${escapeHtml(t('toolResults.media.viewImgAlt'))}" loading="lazy" /></a></div>`;
  }

  return html;
}

// 终端类渲染函数
function renderTerminalSession(result: any, args: any): string {
  const operation = args.operation || args.action || 'start';
  const sessionName = args.session_name || args.name || result.session_name || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.operation'))}</strong>${escapeHtml(operation)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.terminalName'))}</strong>${escapeHtml(sessionName)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += '</div>';

  return html;
}

function renderTerminalInput(result: any, args: any): string {
  const command = args.command || args.input || '';
  const outputWait = args.output_wait || 30;
  const output = result.output || result.result || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.command'))}</strong>${escapeHtml(command)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.waitTime'))}</strong>${escapeHtml(t('toolResults.duration.seconds', { seconds: outputWait }))}</div>`;
  html += '</div>';

  if (output) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.output'))}</div>`;
    html += `<pre>${escapeHtml(output)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderTerminalSnapshot(result: any, args: any): string {
  const sessionName = args.session_name || args.name || result.session_name || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.successMark'));
  const startLine = args.start_line || 0;
  const endLine = args.end_line || 0;
  const content = result.content || result.output || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.terminalName'))}</strong>${escapeHtml(sessionName)}</div>`;
  if (startLine || endLine) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.lineRange'))}</strong>${startLine} - ${endLine}</div>`;
  }
  html += '</div>';

  if (content) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.fetchedContent'))}</div>`;
    html += `<pre>${escapeHtml(content)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderSleep(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.completed'));
  const mode = result?.mode || 'seconds';
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${escapeHtml(mode)}</div>`;

  if (mode === 'seconds' || mode === '' || mode === null) {
    const seconds = args.seconds || args.duration || 0;
    html += `<div><strong>${escapeHtml(t('toolResults.labels.waitTime'))}</strong>${escapeHtml(t('toolResults.duration.seconds', { seconds }))}</div>`;
    if (args.reason) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.reason'))}</strong>${escapeHtml(String(args.reason))}</div>`;
    }
  } else if (mode === 'wait_sub_agent_output') {
    const targetName = result?.display_name ?? args?.wait_sub_agent_output ?? '';
    if (targetName !== '') {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.waitSubAgent'))}</strong>${escapeHtml(String(targetName))}</div>`;
    }
  } else if (mode === 'wait_sub_agent_ids') {
    const agentIds = Array.isArray(result?.agent_ids) ? result.agent_ids : [];
    if (agentIds.length > 0) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.waitSubAgent'))}</strong>${agentIds.map(String).join(', ')}</div>`;
    }
    const taskIds = Array.isArray(result?.waited_task_ids) ? result.waited_task_ids : [];
    if (taskIds.length > 0) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.taskId'))}</strong>${taskIds.map(String).join(', ')}</div>`;
    }
  } else if (mode === 'wait_runcommand_id') {
    const commandId = result?.command_id ?? args?.wait_runcommand_id ?? '';
    if (commandId !== '') {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.backgroundCommandId'))}</strong>${escapeHtml(String(commandId))}</div>`;
    }
  }

  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  // content
  if (mode === 'seconds' || mode === '' || mode === null) {
    const message = result?.message || '';
    if (message) {
      html += '<div class="tool-result-content scrollable">';
      html += `<div class="content-label">${escapeHtml(t('toolResults.labels.result'))}</div>`;
      html += `<pre>${escapeHtml(String(message))}</pre>`;
      html += '</div>';
    }
  } else if (mode === 'wait_sub_agent_output') {
    const message = result?.message || '';
    if (message) {
      html += '<div class="tool-result-content scrollable">';
      html += `<div class="content-label">${escapeHtml(t('toolResults.labels.subAgentOutput'))}</div>`;
      html += `<pre>${escapeHtml(String(message))}</pre>`;
      html += '</div>';
    }
  } else if (mode === 'wait_sub_agent_ids') {
    const results = Array.isArray(result?.results) ? result.results : [];
    if (results.length > 0) {
      html += '<div class="tool-result-content scrollable">';
      html += `<div class="content-label">${escapeHtml(t('toolResults.labels.waitedSubAgents', { n: results.length }))}</div>`;
      results.forEach((item: any) => {
        if (!item || typeof item !== 'object') return;
        const agentId = item.agent_id ?? '?';
        const taskId = item.task_id ?? '?';
        const itemStatus = item.status ?? 'unknown';
        const success = item.success === true;
        html += '<div class="sub-agent-result-item">';
        const outcome = success ? t('toolResults.status.successMark') : t('toolResults.status.failedMark');
        html += `<div><strong>${escapeHtml(t('toolResults.sentences.subAgentResultName', { agentId: String(agentId) }))}</strong>${escapeHtml(t('toolResults.sentences.subAgentResultMeta', { taskId: String(taskId), status: String(itemStatus), outcome }))}</div>`;
        const itemMessage = item.message || item.error || '';
        if (itemMessage) {
          const runtime = item.runtime_seconds ?? item.elapsed_seconds ?? null;
          const runtimeNote =
            runtime !== null ? t('toolResults.duration.runtimeNote', { n: Math.round(Number(runtime)) }) : '';
          const stats = item.stats;
          if (runtimeNote || stats) {
            html += '<div class="sub-agent-meta">';
            if (runtimeNote) {
              html += `<span>${escapeHtml(runtimeNote)}</span>`;
            }
            if (stats && typeof stats === 'object') {
              const statParts = [];
              if (stats.api_calls != null) statParts.push(t('toolResults.counts.statApiCalls', { n: stats.api_calls }));
              if (stats.files_read != null) statParts.push(t('toolResults.counts.statFilesRead', { n: stats.files_read }));
              if (stats.edit_files != null) statParts.push(t('toolResults.counts.statEditFiles', { n: stats.edit_files }));
              if (stats.searches != null) statParts.push(t('toolResults.counts.statSearches', { n: stats.searches }));
              if (stats.web_pages != null) statParts.push(t('toolResults.counts.statWebPages', { n: stats.web_pages }));
              if (stats.commands != null) statParts.push(t('toolResults.counts.statCommands', { n: stats.commands }));
              if (statParts.length > 0) {
                html += `<span>${escapeHtml(statParts.join(' | '))}</span>`;
              }
            }
            html += '</div>';
          }
          html += `<pre>${escapeHtml(String(itemMessage))}</pre>`;
        }
        html += '</div>';
      });
      html += '</div>';
    }
  } else if (mode === 'wait_runcommand_id') {
    const nested = result?.result;
    if (nested && typeof nested === 'object') {
      const output = nested.output || nested.stdout || '';
      const exitCode = nested.exit_code !== undefined ? nested.exit_code : nested.returncode;
      html += '<div class="tool-result-content scrollable">';
      html += `<div class="content-label">${escapeHtml(t('toolResults.labels.backgroundCommandOutput'))}</div>`;
      if (exitCode !== undefined) {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.exitCode'))}</strong>${escapeHtml(String(exitCode))}</div>`;
      }
      if (output) {
        html += `<pre>${escapeHtml(String(output))}</pre>`;
      } else {
        html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noOutput'))}</div>`;
      }
      html += '</div>';
    }
  }

  return html;
}

// 终端指令类渲染函数
function renderRunCommand(result: any, args: any): string {
  const command = args.command || '';
  const timeout = args.timeout || 30;
  const output = result.output || result.stdout || '';
  const exitCode = result.exit_code !== undefined ? result.exit_code : result.returncode;

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.command'))}</strong>${escapeHtml(command)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.timeout'))}</strong>${escapeHtml(t('toolResults.duration.seconds', { seconds: timeout }))}</div>`;
  if (exitCode !== undefined) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.exitCode'))}</strong>${exitCode}</div>`;
  }
  html += '</div>';

  if (output) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.output'))}</div>`;
    html += `<pre>${escapeHtml(output)}</pre>`;
    html += '</div>';
  }

  return html;
}

// 记忆类渲染函数
function renderUpdateMemory(result: any, args: any): string {
  const operation = args.operation || result.operation || '';
  const content = args.content || '';
  const index = args.index || result.index;
  const count = result.count || 0;

  let html = '<div class="tool-result-meta">';

  if (operation === 'append') {
    html += `<div>${escapeHtml(t('toolResults.sentences.memoryAdd'))}</div>`;
    html += `<div>${count}. ${escapeHtml(content)}</div>`;
  } else if (operation === 'replace') {
    html += `<div>${escapeHtml(t('toolResults.sentences.memoryUpdate'))}</div>`;
    html += `<div>${index}. ${escapeHtml(content)}</div>`;
  } else if (operation === 'delete') {
    html += `<div>${escapeHtml(t('toolResults.sentences.memoryDelete'))}</div>`;
    html += `<div>${index}. ${escapeHtml(content || t('toolResults.values.deleted'))}</div>`;
  }

  html += '</div>';

  return html;
}

function renderRecallProjectMemory(result: any, args: any): string {
  const name = args.name || result.memory_name || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.read'));
  const content = result.content || result.text || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.memory'))}</strong>${escapeHtml(name)}</div>`;
  html += '</div>';

  if (result.success && content) {
    html += '<div class="tool-result-content scrollable">';
    html += `<pre>${escapeHtml(content)}</pre>`;
    html += '</div>';
  } else if (!result.success && result.error) {
    html += `<div class="tool-result-error">${escapeHtml(String(result.error))}</div>`;
  }

  return html;
}

function renderSearchProjectMemory(result: any, args: any): string {
  const keywords = Array.isArray(result?.keywords)
    ? result.keywords
    : Array.isArray(args?.keywords)
      ? args.keywords
      : [];
  const results = Array.isArray(result?.results) ? result.results : [];
  const status = formatToolStatusLabel(result, t('toolResults.status.completed'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.keywords'))}</strong>${escapeHtml(keywords.join(' / ') || t('toolResults.values.notSpecified'))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div>${escapeHtml(t('toolResults.counts.memoryHits', { n: results.length }))}</div>`;
  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  if (!results.length) {
    if (result?.success) {
      html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noMatchesMemory'))}</div>`;
    }
    return html;
  }

  html += '<div class="search-result-list">';
  results.forEach((item: any) => {
    html += '<div class="search-result-item">';
    html += `<div class="search-result-title">${escapeHtml(item.name || item.file || '')}</div>`;
    if (item.description) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.description'))}</strong>${escapeHtml(item.description)}</div>`;
    }
    const snippets = Array.isArray(item.snippets) ? item.snippets : [];
    if (snippets.length > 0) {
      // 每条匹配片段独立成行渲染（pre 整体渲染时长行不换行会溢出容器），
      // 配合 .search-match-snippet 的 nowrap + ellipsis：过长片段直接省略号截断
      html += '<div class="search-match-item">';
      snippets.forEach((snippet: any) => {
        const lineText = `L${snippet.line ?? '?'}: ${snippet.text || ''}`;
        const escaped = escapeHtml(lineText);
        html += `<div class="search-match-snippet" title="${escaped.replace(/"/g, '&quot;')}">${escaped}</div>`;
      });
      html += '</div>';
    }
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function renderUpdateProjectMemory(result: any, args: any): string {
  const name = args.name || result.memory_name || '';
  const description = args.description || '';
  const content = args.content || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.updated'));

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.memory'))}</strong>${escapeHtml(name)}</div>`;
  if (description) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.description'))}</strong>${escapeHtml(description)}</div>`;
  }
  if (!result.success && result.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  if (result.success && content) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.memoryContent'))}</div>`;
    html += `<pre>${escapeHtml(content)}</pre>`;
    html += '</div>';
  }

  return html;
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
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.keywords'))}</strong>${escapeHtml(keywordText)}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.dateRange'))}</strong>${escapeHtml(result?.start_date || args?.start_date || t('toolResults.values.unlimited'))} ~ ${escapeHtml(result?.end_date || args?.end_date || t('toolResults.values.unlimited'))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.scope'))}</strong>${escapeHtml(t('toolResults.sentences.conversationScope'))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.resultCount'))}</strong>${results.length}</div>`;
  html += '</div>';
  if (!results.length) {
    html += `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noMatchesConversation'))}</div>`;
    return html;
  }
  html += '<div class="search-result-list">';
  results.forEach((item: any) => {
    html += '<div class="search-result-item">';
    html += `<div class="search-result-title">${escapeHtml(item.title || t('toolResults.values.unnamedConversation'))}</div>`;
    html += `<div><strong>${escapeHtml(t('toolResults.labels.id'))}</strong>${escapeHtml(item.id || '')}</div>`;
    html += `<div>${escapeHtml(t('toolResults.counts.conversationScale', { n: String(item.total_messages || 0), m: String(item.total_tools || 0) }))}</div>`;
    if (item.first_user_message) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.firstUserMessage'))}</strong>${escapeHtml(item.first_user_message)}</div>`;
    }
    if (item.updated_at) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.updatedAt'))}</strong>${escapeHtml(item.updated_at)}</div>`;
    }
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function renderConversationReview(result: any, args: any): string {
  const status = formatToolStatusLabel(
    result,
    result?.mode === 'read' ? t('toolResults.status.returned') : t('toolResults.status.generated')
  );
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.conversationId'))}</strong>${escapeHtml(result?.conversation_id || args?.conversation_id || '')}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${escapeHtml(result?.mode || args?.mode || '')}</div>`;
  if (result?.title) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.title'))}</strong>${escapeHtml(result.title)}</div>`;
  }
  if (result?.path) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.reviewFile'))}</strong>${escapeHtml(result.path)}</div>`;
  }
  if (result?.char_count !== undefined) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.charCount'))}</strong>${escapeHtml(String(result.char_count))}</div>`;
  }
  html += '</div>';
  if (!result?.success && result?.error) {
    html += `<div class="tool-result-error">${escapeHtml(result.error)}</div>`;
  }
  if (result?.too_long && result?.path) {
    html += `<div class="tool-result-warning">${escapeHtml(t('toolResults.sentences.tooLongSaved'))}</div>`;
  }
  if (result?.content) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.labels.reviewContent'))}</div>`;
    html += `<pre>${escapeHtml(result.content)}</pre>`;
    html += '</div>';
  }
  return html;
}

function renderCreateSkill(result: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.archived'));
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (result?.skill_name) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.skill'))}</strong>${escapeHtml(result.skill_name)}</div>`;
  }
  html += '</div>';
  if (!result?.success && result?.error) {
    html += `<div class="tool-result-error">${escapeHtml(result.error)}</div>`;
  }
  if (result?.sync_warning) {
    html += `<div class="tool-result-warning">${escapeHtml(result.sync_warning)}</div>`;
  }
  return html;
}

function renderListWorkflows(result: any, args: any): string {
  if (!result?.success) {
    const error = result?.error ?? t('toolResults.sentences.queryFailed');
    return `<div class="tool-result-error">⚠️ ${escapeHtml(String(error))}</div>`;
  }

  // name 形态：展示该工作流的完整定义文档
  const detailName = String(args?.name || result?.workflow_name || '');
  if (detailName) {
    let html = '<div class="tool-result-meta">';
    html += `<div><strong>${escapeHtml(t('toolResults.labels.workflow'))}</strong>${escapeHtml(detailName)}</div>`;
    html += '</div>';
    const doc = String(result?.message || '');
    if (doc) {
      html += '<div class="tool-result-content scrollable">';
      html += `<pre>${escapeHtml(doc)}</pre>`;
      html += '</div>';
    }
    return html;
  }

  // 列表形态
  const items = Array.isArray(result?.workflows) ? result.workflows : [];
  if (items.length === 0) {
    return `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noWorkflows'))}</div>`;
  }
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.workflowCount'))}</strong>${items.length}</div>`;
  html += '</div>';
  html += '<div class="search-result-list">';
  items.forEach((item: any) => {
    const name = escapeHtml(String(item?.name || t('toolResults.values.unnamed')));
    const desc = escapeHtml(String(item?.description || t('toolResults.values.noDescription')));
    const source = item?.source === 'builtin' ? t('toolResults.values.builtin') : t('toolResults.values.user');
    const nodeCount = Number(item?.nodeCount || 0);
    html += '<div class="search-result-item">';
    html += `<div class="search-result-title">${name}</div>`;
    html += `<div>${desc}</div>`;
    html += `<div><strong>${escapeHtml(t('toolResults.labels.source'))}</strong>${source} | <strong>${escapeHtml(t('toolResults.labels.nodeCount'))}</strong>${nodeCount}</div>`;
    html += '</div>';
  });
  html += '</div>';
  return html;
}

function renderSaveWorkflow(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.archived'));
  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  const sourceDir = String(args?.source_dir || '');
  if (sourceDir) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.sourceDir'))}</strong>${escapeHtml(sourceDir)}</div>`;
  }
  if (result?.workflow_name) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.workflow'))}</strong>${escapeHtml(String(result.workflow_name))}</div>`;
  }
  if (result?.success && typeof result?.node_count === 'number') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.nodeCountTotal'))}</strong>${result.node_count}</div>`;
  }
  if (result?.success && result?.overwritten) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${escapeHtml(t('toolResults.sentences.overwrittenVersion'))}</div>`;
  } else if (result?.success && result?.shadows_builtin) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.mode'))}</strong>${escapeHtml(t('toolResults.sentences.userCopyShadow'))}</div>`;
  }
  html += '</div>';
  if (!result?.success && result?.error) {
    html += `<div class="tool-result-error">${escapeHtml(String(result.error))}</div>`;
  }
  if (result?.success && result?.summary) {
    html += '<div class="tool-result-content">';
    html += escapeHtml(String(result.summary));
    html += '</div>';
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
  enabled: 'toolResults.personalization.fieldLabels.enabled',
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
      auto: t('toolResults.personalization.values.styleAuto'),
    };
    return styleMap[String(value)] || String(value);
  }
  if (field === 'conversation_continuity') {
    const independenceMap: Record<string, string> = {
      low: t('toolResults.personalization.values.independenceLow'),
      medium: t('toolResults.personalization.values.independenceMedium'),
      high: t('toolResults.personalization.values.independenceHigh'),
    };
    return independenceMap[String(value)] || String(value);
  }
  return String(value);
}

function renderAskUser(result: any, args: any): string {
  const question = args.question || result.question || '';
  const context = args.context || result.context || '';
  const status = formatToolStatusLabel(result, t('toolResults.status.answered'));
  const answerText =
    result.status === 'answered' ? String(result.answer_text || result.message || '').trim() : '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(status)}</div>`;
  if (question) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.question'))}</strong>${escapeHtml(String(question))}</div>`;
  }
  if (context) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.context'))}</strong>${escapeHtml(String(context))}</div>`;
  }
  html += '</div>';

  const options = Array.isArray(args.options) ? args.options : [];
  if (options.length > 0) {
    html += '<div class="tool-result-content">';
    html += '<div class="tool-result-meta">';
    html += `<div><strong>${escapeHtml(t('toolResults.labels.providedOptions'))}</strong></div>`;
    options.forEach((option: any, idx: number) => {
      const label = String(option?.label || option?.id || t('toolResults.values.optionLabel', { n: idx + 1 }));
      const desc = String(option?.description || '').trim();
      html += `<div>${idx + 1}. ${escapeHtml(label)}${desc ? ` — ${escapeHtml(desc)}` : ''}</div>`;
    });
    html += '</div></div>';
  }

  if (answerText) {
    html += '<div class="tool-result-content">';
    html += '<div class="tool-result-meta">';
    answerText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const separator = line.indexOf('：');
        if (separator > 0) {
          const label = line.slice(0, separator + 1);
          const value = line.slice(separator + 1);
          html += `<div><strong>${escapeHtml(label)}</strong>${escapeHtml(value)}</div>`;
        } else {
          html += `<div><strong>${escapeHtml(t('toolResults.labels.userAnswer'))}</strong>${escapeHtml(line)}</div>`;
        }
      });
    html += '</div></div>';
  }

  return html;
}

function renderManagePersonalization(result: any, args: any): string {
  const action = String(args.action || result.action || 'read');
  const status = formatToolStatusLabel(
    result,
    action === 'update' ? t('toolResults.status.updated') : t('toolResults.status.read')
  );
  const field = String(result.updated_field || args.field || '');
  const newValue = result.updated_value ?? args.value;

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.operation'))}</strong>${escapeHtml(action === 'update' ? t('toolResults.personalization.actionUpdate') : t('toolResults.personalization.actionRead'))}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;

  if (!result?.success) {
    if (result?.error) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
    }
    if (Array.isArray(result?.validation_errors) && result.validation_errors.length > 0) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.validation'))}</strong>${escapeHtml(result.validation_errors.join('；'))}</div>`;
    }
    html += '</div>';
    return html;
  }

  if (action === 'update' && field) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.updatedField'))}</strong>${escapeHtml(formatPersonalizationFieldLabel(field))}</div>`;
    html += `<div><strong>${escapeHtml(t('toolResults.labels.updatedValue'))}</strong>${escapeHtml(formatPersonalizationFieldValue(field, newValue))}</div>`;
  }

  html += '</div>';
  return html;
}

// 待办事项类渲染函数
function renderTodoCreate(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.created'));
  const todoList = result.todo_list || {};
  const title = todoList.title || args.title || '';
  const tasks = todoList.tasks || args.tasks || [];

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.title'))}</strong>${escapeHtml(title)}</div>`;
  html += '</div>';

  if (tasks.length > 0) {
    html += '<div class="tool-result-content">';
    html += '<div class="todo-list">';
    tasks.forEach((task: any) => {
      const taskText =
        typeof task === 'string' ? task : task.text || task.title || task.content || '';
      html += `<div class="todo-item"><input type="checkbox" disabled /> ${escapeHtml(taskText)}</div>`;
    });
    html += '</div>';
    html += '</div>';
  }

  return html;
}

function renderTodoUpdate(result: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.updated'));
  const todoList = result.todo_list || {};
  const title = todoList.title || '';
  const tasks = todoList.tasks || [];

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.title'))}</strong>${escapeHtml(title)}</div>`;
  html += '</div>';

  if (tasks.length > 0) {
    html += '<div class="tool-result-content">';
    html += '<div class="todo-list">';
    tasks.forEach((task: any) => {
      const taskText =
        typeof task === 'string' ? task : task.text || task.title || task.content || '';
      const completed =
        typeof task === 'object' &&
        (task.status === 'done' || task.completed || task.done || task.checked);
      html += `<div class="todo-item"><input type="checkbox" ${completed ? 'checked' : ''} disabled /> ${escapeHtml(taskText)}</div>`;
    });
    html += '</div>';
    html += '</div>';
  }

  return html;
}

// 彩蛋类渲染函数
function renderEasterEgg(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.triggered'));
  const type = result.type || args.type || '';
  const content = result.content || result.message || '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  html += `<div><strong>${escapeHtml(t('toolResults.labels.type'))}</strong>${escapeHtml(type)}</div>`;
  html += '</div>';

  if (content) {
    html += '<div class="tool-result-content">';
    html += `<div class="easter-egg-content">${escapeHtml(content)}</div>`;
    html += '</div>';
  }

  return html;
}

// 子智能体类渲染函数
function renderCreateSubAgent(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.created'), t('toolResults.status.createFailed'));
  const agentId = result.agent_id ?? args.agent_id ?? '';
  // 多智能体模式：只展示角色内编号显示名，全局 agent_id/task_id 不暴露
  const displayName = result.display_name ?? '';
  const taskId = result.task_id ?? '';
  const deliverablesDir = result.deliverables_dir ?? '';
  const taskDescription = args.task ?? '';
  const message = result.message ?? result.summary ?? '';

  const stats = result.stats || {};
  const runtimeSeconds =
    result.runtime_seconds ?? result.elapsed_seconds ?? stats.runtime_seconds ?? 0;
  const apiCalls = stats.api_calls ?? stats.turn_count ?? 0;
  const toolCount =
    (stats.files_read || 0) +
    (stats.write_files || 0) +
    (stats.edit_files || 0) +
    (stats.searches || 0) +
    (stats.web_pages || 0) +
    (stats.commands || 0);
  const hasStats = runtimeSeconds > 0 || apiCalls > 0 || toolCount > 0;

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgent'))}</strong>${escapeHtml(String(displayName))}</div>`;
  } else if (agentId !== '') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgentId'))}</strong>${escapeHtml(String(agentId))}</div>`;
  }
  if (taskId !== '' && !displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.taskId'))}</strong>${escapeHtml(String(taskId))}</div>`;
  }
  if (deliverablesDir !== '' && !displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.deliverablesDir'))}</strong>${escapeHtml(String(deliverablesDir))}</div>`;
  }
  if (taskDescription) {
    const preview = String(taskDescription).slice(0, 200);
    const suffix = String(taskDescription).length > 200 ? '…' : '';
    html += `<div><strong>${escapeHtml(t('toolResults.labels.task'))}</strong>${escapeHtml(preview + suffix)}</div>`;
  }
  html += '</div>';

  if (message || hasStats) {
    html += '<div class="tool-result-content scrollable">';
    if (hasStats) {
      html += `<div class="content-label">${escapeHtml(t('toolResults.sectionLabels.executionStats'))}</div>`;
      if (runtimeSeconds > 0) {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.workTime'))}</strong>${formatDuration(runtimeSeconds)}</div>`;
      }
      if (apiCalls > 0) {
        html += `<div>${escapeHtml(t('toolResults.counts.apiCalls', { n: apiCalls }))}</div>`;
      }
      if (toolCount > 0) {
        html += `<div>${escapeHtml(t('toolResults.counts.toolCalls', { n: toolCount }))}</div>`;
      }
    }
    if (message) {
      if (hasStats) {
        html += `<div class="content-label">${escapeHtml(t('toolResults.sectionLabels.finalReply'))}</div>`;
      }
      html += `<div>${escapeHtml(String(message))}</div>`;
    }
    html += '</div>';
  }

  return html;
}

function renderTerminateSubAgent(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.closed'), t('toolResults.status.closeFailed'));
  const displayName = result.display_name ?? args.display_name ?? '';
  const agentId = result.agent_id ?? args.agent_id ?? '';
  const taskId = result.task_id ?? '';
  const message = result.message ?? result.system_message ?? '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgent'))}</strong>${escapeHtml(String(displayName))}</div>`;
  } else if (agentId !== '') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgentId'))}</strong>${escapeHtml(String(agentId))}</div>`;
  }
  if (taskId !== '' && !displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.taskId'))}</strong>${escapeHtml(String(taskId))}</div>`;
  }
  html += '</div>';

  if (message) {
    html += '<div class="tool-result-content scrollable">';
    html += `<div>${escapeHtml(String(message))}</div>`;
    html += '</div>';
  }

  return html;
}

function renderGetSubAgentStatus(result: any): string {
  if (!result.success) {
    const error = result.error ?? t('toolResults.sentences.queryFailed');
    return `<div class="tool-result-error">⚠️ ${escapeHtml(String(error))}</div>`;
  }

  const results = Array.isArray(result.results) ? result.results : [];
  if (results.length === 0) {
    return `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noSubAgentStatus'))}</div>`;
  }

  let html = '<div class="sub-agent-status-list">';
  results.forEach((item: any) => {
    const agentId = item.agent_id ?? '';
    const found = item.found !== false;
    const status = String(item.status || '');
    const taskId = item.task_id ?? '';
    const summary = item.summary ?? item.final_result?.message ?? item.final_result?.summary ?? '';

    html += '<div class="sub-agent-status-item">';
    const itemDisplayName = item.display_name || '';
    const itemHeader = itemDisplayName || t('toolResults.sentences.subAgentResultName', { agentId: String(agentId) });
    html += `<div class="sub-agent-status-header">${escapeHtml(String(itemHeader))}</div>`;

    if (!found) {
      html += '<div class="tool-result-meta">';
      html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(t('toolResults.status.notFound'))}</div>`;
      html += '</div>';
    } else {
      html += '<div class="tool-result-meta">';
      if (status === 'completed') {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(t('toolResults.status.done'))}</div>`;
      } else if (status === 'terminated') {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(t('toolResults.status.terminated'))}</div>`;
      } else if (status === 'running') {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(t('common.running'))}</div>`;
      } else if (status === 'pending') {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(t('toolResults.status.waiting'))}</div>`;
      } else {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${escapeHtml(status || t('toolResults.values.unknown'))}</div>`;
      }
      if (taskId !== '' && !itemDisplayName) {
        html += `<div><strong>${escapeHtml(t('toolResults.labels.taskId'))}</strong>${escapeHtml(String(taskId))}</div>`;
      }
      html += '</div>';

      if (summary) {
        html += '<div class="tool-result-content scrollable">';
        html += `<div>${escapeHtml(String(summary))}</div>`;
        html += '</div>';
      }
    }

    html += '</div>';
  });
  html += '</div>';

  return html;
}

function renderSendMessageToSubAgent(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.sent'), t('toolResults.status.sendFailed'));
  const displayName = args.display_name ?? result.display_name ?? '';
  const agentId = args.agent_id ?? result.agent_id ?? '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (displayName) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgent'))}</strong>${escapeHtml(String(displayName))}</div>`;
  } else if (agentId !== '') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.subAgentId'))}</strong>${escapeHtml(String(agentId))}</div>`;
  }
  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  return html;
}

function renderAnswerSubAgentQuestion(result: any, args: any): string {
  const status = formatToolStatusLabel(result, t('toolResults.status.replied'), t('toolResults.status.replyFailed'));
  const questionId = args.question_id ?? result.question_id ?? '';
  const answer = args.answer ?? '';

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (questionId) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.questionId'))}</strong>${escapeHtml(String(questionId))}</div>`;
  }
  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  if (answer && typeof answer === 'string') {
    html += '<div class="tool-result-content scrollable">';
    html += `<div class="content-label">${escapeHtml(t('toolResults.sectionLabels.replyContent'))}</div>`;
    const preview = String(answer).slice(0, 500);
    const suffix = String(answer).length > 500 ? '…' : '';
    html += `<pre>${escapeHtml(preview + suffix)}</pre>`;
    html += '</div>';
  }

  return html;
}

function renderCreateCustomAgent(result: any, args: any): string {
  const overwritten = !!result?.overwritten;
  const status = result?.success
    ? overwritten
      ? t('toolResults.status.overwrittenUpdated')
      : t('toolResults.status.created')
    : t('toolResults.status.createFailed');
  const roleId = args.role_id ?? result.role_id ?? '';
  const name = args.name ?? result.name ?? roleId;

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.status'))}</strong>${status}</div>`;
  if (roleId !== '') {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.roleId'))}</strong>${escapeHtml(String(roleId))}</div>`;
  }
  if (name && name !== roleId) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.roleName'))}</strong>${escapeHtml(String(name))}</div>`;
  }
  if (!result?.success && result?.error) {
    html += `<div><strong>${escapeHtml(t('toolResults.labels.error'))}</strong>${escapeHtml(String(result.error))}</div>`;
  }
  html += '</div>';

  return html;
}

function renderListAgents(result: any): string {
  if (!result?.success) {
    const error = result?.error ?? t('toolResults.sentences.queryFailed');
    return `<div class="tool-result-error">⚠️ ${escapeHtml(String(error))}</div>`;
  }

  const roles = Array.isArray(result.roles) ? result.roles : [];
  if (roles.length === 0) {
    return `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noRoles'))}</div>`;
  }

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.roleCount'))}</strong>${roles.length}</div>`;
  html += '</div>';

  html += '<div class="search-result-list">';
  roles.forEach((role: any) => {
    const roleId = role.role_id || t('toolResults.values.unknown');
    const name = role.name || roleId;
    const description = role.description || '';
    const thinkingMode = role.thinking_mode || 'fast';
    const isCustom = role.is_custom ? t('toolResults.values.yes') : t('toolResults.values.no');

    html += '<div class="search-result-item">';
    html += `<div class="search-result-title">${escapeHtml(roleId)} — ${escapeHtml(name)}</div>`;
    if (description) {
      html += `<div>${escapeHtml(description)}</div>`;
    }
    html += `<div><strong>${escapeHtml(t('toolResults.labels.thinkingMode'))}</strong>${escapeHtml(thinkingMode)} | <strong>${escapeHtml(t('toolResults.labels.custom'))}</strong>${escapeHtml(isCustom)}</div>`;
    html += '</div>';
  });
  html += '</div>';

  return html;
}

function renderListActiveSubAgents(result: any): string {
  if (!result?.success) {
    const error = result?.error ?? t('toolResults.sentences.queryFailed');
    return `<div class="tool-result-error">⚠️ ${escapeHtml(String(error))}</div>`;
  }

  const agents = Array.isArray(result.agents) ? result.agents : [];
  if (agents.length === 0) {
    return `<div class="tool-result-empty">${escapeHtml(t('toolResults.sentences.noActiveSubAgents'))}</div>`;
  }

  let html = '<div class="tool-result-meta">';
  html += `<div><strong>${escapeHtml(t('toolResults.labels.activeCount'))}</strong>${agents.length}</div>`;
  html += '</div>';

  html += '<div class="sub-agent-status-list">';
  agents.forEach((agent: any) => {
    const agentId = agent.agent_id ?? '?';
    const displayName = agent.display_name || `Agent_${agentId}`;
    const status = agent.status || 'unknown';
    const summary = agent.summary || '';
    const lastOutput = agent.last_output || '';

    html += '<div class="sub-agent-status-item">';
    // 只展示角色内编号显示名，不暴露全局 agent_id
    html += `<div class="sub-agent-status-header">${escapeHtml(displayName)} [${escapeHtml(status)}]</div>`;
    html += '<div class="tool-result-meta">';
    if (summary) {
      html += `<div><strong>${escapeHtml(t('toolResults.labels.task'))}</strong>${escapeHtml(String(summary))}</div>`;
    }
    if (lastOutput) {
      const preview = String(lastOutput).slice(0, 120);
      const suffix = String(lastOutput).length > 120 ? '…' : '';
      html += `<div><strong>${escapeHtml(t('toolResults.labels.lastOutput'))}</strong>${escapeHtml(preview + suffix)}</div>`;
    }
    html += '</div>';
    html += '</div>';
  });
  html += '</div>';

  return html;
}
