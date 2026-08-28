// @ts-nocheck
import { debugLog } from '../common';
import { t } from '@/locales';
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import { usePersonalizationStore } from '../../../stores/personalization';
import { useTutorialStore } from '../../../stores/tutorial';
import { renderMarkdown as renderMarkdownHelper } from '../../../composables/useMarkdownRenderer';
import { scrollToBottom as scrollToBottomHelper, conditionalScrollToBottom as conditionalScrollToBottomHelper, scrollThinkingToBottom as scrollThinkingToBottomHelper } from '../../../composables/useScrollControl';
import { startResize as startPanelResize, handleResize as handlePanelResize, stopResize as stopPanelResize } from '../../../composables/usePanelResize';
import {
  SUB_AGENT_DONE_PREFIX_RE,
  BG_RUN_COMMAND_DONE_PREFIX_RE,
  userMDebug,
  UI_BOUNCE_TRACE_MAX,
  uiBounceTraceLastTsByKey,
  isUiBounceTraceEnabled,
  uiBounceTrace,
  isConnectionDiagEnabled,
  pushConnectionDiagRecord,
  connectionDiag,
  parseSubAgentDoneLabel,
  parseBackgroundRunCommandDoneLabel,
  parseSystemNoticeLabel,
} from './shared';

export const composerMethods = {
  normalizeComposerDraftContent(rawValue) {
    let text = '';
    if (typeof rawValue === 'string') {
      text = rawValue;
    } else if (rawValue === null || rawValue === undefined) {
      text = '';
    } else {
      text = String(rawValue);
    }
    if (text.length > 40000) {
      text = text.slice(0, 40000);
    }
    return text;
  },
  scheduleComposerDraftPersist(reason = 'input') {
    const current = this.normalizeComposerDraftContent(this.inputMessage);
    const lastSynced = this.normalizeComposerDraftContent(this.composerDraftLastSyncedContent);
    const dirty = current !== lastSynced;
    this.composerDraftDirty = dirty;
    if (this.composerDraftSaveTimer) {
      clearTimeout(this.composerDraftSaveTimer);
      this.composerDraftSaveTimer = null;
    }
    if (!dirty) {
      return;
    }
    this.composerDraftSaveTimer = window.setTimeout(() => {
      this.persistComposerDraftNow({ reason: `debounce:${reason}` }).catch(() => {});
    }, 1000);
  },
  async persistComposerDraftNow(options = {}) {
    const reason = String(options?.reason || 'manual');
    const force = !!options?.force;
    const useBeacon = !!options?.useBeacon;
    const content = this.normalizeComposerDraftContent(
      Object.prototype.hasOwnProperty.call(options || {}, 'content')
        ? options.content
        : this.inputMessage
    );
    const lastSynced = this.normalizeComposerDraftContent(this.composerDraftLastSyncedContent);
    if (!force && content === lastSynced) {
      this.composerDraftDirty = false;
      return { success: true, skipped: true };
    }

    if (this.composerDraftSaveTimer) {
      clearTimeout(this.composerDraftSaveTimer);
      this.composerDraftSaveTimer = null;
    }

    const composerRef = typeof this.getInputComposerRef === 'function' ? this.getInputComposerRef() : null;
    const composerMeta =
      composerRef && typeof composerRef.getComposerDraftMeta === 'function'
        ? composerRef.getComposerDraftMeta()
        : {};
    const payloadText = JSON.stringify({
      content,
      editor_json:
        composerMeta?.editor_json && typeof composerMeta.editor_json === 'object'
          ? composerMeta.editor_json
          : null,
      skill_refs: Array.isArray(composerMeta?.skill_refs) ? composerMeta.skill_refs : []
    });
    if (
      useBeacon &&
      typeof navigator !== 'undefined' &&
      typeof navigator.sendBeacon === 'function'
    ) {
      try {
        const blob = new Blob([payloadText], { type: 'application/json' });
        navigator.sendBeacon('/api/input-draft', blob);
        this.composerDraftLastSyncedContent = content;
        this.composerDraftDirty = false;
        return { success: true, queued: true, reason };
      } catch (error) {
        // sendBeacon 失败时回落到 fetch
      }
    }

    const response = await fetch('/api/input-draft', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      credentials: 'same-origin',
      body: payloadText,
      keepalive: !!options?.keepalive
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data?.success) {
      throw new Error(data?.error || t('appUi.saveInputDraftFailed'));
    }
    this.composerDraftLastSyncedContent = content;
    this.composerDraftDirty = false;
    return { success: true, saved: true, reason };
  },
  async restoreComposerDraftState(reason = 'manual') {
    const fetchSeq = Number(this.composerDraftFetchSeq || 0) + 1;
    this.composerDraftFetchSeq = fetchSeq;
    try {
      const response = await fetch('/api/input-draft', {
        method: 'GET',
        credentials: 'same-origin'
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || t('appUi.fetchInputDraftFailed'));
      }
      if (Number(this.composerDraftFetchSeq || 0) !== fetchSeq) {
        return;
      }
      if (this.taskInProgress || this.composerBusy) {
        debugLog('[UI] 跳过输入草稿恢复（任务运行中）', {
          reason,
          taskInProgress: !!this.taskInProgress,
          composerBusy: !!this.composerBusy
        });
        return;
      }
      const content = this.normalizeComposerDraftContent(payload?.data?.content || '');
      const skillRefs = Array.isArray(payload?.data?.skill_refs) ? payload.data.skill_refs : [];
      const editorJson =
        payload?.data?.editor_json && typeof payload.data.editor_json === 'object'
          ? payload.data.editor_json
          : null;
      this.composerDraftLastSyncedContent = content;
      this.composerDraftDirty = false;
      this.inputSetMessage(content);
      this.$nextTick(() => {
        const composerRef =
          typeof this.getInputComposerRef === 'function' ? this.getInputComposerRef() : null;
        if (composerRef && typeof composerRef.restoreComposerDraftMeta === 'function') {
          composerRef.restoreComposerDraftMeta({ skill_refs: skillRefs, editor_json: editorJson });
        }
        if (typeof this.autoResizeInput === 'function') {
          this.autoResizeInput();
        }
      });
      debugLog('[UI] 输入草稿已恢复', { reason, length: content.length });
    } catch (error) {
      console.warn('[UI] 恢复输入草稿失败:', error);
    }
  },
  handleBeforeUnloadDraftPersist() {
    this.persistComposerDraftNow({
      reason: 'beforeunload',
      force: true,
      useBeacon: true
    }).catch(() => {});
  },
  clearComposerDraftState(reason = 'manual') {
    debugLog('[UI] 清理输入草稿状态', { reason });
    this.inputClearMessage();
    this.composerDraftLastSyncedContent = '';
    this.composerDraftDirty = false;
    if (this.composerDraftSaveTimer) {
      clearTimeout(this.composerDraftSaveTimer);
      this.composerDraftSaveTimer = null;
    }
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
    this.$nextTick(() => {
      if (typeof this.autoResizeInput === 'function') {
        this.autoResizeInput();
      }
    });
  },
  handleInputChange() {
    this.autoResizeInput();
    this.scheduleComposerDraftPersist('input-change');
  },
  handleInputFocus() {
    this.inputSetFocused(true);
    this.closeQuickMenu();
    this.closePermissionMenu();
  },
  handleInputBlur() {
    this.inputSetFocused(false);
  },
  getComposerElement(field) {
    const composer = this.getInputComposerRef();
    const unwrap = (value: any) => {
      if (!value) {
        return null;
      }
      if (value instanceof HTMLElement) {
        return value;
      }
      if (typeof value === 'object' && 'value' in value && !(value instanceof Window)) {
        return value.value;
      }
      return value;
    };
    if (composer && composer[field]) {
      return unwrap(composer[field]);
    }
    if (this.$refs && this.$refs[field]) {
      return unwrap(this.$refs[field]);
    }
    return null;
  },
  getInputComposerRef() {
    return this.$refs.inputComposer || null;
  },
  handleComposerHeightChange(payload = {}) {
    const raw =
      Number(payload?.reservedHeight || 0) ||
      Number(payload?.height || 0) ||
      Number(payload?.composerHeight || 0);
    if (!Number.isFinite(raw) || raw <= 0) {
      return;
    }
    const next = Math.max(80, Math.min(560, Math.round(raw)));
    if (Math.abs(next - Number(this.composerReservedHeight || 0)) < 1) {
      return;
    }
    this.composerReservedHeight = next;
  }
};
