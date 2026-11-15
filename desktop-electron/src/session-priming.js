// 启动期会话预热：主窗口建立前，用 defaultSession 先完成一次桌面免密登录。
//
// 背景：窗口建立时 chrome 视图会立即请求 /api/conversation-tabs，而会话要等
// 内容视图加载 /login、LoginApp.onMounted 自动 POST /host-login 之后才建立，
// 中间这段窗口期里标签条拿到 401，界面显示成「同步失败，重试」并且没有标签。
// 这里在建窗之前把会话准备好，让两个视图的首个请求就是已登录状态。
//
// 约束：
// - cookie 必须写进 defaultSession（视图同用这个 session），所以走 ses.fetch 而不是全局 fetch。
// - /host-login 受 CSRF 保护（server/state.py 的 CSRF_PROTECTED_PATHS），先取 token。
// - 已经登录时不重复登录：重复 host-login 会 session.clear() 并重置工作区等会话状态。
// - 任何失败都只记日志，不阻塞启动：前端原有的登录流程仍然兜底。

import { session } from 'electron';

const PRIME_TIMEOUT_MS = 10_000;

async function fetchWithTimeout(ses, url, init, timeoutMs = PRIME_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await ses.fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/** 返回 true 表示当前 cookie 已经可用（无需登录）。失败按未登录处理。 */
async function alreadySignedIn(ses, origin) {
  try {
    const response = await fetchWithTimeout(ses, `${origin}/api/session-status`, {
      credentials: 'include'
    });
    if (!response.ok) return false;
    const payload = await response.json();
    return Boolean(payload?.logged_in);
  } catch {
    return false;
  }
}

export async function primeDesktopSession(port) {
  if (!Number.isInteger(port) || port <= 0) return false;
  const ses = session.defaultSession;
  const origin = `http://127.0.0.1:${port}`;
  if (await alreadySignedIn(ses, origin)) return true;

  const tokenResponse = await fetchWithTimeout(ses, `${origin}/api/csrf-token`, {
    credentials: 'include'
  });
  if (!tokenResponse.ok) throw new Error(`CSRF token 获取失败 (${tokenResponse.status})`);
  const tokenPayload = await tokenResponse.json().catch(() => null);
  const token = typeof tokenPayload?.token === 'string' ? tokenPayload.token : '';
  if (!token) throw new Error('CSRF token 为空');

  const loginResponse = await fetchWithTimeout(ses, `${origin}/host-login`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
    body: JSON.stringify({})
  });
  const payload = await loginResponse.json().catch(() => null);
  if (!loginResponse.ok || !payload?.success) {
    const detail = payload?.error ? `：${payload.error}` : '';
    throw new Error(`预登录失败 (${loginResponse.status})${detail}`);
  }
  return true;
}
