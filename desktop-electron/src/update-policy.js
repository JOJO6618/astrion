import { app, net, shell } from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { fileURLToPath } from 'node:url';
import { createUpdaterLogger } from './updater-log.js';

const ENDPOINT = 'https://astrion.cyjai.com/downloads/update-policy';
const OFFICIAL = 'https://astrion.cyjai.com/';
const events = new EventEmitter();
const log = (event, details) => createUpdaterLogger(app)(event, details);
let inFlight = null;
let checkedAt = 0;
let current = { required: false, delivery: 'auto', reason: '', download_url: OFFICIAL, source: 'none' };

export function desktopUpdateIdentity() {
  const metadata = JSON.parse(fs.readFileSync(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8'));
  return { version: app.getVersion(), build_id: metadata.astrionBuildId || 'legacy', platform: process.platform, arch: process.arch };
}

export function officialDownloadUrl(value) {
  const url = new URL(value || OFFICIAL);
  if (url.protocol !== 'https:' || url.hostname !== 'astrion.cyjai.com' || url.username || url.password || url.port && url.port !== '443') {
    throw new Error('invalid_official_download_url');
  }
  return url.href;
}

function validatePolicy(value, identity) {
  if (!value || value.schema !== 1 || typeof value.required !== 'boolean' || !['auto', 'manual'].includes(value.delivery)) throw new Error('invalid_update_policy');
  for (const key of ['version', 'build_id', 'platform', 'arch']) {
    if (value.client?.[key] !== identity[key]) throw new Error('update_policy_identity_mismatch');
  }
  const issued = Date.parse(value.issued_at), expires = Date.parse(value.expires_at);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || issued > Date.now() + 300000 || expires <= Date.now() || expires <= issued || expires - issued > 604800000) throw new Error('update_policy_expired');
  if (typeof value.id !== 'string' || !value.id || typeof value.latest_version !== 'string' || typeof value.reason !== 'string' || value.reason.length > 2000) throw new Error('invalid_update_policy_fields');
  return { ...value, download_url: officialDownloadUrl(value.download_url) };
}

function cachePath() { return path.join(app.getPath('userData'), 'update-policy-cache.json'); }
function cachedPolicy(identity) {
  try { return validatePolicy(JSON.parse(fs.readFileSync(cachePath(), 'utf8')), identity); }
  catch { return null; }
}
function savePolicy(policy) {
  const filename = cachePath(), temporary = filename + '.' + randomUUID() + '.tmp';
  try {
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(temporary, JSON.stringify(policy), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(temporary, filename);
  } catch (error) {
    log('update-policy-cache-failed', { error: String(error.message || error) });
  } finally { try { fs.unlinkSync(temporary); } catch { /* already renamed */ } }
}

export function currentUpdatePolicy() {
  // A policy's expiry is checked on every use, not just on network refresh.
  if (current.expires_at && Date.parse(current.expires_at) <= Date.now()) {
    current = { required: false, delivery: 'auto', reason: '', download_url: OFFICIAL, source: 'expired' };
  }
  return current;
}

export function onUpdatePolicyChange(listener) {
  events.on('change', listener);
  return () => events.off('change', listener);
}

export async function checkUpdatePolicy({ force = false } = {}) {
  if (inFlight) return inFlight;
  if (!force && checkedAt && Date.now() - checkedAt < 30000) return currentUpdatePolicy();
  inFlight = (async () => {
    const identity = desktopUpdateIdentity();
    if (!app.isPackaged) return currentUpdatePolicy();
    const url = new URL(ENDPOINT);
    for (const [key, value] of Object.entries({ current_version: identity.version, build_id: identity.build_id, platform: identity.platform, arch: identity.arch })) url.searchParams.set(key, value);
    try {
      const response = await net.fetch(url.href, { cache: 'no-store', credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error('update_policy_http_' + response.status);
      const body = await response.text();
      if (body.length > 32768) throw new Error('update_policy_too_large');
      const policy = validatePolicy(JSON.parse(body), identity);
      savePolicy(policy);
      current = { ...policy, source: 'server' };
      log('update-policy-received', { id: policy.id, required: policy.required, delivery: policy.delivery, buildId: identity.build_id });
    } catch (error) {
      const cached = cachedPolicy(identity);
      // Also preserve a still-valid in-memory rule if a cache write failed.
      let previous = null;
      try { previous = validatePolicy(current, identity); } catch { /* no valid prior policy */ }
      const policy = [cached, previous].filter(Boolean).sort((a, b) => Date.parse(b.issued_at) - Date.parse(a.issued_at))[0];
      current = policy ? { ...policy, source: 'cache' } : { required: false, delivery: 'auto', reason: '', download_url: OFFICIAL, source: 'unavailable' };
      log('update-policy-unavailable', { cached: Boolean(policy), error: String(error.message || error) });
    }
    checkedAt = Date.now();
    events.emit('change', current);
    return current;
  })();
  try { return await inFlight; } finally { inFlight = null; }
}

export async function openPolicyDownload() {
  await shell.openExternal(officialDownloadUrl(currentUpdatePolicy().download_url));
}
