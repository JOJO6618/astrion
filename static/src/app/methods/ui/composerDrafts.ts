// @ts-nocheck
import { t } from '@/locales';
import {
  conversationViewEpoch,
  currentConversationSession,
  ownsConversationSession
} from '../conversation/session';
import {
  emptyDraft,
  getDraft,
  updateDraft,
  writeLocal,
  readRemote,
  syncDraft
} from './draftStorage';

const visible = new WeakMap();
const applying = new WeakMap();
function record(
  host,
  workspaceId = host.currentHostWorkspaceId,
  conversationId = host.currentConversationId
) {
  return getDraft(
    host.composerDraftOwner || '',
    String(workspaceId || ''),
    conversationId || 'new'
  );
}
function capture(host) {
  const entry = visible.get(host) || record(host);
  if (applying.has(host)) return entry;
  const meta = host.getInputComposerRef?.()?.getComposerDraftMeta?.() || {};
  return updateDraft(entry, {
    ...entry.payload,
    content: host.inputMessage || '',
    skill_refs: meta.skill_refs || [],
    editor_json: meta.editor_json || null,
    files: [...(host.selectedFiles || [])],
    images: [...(host.selectedImages || [])],
    videos: [...(host.selectedVideos || [])]
  });
}
function reportFailure(host, error, entry) {
  // Network errors are retried quietly while the durable local copy is available.
  if ((!entry.localFailed && !error.conflict && entry.loaded) || entry.warned) return;
  entry.warned = true;
  host.uiPushToast?.({
    title: t('appUi.saveInputDraftFailed'),
    message: error.conflict
      ? t('appUi.inputDraftConflict')
      : entry.localFailed
        ? t('appUi.inputDraftLocalFailed')
        : t('appUi.fetchInputDraftFailed'),
    type: 'warning'
  });
}
function persist(host, entry) {
  writeLocal(entry);
  return syncDraft(entry, (error, failed) => reportFailure(host, error, failed));
}
function updateDirty(host, entry) {
  host.composerDraftDirty = entry.signature !== entry.acknowledged;
  host.composerDraftLastSyncedContent = JSON.parse(entry.acknowledged || '{}').content || '';
}
function recoverPending(host) {
  const recovered = host.pendingDraftRecoveryMessages || [];
  host.pendingDraftRecoveryMessages = [];
  if (recovered.length) host.recoverRuntimeMessagesToDraft(recovered);
}

export const composerDraftMethods = {
  normalizeComposerDraftContent(value) {
    return String(value ?? '').slice(0, 40000);
  },
  scheduleComposerDraftPersist() {
    if (applying.has(this) || this.isConversationIndependentRoute?.()) return;
    const entry = capture(this);
    updateDirty(this, entry);
    if (entry.localFailed) reportFailure(this, new Error('local storage'), entry);
    if (this.composerDraftSaveTimer) clearTimeout(this.composerDraftSaveTimer);
    this.composerDraftSaveTimer = window.setTimeout(() => {
      void persist(this, entry);
    }, 700);
  },
  async persistComposerDraftNow() {
    if (this.isConversationIndependentRoute?.()) return { success: true, skipped: true };
    const entry = capture(this);
    if (this.composerDraftSaveTimer) clearTimeout(this.composerDraftSaveTimer);
    this.composerDraftSaveTimer = null;
    updateDirty(this, entry);
    if (!entry.workspaceId || !entry.owner) return { success: false, pendingWorkspace: true };
    const result = await persist(this, entry);
    if ((visible.get(this) || record(this)) === entry) updateDirty(this, entry);
    return result;
  },
  async prepareComposerDraftState(workspaceId, conversationId = null) {
    const entry = record(this, workspaceId, conversationId);
    // Source edits belong to the visible binding, even after the target session starts.
    if (entry === visible.get(this)) capture(this);
    try {
      await readRemote(entry);
    } catch (error) {
      reportFailure(this, error, entry);
    }
    return entry;
  },
  applyComposerDraftState(entry, workspaceId, session) {
    if (!entry || (session && !ownsConversationSession(this, session))) return;
    if (String(this.currentHostWorkspaceId || '') !== String(workspaceId || '')) return;
    visible.set(this, entry);
    const application = {};
    const epoch = conversationViewEpoch(this);
    applying.set(this, application);
    updateDirty(this, entry);
    this.inputSetMessage(entry.payload.content);
    this.selectedFiles = [...entry.payload.files];
    this.selectedImages = [...entry.payload.images];
    this.selectedVideos = [...entry.payload.videos];
    this.$nextTick(() => {
      if (applying.get(this) !== application) return;
      applying.delete(this);
      if (
        visible.get(this) !== entry ||
        conversationViewEpoch(this) !== epoch ||
        (session && !ownsConversationSession(this, session))
      )
        return;
      if (this.inputMessage === entry.payload.content) {
        this.getInputComposerRef?.()?.restoreComposerDraftMeta?.(entry.payload);
      } else {
        capture(this);
      }
      this.autoResizeInput?.();
      if (entry.signature !== entry.acknowledged) void persist(this, entry);
      recoverPending(this);
    });
  },
  async restoreComposerDraftState() {
    if (!this.currentHostWorkspaceId) return { success: true, pendingWorkspace: true };
    if (this.isConversationIndependentRoute?.()) return;
    if (!visible.has(this) && this.inputMessage) {
      visible.set(this, record(this));
      this.scheduleComposerDraftPersist();
      recoverPending(this);
      return { success: true, preservedEdits: true };
    }
    const session = currentConversationSession(this);
    const epoch = conversationViewEpoch(this);
    const content = this.inputMessage;
    const entry = await this.prepareComposerDraftState(
      this.currentHostWorkspaceId,
      this.currentConversationId
    );
    if (conversationViewEpoch(this) !== epoch || currentConversationSession(this) !== session)
      return;
    if (this.inputMessage && (this.inputMessage !== content || !entry.payload.content)) {
      visible.set(this, entry);
      this.scheduleComposerDraftPersist();
      recoverPending(this);
      return;
    }
    this.applyComposerDraftState(entry, this.currentHostWorkspaceId, session);
  },
  handleBeforeUnloadDraftPersist() {
    // Synchronous local persistence is the navigation guarantee. No unauthenticated beacon.
    if (this.isConversationIndependentRoute?.()) return;
    const entry = capture(this);
    writeLocal(entry);
  },
  captureComposerDraftSubmission() {
    const entry = capture(this);
    return { entry, signature: entry.signature, payload: JSON.parse(entry.signature) };
  },
  adoptComposerDraftConversation(conversationId) {
    const source = capture(this);
    const target = record(this, this.currentHostWorkspaceId, conversationId);
    updateDraft(target, source.payload);
    visible.set(this, target);
    return target;
  },
  completeComposerDraftSubmission(submission, isCurrent = true) {
    if (!submission) return false;
    if (!isCurrent) {
      if (submission.entry.signature === submission.signature) {
        updateDraft(submission.entry, {
          ...emptyDraft(),
          recovered_ids: submission.entry.payload.recovered_ids
        });
        void persist(this, submission.entry);
      }
      return false;
    }
    const current = capture(this);
    // The user can continue typing during a request. Only clear the submitted version.
    const matches = current.signature === submission.signature;
    if (submission.entry !== current && submission.entry.signature === submission.signature) {
      updateDraft(submission.entry, {
        ...emptyDraft(),
        recovered_ids: submission.entry.payload.recovered_ids
      });
      void persist(this, submission.entry);
    }
    if (!matches) return false;
    updateDraft(current, { ...emptyDraft(), recovered_ids: current.payload.recovered_ids });
    this.inputClearMessage();
    this.inputClearSelectedImages?.();
    this.inputClearSelectedVideos?.();
    this.inputClearSelectedFiles?.();
    this.getInputComposerRef?.()?.restoreComposerDraftMeta?.(emptyDraft());
    this.inputSetLineCount?.(1);
    this.inputSetMultiline?.(false);
    void persist(this, current);
    this.autoResizeInput?.();
    return true;
  },
  recoverRuntimeMessagesToDraft(messages = []) {
    if (!messages.length || !this.currentConversationId) return;
    const entry = capture(this);
    const known = new Set(entry.payload.recovered_ids);
    const recovered = messages.filter(
      (item) =>
        item?.id &&
        (item.text || item.files?.length || item.images?.length || item.videos?.length) &&
        !known.has(item.id)
    );
    if (!recovered.length) return;
    const payload = {
      ...entry.payload,
      content: [entry.payload.content, ...recovered.map((item) => item.text)]
        .filter(Boolean)
        .join('\n\n'),
      editor_json: null,
      files: [
        ...new Set([...entry.payload.files, ...recovered.flatMap((item) => item.files || [])])
      ],
      images: [
        ...new Set([...entry.payload.images, ...recovered.flatMap((item) => item.images || [])])
      ],
      videos: [
        ...new Set([...entry.payload.videos, ...recovered.flatMap((item) => item.videos || [])])
      ],
      recovered_ids: [...known, ...recovered.map((item) => item.id)]
    };
    // Never truncate recovered messages. Their original persistent queue remains the fallback.
    if (
      payload.content.length > 40000 ||
      payload.files.length > 9 ||
      payload.images.length > 9 ||
      payload.videos.length > 1
    ) {
      this.uiPushToast?.({
        title: t('appUi.saveInputDraftFailed'),
        message: t('appUi.inputDraftRecoveryTooLong'),
        type: 'warning'
      });
      return;
    }
    updateDraft(entry, payload);
    this.applyComposerDraftState(entry, entry.workspaceId, currentConversationSession(this));
    void persist(this, entry);
  },
  clearComposerDraftState() {
    const submission = this.captureComposerDraftSubmission();
    this.completeComposerDraftSubmission(submission);
    this.inputSetImagePickerOpen?.(false);
    this.inputSetVideoPickerOpen?.(false);
    this.imageEntries = [];
    this.videoEntries = [];
    this.imageLoading = false;
    this.videoLoading = false;
    this.mediaUploading = false;
  }
};
