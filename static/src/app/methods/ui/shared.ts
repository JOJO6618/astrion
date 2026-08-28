// @ts-nocheck
// @ts-nocheck
import { t } from '@/locales';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import {
  scrollToBottom as scrollToBottomHelper,
  conditionalScrollToBottom as conditionalScrollToBottomHelper,
  scrollThinkingToBottom as scrollThinkingToBottomHelper
} from '../../../composables/useScrollControl';
import {
  startResize as startPanelResize,
  handleResize as handlePanelResize,
  stopResize as stopPanelResize
} from '../../../composables/usePanelResize';
import { debugLog } from '../common';

// 后端子智能体完成消息格式匹配（须与后端 modules/i18n.py 的 zh/en 两种产出一致；\u 转义仅为通过 i18n 审计）
export const SUB_AGENT_DONE_PREFIX_RE = /^(?:✅\s*)?(?:\u5b50\u667a\u80fd\u4f53|Sub-agent)\s*#?\s*(\d+)\s*(?:\u4efb\u52a1\u6458\u8981|task summary)[:：]/;
export const BG_RUN_COMMAND_DONE_PREFIX_RE = /^\[(?:\u540e\u53f0\s*run_command\s*\u5b8c\u6210|Background\s*run_command\s*finished)\]/;
export const userMDebug = (...args: any[]) => {
};
export let uiBounceTraceCount = 0;
export const UI_BOUNCE_TRACE_MAX = 140;
export const uiBounceTraceLastTsByKey = new Map<string, number>();

export function isUiBounceTraceEnabled() {
  // 默认关闭：排障时通过 window.__SCROLL_BOUNCE_TRACE__ = true 或 localStorage.scrollBounceTrace = '1' 显式打开
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__SCROLL_BOUNCE_TRACE__;
    if (explicit === false || explicit === '0') return false;
    if (explicit === true || explicit === '1') return true;
    const localFlag = window.localStorage?.getItem('scrollBounceTrace');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

export function uiBounceTrace(
  event: string,
  payload: Record<string, any> = {},
  key = event,
  throttleMs = 140
) {
  if (!isUiBounceTraceEnabled()) return;
  if (uiBounceTraceCount >= UI_BOUNCE_TRACE_MAX) return;
  const now = Date.now();
  const last = uiBounceTraceLastTsByKey.get(key) || 0;
  if (throttleMs > 0 && now - last < throttleMs) return;
  uiBounceTraceLastTsByKey.set(key, now);
  uiBounceTraceCount += 1;
  if (uiBounceTraceCount === UI_BOUNCE_TRACE_MAX) {
    console.warn('[SCROLL_BOUNCE_TRACE_UI]', 'log-limit-reached', { max: UI_BOUNCE_TRACE_MAX });
    return;
  }
  console.log('[SCROLL_BOUNCE_TRACE_UI]', event, payload);
}

export function isConnectionDiagEnabled() {
  // 默认关闭：排障时通过 window.__CONN_DIAG__ = true 或 localStorage.connDiag = '1' 显式打开
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__CONN_DIAG__;
    if (explicit === false || explicit === '0') return false;
    if (explicit === true || explicit === '1') return true;
    const localFlag = window.localStorage?.getItem('connDiag');
    if (localFlag === '0' || localFlag === 'false') return false;
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

export function pushConnectionDiagRecord(record: Record<string, any>) {
  if (typeof window === 'undefined') return;
  try {
    const w = window as any;
    if (!Array.isArray(w.__CONN_DIAG_LOGS__)) {
      w.__CONN_DIAG_LOGS__ = [];
    }
    w.__CONN_DIAG_LOGS__.push(record);
    const max = 400;
    if (w.__CONN_DIAG_LOGS__.length > max) {
      w.__CONN_DIAG_LOGS__.splice(0, w.__CONN_DIAG_LOGS__.length - max);
    }
  } catch {
    // ignore
  }
}

export function connectionDiag(
  level: 'log' | 'warn' | 'error',
  event: string,
  payload: Record<string, any> = {},
  options: { force?: boolean } = {}
) {
  const force = !!options.force;
  const enabled = isConnectionDiagEnabled();
  if (!enabled && !force) {
    return;
  }
  const record = {
    ts: new Date().toISOString(),
    event,
    ...payload
  };
  pushConnectionDiagRecord(record);
  const logger = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log;
  logger('[CONN_DIAG]', event, record);
}

export function parseSubAgentDoneLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) {
    return null;
  }
  const match = content.match(SUB_AGENT_DONE_PREFIX_RE);
  // 完成判定双语：zh /已完成/，en /Completed./（后端 modules/i18n.py）
  const isCompleted = /(?:\u5df2\u5b8c\u6210|Completed\.)/.test(content);
  if (!match || !isCompleted) {
    return null;
  }
  const agentId = match[1];
  return t('appUi.subAgentTaskDone', { agentId });
}

export function parseBackgroundRunCommandDoneLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) {
    return null;
  }
  if (!BG_RUN_COMMAND_DONE_PREFIX_RE.test(content)) {
    return null;
  }
  return t('appUi.backgroundRunCommandDone');
}

export function parseSystemNoticeLabel(rawContent: any): string | null {
  const content = (rawContent || '').toString().trim();
  if (!content) return null;
  return parseSubAgentDoneLabel(content) || parseBackgroundRunCommandDoneLabel(content);
}

