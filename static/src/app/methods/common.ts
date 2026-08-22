// @ts-nocheck
const ENABLE_APP_DEBUG_LOGS = false;
const TRACE_CONV = false;

export function debugLog(...args) {
  if (!ENABLE_APP_DEBUG_LOGS) return;
  try {
    console.log('[app]', ...args);
  } catch (e) {
    /* ignore logging errors */
  }
}

export const traceLog = (...args) => {
  if (!TRACE_CONV) return;
  try {
    console.log('[conv-trace]', ...args);
  } catch (e) {
    // ignore
  }
};

// 目标模式调试日志：上报到后端 goal_mode_debug.log，便于复现状态继承问题。
// 默认关闭，避免日常刷屏；需要时可在浏览器控制台执行 localStorage.setItem('goalModeDebug','1') 后刷新。
function isGoalModeDebugEnabled() {
  if (typeof window === 'undefined') return false;
  try {
    const explicit = (window as any).__GOAL_MODE_DEBUG__;
    if (explicit === true || explicit === '1') return true;
    if (explicit === false || explicit === '0') return false;
    const localFlag = window.localStorage?.getItem('goalModeDebug');
    if (localFlag === '1' || localFlag === 'true') return true;
    return false;
  } catch {
    return false;
  }
}

let goalModeDebugSeq = 0;
export function goalModeDebugLog(event: string, payload: Record<string, any> = {}) {
  if (!isGoalModeDebugEnabled()) return;
  try {
    goalModeDebugSeq += 1;
    const entry = {
      event,
      seq: goalModeDebugSeq,
      client_ts: new Date().toISOString(),
      ...payload
    };
    // 同时输出到浏览器控制台，方便开发时查看
    console.log('[GOAL_MODE_DEBUG]', event, entry);
    // 上报后端持久化
    if (typeof fetch !== 'undefined') {
      void fetch('/api/client_debug_log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry)
      }).catch(() => {});
    }
  } catch (e) {
    // ignore logging errors
  }
}
