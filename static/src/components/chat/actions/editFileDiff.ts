// edit_file 的 diff 渲染：按替换明细与上下文行重建 hunk，排序合并后输出行级 diff。
// 从 toolRenderers.ts 拆出（逻辑未改），只返回 diff 区域 HTML。

import { escapeHtml } from './toolResultHtml';

export function renderEditFileDiff(details: any[]): string {
  let html = '';
  html += '<div class="tool-result-diff scrollable">';
  const renderDiffLine = (lineNo: number | null, marker: string, text: string, className = '') => {
    const lineNoText = typeof lineNo === 'number' && Number.isFinite(lineNo) ? String(lineNo) : '';
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
          minLine: numberedRows.length ? Math.min(...numberedRows.map((row) => row.lineNo)) : null,
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
  return html;
}
