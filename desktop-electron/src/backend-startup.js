// Keep the previous origin whenever possible. Another process can acquire the
// preferred port between probing it and spawning the sidecar, so allow one
// fresh-port retry only after the first sidecar has exited.
export async function startDesktopPort({
  preferredPort, resolveBackendPort, pickFreePort, spawnBackend, waitBackendReady,
  shutdownBackend, primeDesktopSession, savePreferredPort, warn,
}) {
  let port = await resolveBackendPort(preferredPort);
  let child = spawnBackend(port);
  try {
    await waitBackendReady(port, child);
  } catch (error) {
    const stopped = await shutdownBackend();
    if (stopped === false || !preferredPort || port !== preferredPort) throw error;
    warn(`首选端口 ${port} 启动失败，改用空闲端口重试`, error);
    port = await pickFreePort();
    child = spawnBackend(port);
    try {
      await waitBackendReady(port, child);
    } catch (fallbackError) {
      await shutdownBackend();
      throw fallbackError;
    }
  }
  savePreferredPort(port);
  // Session priming is an optimization; the existing front-end login remains
  // the fallback (including when a password is configured).
  try {
    if (!await primeDesktopSession(port)) throw new Error('桌面会话预热未完成');
  } catch (error) {
    warn('启动预登录失败，交由前端登录流程处理', error);
  }
  return port;
}
