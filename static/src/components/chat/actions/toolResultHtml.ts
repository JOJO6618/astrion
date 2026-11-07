// 工具结果显示的公共拼装件。所有渲染器（含子智能体）共用同一套结构与样式类：
//   .tool-result-body      外层纵向容器，子块之间统一间距（不再依赖各块 margin）
//   .tool-result-meta      只放参数（§5.6）；短参数一行，长参数/多行参数进限高小块
//   .tool-result-content   只放结果；.scrollable 是该工具唯一的大块滚动区域
//   .tool-result-error     失败原因，由 renderEnhancedToolResult 统一追加
// 样式定义在 ToolAction.vue 的非 scoped 样式块。

import { t } from '@/locales';

export function escapeHtml(text: unknown): string {
  const div = document.createElement('div');
  div.textContent = String(text ?? '');
  return div.innerHTML;
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
}

const isBlank = (value: unknown) =>
  value === undefined || value === null || (typeof value === 'string' && value.trim() === '');

const toText = (value: unknown): string =>
  typeof value === 'string'
    ? value
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);

// 英文半角冒号后补空格；中文全角冒号自带间距
const labelGap = (label: string) => (/:$/.test(label) ? ' ' : '');

/** 「标签：值」纯文本拼接（摘要行等场景）。 */
export function lv(label: string, value: unknown): string {
  return `${label}${labelGap(label)}${toText(value)}`;
}

// 超过该长度或含换行的参数改用限高块显示，避免说明区被长参数撑高
const LONG_PARAM_CHARS = 120;

export interface ParamOptions {
  /** 等宽字体（命令、代码类参数） */
  mono?: boolean;
  /** 强制使用限高块 */
  block?: boolean;
}

/** 参数行：label 已含冒号（沿用 toolResults.labels.* 文案）。值为空时返回空串。 */
export function param(label: string, value: unknown, options: ParamOptions = {}): string {
  if (isBlank(value)) return '';
  const text = toText(value);
  const useBlock = options.block || text.includes('\n') || text.length > LONG_PARAM_CHARS;
  const mono = options.mono ? ' tool-param--mono' : '';
  if (useBlock) {
    return `<div class="tool-param tool-param--block${mono}"><span class="tool-param-label">${escapeHtml(label)}</span><div class="tool-param-text">${escapeHtml(text)}</div></div>`;
  }
  return `<div class="tool-param${mono}"><span class="tool-param-label">${escapeHtml(label)}</span>${labelGap(label)}<span class="tool-param-value">${escapeHtml(text)}</span></div>`;
}

/** 说明区：拼接参数行，全部为空时不输出容器（避免空灰块）。 */
export function meta(...rows: string[]): string {
  const body = rows.filter(Boolean).join('');
  return body ? `<div class="tool-result-meta">${body}</div>` : '';
}

/**
 * 非终态/非普通成败状态（待审批、等待回答、已拒绝、已取消）才显示状态行；
 * 普通成功/失败已由块头部文案表达，不再重复。
 */
export function pendingStatus(result: any): string {
  const state = String(result?.status || '').toLowerCase();
  let label = '';
  if (state === 'awaiting_user_answer') label = t('toolResults.status.awaitingUserAnswer');
  else if (state === 'awaiting_approval' || state === 'pending_approval' || state === 'pending')
    label = t('toolResults.status.awaitingApproval');
  else if (state === 'rejected') label = t('toolResults.status.rejected');
  else if (state === 'cancelled') label = t('toolResults.status.cancelled');
  return label ? param(t('toolResults.labels.status'), label) : '';
}

/** 结果框（唯一的大块滚动区域）；label 放在框外，不随内容滚走。 */
export function box(innerHtml: string, label = ''): string {
  if (!innerHtml) return '';
  const content = `<div class="tool-result-content scrollable">${innerHtml}</div>`;
  if (!label) return content;
  return `<div class="tool-result-section"><div class="tool-result-section-label">${escapeHtml(label)}</div>${content}</div>`;
}

/** 纯文本结果框。 */
export function preBox(text: unknown, label = ''): string {
  if (isBlank(text)) return '';
  return box(`<pre>${escapeHtml(toText(text).replace(/\s+$/, ''))}</pre>`, label);
}

/** 结果摘要行（退出码、命中数、替换数等执行统计）。 */
export function summary(...parts: string[]): string {
  const items = parts.filter(Boolean);
  if (!items.length) return '';
  return `<div class="tool-result-summary">${items.map(escapeHtml).join('<span class="tool-result-summary-sep">·</span>')}</div>`;
}

export function empty(text: string): string {
  return `<div class="tool-result-empty">${escapeHtml(text)}</div>`;
}

export function errorBlock(text: unknown): string {
  if (isBlank(text)) return '';
  return `<div class="tool-result-error">${escapeHtml(toText(text))}</div>`;
}

export function warning(text: unknown): string {
  if (isBlank(text)) return '';
  return `<div class="tool-result-warning">${escapeHtml(toText(text))}</div>`;
}

/** 工具结果中的失败原因；成功结果不显示。 */
export function resultErrorText(result: any): string {
  if (!result || typeof result !== 'object' || result.success === true) return '';
  const error = result.error;
  return isBlank(error) ? '' : toText(error);
}

export function body(innerHtml: string): string {
  return innerHtml ? `<div class="tool-result-body">${innerHtml}</div>` : '';
}

/** 秒数参数文案；未传参数时返回空串（不虚构默认值）。 */
export function seconds(value: unknown): string {
  if (isBlank(value)) return '';
  return t('toolResults.duration.seconds', { seconds: String(value) });
}

/** 布尔参数文案；未传参数返回空串。 */
export function yesNo(value: unknown): string {
  if (typeof value !== 'boolean') return '';
  return value ? t('toolResults.values.yes') : t('toolResults.values.no');
}
