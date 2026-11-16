// Main-process outbox consumer: independent of content view/window lifetime.
import { app, Notification, session } from 'electron';

export function startScheduleNotifications(port, openMain) {
  const origin = `http://127.0.0.1:${port}`;
  let stopped = false;
  let timer;
  const live = new Map();
  async function post(route, value) {
    const csrf = await session.defaultSession.fetch(`${origin}/api/csrf-token`, { signal: AbortSignal.timeout(10000) });
    const { token } = await csrf.json();
    const response = await session.defaultSession.fetch(`${origin}/api/schedules${route}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
      body: JSON.stringify(value), signal: AbortSignal.timeout(10000)
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload.data;
  }
  async function deliver(run) {
    const ack = error => post(`/notifications/${run.id}/ack`, { token: run.token, ...(error ? { error } : {}) }).catch(error => console.warn('[astrion-schedules] notification acknowledgment:', error.message));
    if (!Notification.isSupported()) { await ack('Native notifications unavailable'); return; }
    const success = run.state === 'success';
    const notice = new Notification({ title: run.name, body: run.summary?.slice(0, 220) || run.reason || (success ? '任务已完成' : `任务状态：${run.state}`), silent: false });
    live.set(run.id, notice);
    notice.once('show', () => { void ack(); });
    notice.once('failed', (_event, error) => { live.delete(run.id); void ack(String(error || 'Native notification failed')); });
    notice.once('close', () => live.delete(run.id));
    notice.once('click', () => {
      live.delete(run.id);
      openMain('/schedules');
    });
    try { notice.show(); } catch (error) { live.delete(run.id); await ack(error.message); }
  }
  async function poll() {
    try {
      const runs = await post('/notifications/claim', {});
      if (!stopped) for (const run of runs) await deliver(run);
    } catch (error) {
      // Backend startup/restart is retried without losing durable notifications.
      if (!stopped) console.warn('[astrion-schedules] notification poll:', error.message);
    }
    if (!stopped) timer = setTimeout(poll, 5000);
  }
  const stop = () => { stopped = true; clearTimeout(timer); live.clear(); };
  app.once('before-quit', stop);
  void poll();
  return stop;
}
