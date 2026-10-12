// @ts-nocheck
import { t } from '@/locales';
import { workspaceScopedFetch as fetch } from '../workspaceRequestScope';
import {
  conversationViewEpoch,
  currentConversationSession,
  ownsConversationSession
} from '../conversation/session';

type DraftRecord = {
  payload: any;
  signature: string;
  acknowledged: string;
  revision: number;
  sequence: number;
  loaded: boolean;
  tail: Promise<any>;
  saving?: string;
  read?: object;
  warned?: string;
};
const hosts = new WeakMap<object, Map<string, DraftRecord>>();
const applications = new WeakMap<
  object,
  { entry: DraftRecord; workspaceId: string; epoch: number }
>();
const clientId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
const empty = () => ({ content: '', skill_refs: [], editor_json: null });
const scope = (host) => String(host.currentHostWorkspaceId || '');
function record(host, workspaceId = scope(host)): DraftRecord {
  let entries = hosts.get(host);
  if (!entries) {
    entries = new Map();
    hosts.set(host, entries);
  }
  let entry = entries.get(workspaceId);
  if (!entry) {
    entry = {
      payload: empty(),
      signature: JSON.stringify(empty()),
      acknowledged: JSON.stringify(empty()),
      revision: 0,
      sequence: 0,
      loaded: false,
      tail: Promise.resolve()
    };
    entries.set(workspaceId, entry);
  }
  return entry;
}
function capture(host, content = host.inputMessage) {
  const entry = record(host);
  const meta = host.getInputComposerRef?.()?.getComposerDraftMeta?.() || {};
  const payload = {
    content: host.normalizeComposerDraftContent(content),
    skill_refs: Array.isArray(meta.skill_refs) ? meta.skill_refs : [],
    editor_json: meta.editor_json && typeof meta.editor_json === 'object' ? meta.editor_json : null
  };
  const signature = JSON.stringify(payload);
  if (entry.signature !== signature) {
    entry.payload = payload;
    entry.signature = signature;
    entry.revision++;
  }
  entry.loaded = true;
  return entry;
}

export const composerDraftMethods = {
  normalizeComposerDraftContent(value) {
    return String(value ?? '').slice(0, 40000);
  },
  scheduleComposerDraftPersist(reason = 'input') {
    const application = applications.get(this);
    if (
      application &&
      application.workspaceId === scope(this) &&
      application.epoch === conversationViewEpoch(this) &&
      this.inputMessage === application.entry.payload.content
    )
      return;
    const entry = capture(this);
    this.composerDraftDirty = entry.signature !== entry.acknowledged;
    if (this.composerDraftSaveTimer) clearTimeout(this.composerDraftSaveTimer);
    this.composerDraftSaveTimer = null;
    if (this.composerDraftDirty)
      this.composerDraftSaveTimer = window.setTimeout(() => {
        void this.persistComposerDraftNow({ reason }).catch(() => {});
      }, 1000);
  },
  async persistComposerDraftNow(options = {}) {
    const workspaceId = scope(this);
    const entry = capture(
      this,
      Object.hasOwn(options, 'content') ? options.content : this.inputMessage
    );
    const signature = entry.signature;
    if (this.composerDraftSaveTimer) clearTimeout(this.composerDraftSaveTimer);
    this.composerDraftSaveTimer = null;
    if (!workspaceId) {
      this.composerDraftDirty = entry.signature !== entry.acknowledged;
      return { success: false, pendingWorkspace: true };
    }
    if (entry.saving === signature) return entry.tail;
    if (!options.force && entry.acknowledged === signature) return { success: true, skipped: true };
    const sequence = ++entry.sequence;
    const body = JSON.stringify({
      ...entry.payload,
      workspace_id: workspaceId,
      client_id: clientId,
      sequence
    });
    const url = `/api/input-draft?workspace_id=${encodeURIComponent(workspaceId)}`;
    if (
      options.useBeacon &&
      typeof navigator !== 'undefined' &&
      typeof navigator.sendBeacon === 'function'
    ) {
      if (navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))) {
        return { success: true, queued: true };
      }
    }
    entry.saving = signature;
    const save = async () => {
      try {
        const response = await fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-Astrion-Workspace-Id': workspaceId },
          body,
          keepalive: !!options.keepalive
        });
        const result = await response.json();
        if (!response.ok || !result.success)
          throw new Error(result.error || t('appUi.saveInputDraftFailed'));
        if (result.data?.saved) {
          entry.acknowledged = signature;
          entry.warned = undefined;
        }
        if (scope(this) === workspaceId) {
          this.composerDraftLastSyncedContent =
            JSON.parse(entry.acknowledged || '{}').content || '';
          this.composerDraftDirty = capture(this).signature !== entry.acknowledged;
        }
        return result;
      } catch (error) {
        if (entry.warned !== signature) {
          entry.warned = signature;
          this.uiPushToast?.({
            title: t('appUi.saveInputDraftFailed'),
            message: String(error?.message || error),
            type: 'error'
          });
        }
        if (scope(this) === workspaceId) this.composerDraftDirty = true;
        throw error;
      } finally {
        if (entry.saving === signature) entry.saving = undefined;
      }
    };
    entry.tail = entry.tail.catch(() => {}).then(save);
    return entry.tail;
  },
  async prepareComposerDraftState(workspaceId) {
    workspaceId = String(workspaceId || '');
    if (!workspaceId) return capture(this);
    const entry = record(this, workspaceId);
    if (workspaceId === scope(this)) return capture(this);
    // Failed/newer local edits remain authoritative until acknowledged.
    if (entry.loaded && entry.signature !== entry.acknowledged) return entry;
    const request = {};
    entry.read = request;
    const revision = entry.revision;
    await entry.tail.catch(() => {});
    const response = await fetch(
      `/api/input-draft?workspace_id=${encodeURIComponent(workspaceId)}`,
      {
        headers: { 'X-Astrion-Workspace-Id': workspaceId },
        credentials: 'same-origin'
      }
    );
    const result = await response.json();
    if (!response.ok || !result.success)
      throw new Error(result.error || t('appUi.fetchInputDraftFailed'));
    if (entry.read === request && entry.revision === revision) {
      entry.payload = {
        content: this.normalizeComposerDraftContent(result.data?.content),
        skill_refs: result.data?.skill_refs || [],
        editor_json: result.data?.editor_json || null
      };
      entry.signature = JSON.stringify(entry.payload);
      entry.acknowledged = entry.signature;
      entry.loaded = true;
    }
    return entry;
  },
  applyComposerDraftState(entry, workspaceId, session) {
    if (session && !ownsConversationSession(this, session)) return;
    if (!workspaceId || scope(this) !== String(workspaceId)) return;
    const revision = entry.revision;
    const epoch = conversationViewEpoch(this);
    const application = { entry, workspaceId: String(workspaceId), epoch };
    applications.set(this, application);
    this.composerDraftLastSyncedContent = JSON.parse(entry.acknowledged || '{}').content || '';
    this.composerDraftDirty = entry.signature !== entry.acknowledged;
    this.inputSetMessage(entry.payload.content);
    this.$nextTick(() => {
      if (applications.get(this) !== application) return;
      applications.delete(this);
      if (
        scope(this) !== application.workspaceId ||
        entry.revision !== revision ||
        conversationViewEpoch(this) !== epoch ||
        (session && !ownsConversationSession(this, session))
      )
        return;
      this.getInputComposerRef?.()?.restoreComposerDraftMeta?.(entry.payload);
      this.autoResizeInput?.();
      if (this.composerDraftDirty) this.scheduleComposerDraftPersist('restored-edits');
    });
  },
  async restoreComposerDraftState() {
    const workspaceId = scope(this);
    if (!workspaceId) return { success: true, skipped: true, pendingWorkspace: true };
    const entry = record(this);
    if (this.inputMessage || (entry.loaded && entry.signature !== entry.acknowledged)) return;
    const revision = capture(this).revision;
    const readOwner = {};
    entry.read = readOwner;
    const initialContent = this.inputMessage;
    const epoch = conversationViewEpoch(this);
    const session = currentConversationSession(this);
    const response = await fetch(
      `/api/input-draft?workspace_id=${encodeURIComponent(workspaceId)}`,
      {
        headers: { 'X-Astrion-Workspace-Id': workspaceId }
      }
    );
    const result = await response.json();
    if (
      scope(this) !== workspaceId ||
      currentConversationSession(this) !== session ||
      conversationViewEpoch(this) !== epoch ||
      entry.read !== readOwner ||
      entry.revision !== revision ||
      this.inputMessage !== initialContent
    )
      return;
    if (!response.ok || !result.success)
      throw new Error(result.error || t('appUi.fetchInputDraftFailed'));
    entry.payload = {
      content: this.normalizeComposerDraftContent(result.data?.content),
      skill_refs: result.data?.skill_refs || [],
      editor_json: result.data?.editor_json || null
    };
    entry.signature = JSON.stringify(entry.payload);
    entry.acknowledged = entry.signature;
    entry.loaded = true;
    this.applyComposerDraftState(entry, workspaceId, session);
  },
  handleBeforeUnloadDraftPersist() {
    void this.persistComposerDraftNow({
      reason: 'beforeunload',
      force: true,
      useBeacon: true
    }).catch(() => {});
  },
  clearComposerDraftState() {
    this.inputClearMessage();
    capture(this, '');
    this.composerDraftLastSyncedContent = '';
    this.composerDraftDirty = true;
    if (this.composerDraftSaveTimer) clearTimeout(this.composerDraftSaveTimer);
    this.composerDraftSaveTimer = null;
    this.inputSetLineCount(1);
    this.inputSetMultiline(false);
    this.inputClearSelectedImages();
    this.inputClearSelectedVideos();
    this.inputSetImagePickerOpen(false);
    this.inputSetVideoPickerOpen(false);
    this.imageEntries = [];
    this.videoEntries = [];
    this.imageLoading = false;
    this.videoLoading = false;
    this.mediaUploading = false;
    this.$nextTick(() => this.autoResizeInput?.());
  }
};
