// @ts-nocheck
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';

export const emptyDraft = () => ({
  content: '',
  skill_refs: [],
  editor_json: null,
  files: [],
  images: [],
  videos: [],
  recovered_ids: []
});
export function normalizeDraft(value = {}) {
  const result = { ...emptyDraft(), ...value };
  result.content = String(value.content || '').slice(0, 40000);
  result.editor_json =
    value.editor_json && typeof value.editor_json === 'object' ? value.editor_json : null;
  for (const key of ['skill_refs', 'files', 'images', 'videos', 'recovered_ids'])
    result[key] = Array.isArray(value[key]) ? value[key] : [];
  result.recovered_ids = result.recovered_ids.slice(-1000);
  return Object.fromEntries(Object.keys(emptyDraft()).map((key) => [key, result[key]]));
}
const records = new Map();
const clientId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
export function getDraft(owner, workspaceId, conversationId = 'new') {
  const key = `astrion:composer:v2:${JSON.stringify([owner, workspaceId, conversationId || 'new'])}`;
  if (records.has(key)) return records.get(key);
  let local;
  try {
    local = JSON.parse(globalThis.localStorage?.getItem(key) || 'null');
  } catch {
    /* keep server recovery available */
  }
  const payload = normalizeDraft(local?.payload);
  const entry = {
    key,
    owner,
    workspaceId,
    conversationId: conversationId || 'new',
    payload,
    signature: JSON.stringify(payload),
    acknowledged: local?.acknowledged ?? JSON.stringify(emptyDraft()),
    serverRevision: Number(local?.serverRevision || 0),
    revision: 0,
    sequence: 0,
    loaded: !!local,
    remoteReady: false,
    tail: Promise.resolve(),
    retry: null,
    localFailed: false
  };
  records.set(key, entry);
  return entry;
}
export function writeLocal(entry) {
  if (!entry.owner || !entry.workspaceId) return;
  try {
    if (!globalThis.localStorage) throw new Error('Local storage unavailable');
    globalThis.localStorage.setItem(
      entry.key,
      JSON.stringify({
        payload: entry.payload,
        acknowledged: entry.acknowledged,
        serverRevision: entry.serverRevision
      })
    );
    entry.localFailed = false;
  } catch {
    entry.localFailed = true;
  }
}
export function updateDraft(entry, payload) {
  const normalized = normalizeDraft(payload);
  const signature = JSON.stringify(normalized);
  if (entry.signature !== signature) {
    entry.payload = normalized;
    entry.signature = signature;
    entry.revision++;
    writeLocal(entry);
  }
  entry.loaded = true;
  return entry;
}
function url(entry) {
  return `/api/input-draft?workspace_id=${encodeURIComponent(entry.workspaceId)}&conversation_id=${encodeURIComponent(entry.conversationId)}`;
}
export async function readRemote(entry) {
  if (!entry.workspaceId || !entry.owner) return entry;
  if (entry.reading) return entry.reading;
  const revision = entry.revision;
  const hadLocal = entry.loaded;
  entry.reading = (async () => {
    try {
      const response = await fetch(url(entry), {
        headers: { 'X-Astrion-Workspace-Id': entry.workspaceId },
        signal: AbortSignal.timeout(5000)
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error || 'Draft read failed');
      // A GET may have captured the server before our in-flight POST committed.
      if (Number(result.data?.revision || 0) < entry.serverRevision) return entry;
      if (entry.revision === revision && entry.signature === entry.acknowledged) {
        entry.payload = normalizeDraft(result.data);
        entry.signature = JSON.stringify(entry.payload);
        entry.acknowledged = entry.signature;
        entry.serverRevision = Number(result.data?.revision || 0);
        writeLocal(entry);
      } else {
        const remoteSignature = JSON.stringify(normalizeDraft(result.data));
        if (remoteSignature === entry.signature || remoteSignature === entry.lastAttempt) {
          entry.acknowledged = remoteSignature;
          entry.serverRevision = Number(result.data?.revision || 0);
          writeLocal(entry);
        } else if (!hadLocal || remoteSignature === entry.acknowledged) {
          entry.serverRevision = Number(result.data?.revision || 0);
        }
      }
      entry.remoteReady = true;
      entry.loaded = true;
      return entry;
    } finally {
      entry.reading = null;
    }
  })();
  return entry.reading;
}
export function syncDraft(entry, onFailure = () => {}) {
  if (!entry.owner || !entry.workspaceId) return Promise.resolve({ success: false });
  if (entry.retry) clearTimeout(entry.retry);
  entry.retry = null;
  // Coalesce saves. Capture the latest payload when the previous request finishes.
  if (entry.syncing) {
    entry.resync = true;
    return entry.syncing;
  }
  entry.syncing = Promise.resolve().then(async () => {
    try {
      if (entry.signature === entry.acknowledged) return { success: true, skipped: true };
      if (!entry.remoteReady) await readRemote(entry);
      if (entry.signature === entry.acknowledged) return { success: true };
      const signature = entry.signature;
      entry.lastAttempt = signature;
      const response = await fetch(url(entry), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Astrion-Workspace-Id': entry.workspaceId
        },
        body: JSON.stringify({
          ...entry.payload,
          workspace_id: entry.workspaceId,
          conversation_id: entry.conversationId,
          client_id: clientId,
          sequence: ++entry.sequence,
          base_revision: entry.serverRevision
        }),
        signal: AbortSignal.timeout(10000)
      });
      const result = await response.json();
      if (!response.ok || !result.success) {
        const error = new Error(result.error || 'Draft save failed');
        error.conflict = response.status === 409;
        throw error;
      }
      entry.acknowledged = signature;
      entry.serverRevision = Number(result.data?.revision || entry.serverRevision);
      entry.failures = 0;
      entry.warned = false;
      writeLocal(entry);
      return result;
    } catch (error) {
      onFailure(error, entry);
      entry.conflicted = !!error.conflict;
      if (!error.conflict) {
        entry.remoteReady = false;
        entry.failures = Math.min(6, (entry.failures || 0) + 1);
        entry.retry = setTimeout(
          () => {
            void syncDraft(entry, onFailure);
          },
          Math.min(30000, 1000 * 2 ** entry.failures)
        );
      }
      return { success: false, error };
    } finally {
      entry.syncing = null;
      if (entry.resync) {
        entry.resync = false;
        if (!entry.retry && !entry.conflicted && entry.signature !== entry.acknowledged)
          void syncDraft(entry, onFailure);
      }
    }
  });
  return entry.syncing;
}
