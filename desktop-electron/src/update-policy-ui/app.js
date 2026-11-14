(() => {
  const token = new URLSearchParams(location.search).get('token') || '';
  const elements = Object.fromEntries(['heading', 'versions', 'message', 'reason', 'cached', 'status', 'activity', 'recheck', 'exit', 'download', 'install'].map(id => [id, document.getElementById(id)]));
  let text, busy = false, disposed = false;
  const request = async (url, method = 'GET') => {
    const response = await fetch(url, { method, cache: 'no-store', headers: { 'X-Astrion-Update-Gate': token } });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || 'request_failed');
    return body;
  };
  const setBusy = value => {
    busy = value;
    document.querySelector('.update-gate').setAttribute('aria-busy', String(value));
    for (const name of ['recheck', 'install', 'download']) elements[name].disabled = value;
    elements.activity.hidden = !value;
  };
  const render = ({ policy, identity }) => {
    elements.heading.textContent = policy.delivery === 'manual' ? text.manualTitle : text.requiredTitle;
    elements.versions.textContent = text.versionLine.replace('{current}', identity.version).replace('{latest}', policy.latest_version || identity.version);
    elements.message.textContent = policy.delivery === 'manual' ? text.manualMessage : text.requiredMessage;
    elements.reason.textContent = policy.reason || '';
    elements.reason.hidden = !policy.reason;
    elements.cached.textContent = text.cached;
    elements.cached.hidden = policy.source !== 'cache';
    elements.download.hidden = policy.delivery !== 'manual';
    elements.install.hidden = policy.delivery === 'manual';
  };
  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    elements.status.textContent = text.checking;
    try {
      render(await request('/update/policy?force=1'));
      elements.status.textContent = '';
    } catch { elements.status.textContent = text.requestFailed; }
    finally { setBusy(false); }
  };
  const poll = async () => {
    if (disposed) return;
    try {
      const progress = await request('/update/progress');
      const active = ['checking', 'downloading', 'installing', 'restarting'].includes(progress.state);
      elements.status.textContent = progress.state === 'checking' ? text.checking
        : progress.state === 'downloading' ? text.downloading
        : ['installing', 'restarting'].includes(progress.state) ? text.installing
        : progress.state === 'error' || progress.state === 'no_update' ? (progress.error || text.failed) : '';
      if (!active) { setBusy(false); return; }
      setTimeout(poll, 600);
    } catch {
      elements.status.textContent = text.requestFailed;
      setBusy(false);
    }
  };
  const act = async action => {
    try { await request('/update/policy/' + action, 'POST'); }
    catch { elements.status.textContent = text.requestFailed; }
  };
  elements.recheck.addEventListener('click', refresh);
  elements.download.addEventListener('click', () => act('download'));
  elements.exit.addEventListener('click', () => act('quit'));
  elements.install.addEventListener('click', async () => {
    if (busy) return;
    setBusy(true);
    elements.status.textContent = text.checking;
    try { await request('/update/install', 'POST'); void poll(); }
    catch {
      try { render(await request('/update/policy/status')); }
      catch { /* Keep the last policy visible when the bridge is unavailable. */ }
      elements.status.textContent = text.failed;
      setBusy(false);
    }
  });
  window.addEventListener('pagehide', () => { disposed = true; });
  (async () => {
    text = await request('/update/required/strings');
    for (const [name, key] of [['recheck', 'recheck'], ['exit', 'exit'], ['download', 'download'], ['install', 'autoUpdate']]) elements[name].textContent = text[key];
    render(await request('/update/policy/status'));
    setBusy(false);
  })().catch(() => { window.close(); });
})();
