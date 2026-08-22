// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import {
  extractSkillRefsFromMessage,
  SKILL_MARKDOWN_LINK_RE,
} from './shared';

export const runtimeQueueMethods = {
  buildRuntimeQueueSnapshotKey(messages = []) {
    const list = Array.isArray(messages) ? messages : [];
    return JSON.stringify(
      list.map((item) => [
        String(item?.id || ''),
        String(item?.text || ''),
        Number(item?.createdAt || 0),
        Array.isArray(item?.files) ? item.files : []
      ])
    );
  },
  setRuntimeQueueSyncLock(messages = [], ttlMs = 1800) {
    this.runtimeQueueSyncLockKey = this.buildRuntimeQueueSnapshotKey(messages);
    this.runtimeQueueSyncLockUntil = Date.now() + Math.max(300, Number(ttlMs || 0));
  },
  ensureRuntimeQueueSuppressionState() {
    if (!(this.runtimeQueueSuppressedMessageIds instanceof Set)) {
      this.runtimeQueueSuppressedMessageIds = new Set();
    }
    if (
      !this.runtimeGuidanceSuppressedTextCounts ||
      typeof this.runtimeGuidanceSuppressedTextCounts !== 'object' ||
      Array.isArray(this.runtimeGuidanceSuppressedTextCounts)
    ) {
      this.runtimeGuidanceSuppressedTextCounts = {};
    }
  },
  markRuntimeQueueSuppressedByManualStop() {
    this.ensureRuntimeQueueSuppressionState();
    const queueList = Array.isArray(this.runtimeQueuedMessages) ? this.runtimeQueuedMessages : [];
    queueList.forEach((item) => {
      const id = String(item?.id || '').trim();
      if (id) {
        this.runtimeQueueSuppressedMessageIds.add(id);
      }
    });

    const fallbackList = Array.isArray(this.runtimeGuidanceFallbackQueue)
      ? this.runtimeGuidanceFallbackQueue
      : [];
    fallbackList.forEach((item) => {
      const text = String(item || '').trim();
      if (!text) return;
      const counts = this.runtimeGuidanceSuppressedTextCounts;
      counts[text] = Number(counts[text] || 0) + 1;
    });
  },
  consumeSuppressedRuntimeGuidanceText(rawText) {
    this.ensureRuntimeQueueSuppressionState();
    const text = String(rawText || '').trim();
    if (!text) return false;
    const counts = this.runtimeGuidanceSuppressedTextCounts;
    const current = Number(counts[text] || 0);
    return Number.isFinite(current) && current > 0;
  },
  consumeSuppressedRuntimeQueueMessageId(rawId) {
    this.ensureRuntimeQueueSuppressionState();
    const id = String(rawId || '').trim();
    if (!id) return false;
    return this.runtimeQueueSuppressedMessageIds.has(id);
  },
  clearRuntimeQueueSuppressionState() {
    this.runtimeQueueSuppressedMessageIds = new Set();
    this.runtimeGuidanceSuppressedTextCounts = {};
  },
  pruneSuppressedRuntimeQueues() {
    this.ensureRuntimeQueueSuppressionState();
    const blockedIds = this.runtimeQueueSuppressedMessageIds;
    const currentQueue = Array.isArray(this.runtimeQueuedMessages)
      ? this.runtimeQueuedMessages
      : [];
    const nextQueue = currentQueue.filter((item) => {
      const id = String(item?.id || '').trim();
      if (!id) return false;
      return !blockedIds.has(id);
    });
    if (nextQueue.length !== currentQueue.length) {
      this.runtimeQueuedMessages = nextQueue;
      this.setRuntimeQueueSyncLock(nextQueue, 2200);
    }

    const currentFallback = Array.isArray(this.runtimeGuidanceFallbackQueue)
      ? this.runtimeGuidanceFallbackQueue
      : [];
    if (currentFallback.length > 0) {
      const keptFallback = [];
      currentFallback.forEach((item) => {
        const text = String(item || '').trim();
        if (!text) {
          return;
        }
        if (this.consumeSuppressedRuntimeGuidanceText(text)) {
          return;
        }
        keptFallback.push(text);
      });
      if (keptFallback.length !== currentFallback.length) {
        this.runtimeGuidanceFallbackQueue = keptFallback;
      }
    }
  },
  applyRuntimeQueuedMessages(messages = []) {
    this.ensureRuntimeQueueSuppressionState();
    const previousList = Array.isArray(this.runtimeQueuedMessages)
      ? this.runtimeQueuedMessages
      : [];
    const previousById = new Map(previousList.map((item) => [item?.id, item]));
    const previousIndexById = new Map(previousList.map((item, index) => [item?.id, index]));
    const limit = Math.max(1, Number(this.runtimeQueueLimit || 5));
    const normalizedRaw = (Array.isArray(messages) ? messages : [])
      .map((item) => {
        if (!item) return null;
        const id = String(item.id || '').trim();
        const text = String(item.text || '').trim();
        if (!id || !text) return null;
        const previous = previousById.get(id);
        const rawCreatedAt = Number(item.created_at ?? item.createdAt ?? Date.now());
        const rawFiles = Array.isArray(item.files)
          ? item.files
          : Array.isArray(previous?.files)
            ? previous.files
            : [];
        return {
          id,
          text,
          createdAt: Number.isFinite(rawCreatedAt) ? rawCreatedAt : Date.now(),
          source: previous?.source || 'user',
          files: rawFiles.filter((path) => typeof path === 'string' && path).slice(0, 9)
        };
      })
      .filter((item) => !!item);

    const dedupById = new Map();
    normalizedRaw.forEach((item) => {
      if (!item?.id || dedupById.has(item.id)) {
        return;
      }
      dedupById.set(item.id, item);
    });

    const normalized = Array.from(dedupById.values())
      .sort((a, b) => {
        const at = Number(a?.createdAt || 0);
        const bt = Number(b?.createdAt || 0);
        if (at !== bt) {
          return at - bt;
        }
        const ai = previousIndexById.has(a?.id)
          ? Number(previousIndexById.get(a?.id))
          : Number.MAX_SAFE_INTEGER;
        const bi = previousIndexById.has(b?.id)
          ? Number(previousIndexById.get(b?.id))
          : Number.MAX_SAFE_INTEGER;
        if (ai !== bi) {
          return ai - bi;
        }
        return String(a?.id || '').localeCompare(String(b?.id || ''));
      })
      .slice(0, limit);

    const unchanged =
      previousList.length === normalized.length &&
      previousList.every((item, index) => {
        const next = normalized[index];
        return (
          item?.id === next?.id &&
          item?.text === next?.text &&
          Number(item?.createdAt || 0) === Number(next?.createdAt || 0)
        );
      });

    if (unchanged) {
      return previousList;
    }
    this.runtimeQueuedMessages = normalized;
    return normalized;
  },
  async enqueueRuntimeQueuedMessage(rawMessage, rawFiles = []) {
    const text = (rawMessage || '').toString().trim();
    if (!text) {
      return false;
    }
    const files = (Array.isArray(rawFiles) ? rawFiles : [])
      .filter((path) => typeof path === 'string' && path)
      .slice(0, 9);
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      const taskId = taskStore.currentTaskId;
      if (!taskId) {
        this.uiPushToast({
          title: '暂不可堆积',
          message: '未检测到活跃任务，请稍后再试',
          type: 'warning'
        });
        return false;
      }
      const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/runtime_queue`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          message: text,
          files: files.length ? files : undefined
        })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || '堆积消息失败');
      }
      const nextQueue = this.applyRuntimeQueuedMessages(payload?.data?.messages || []) || [];
      this.setRuntimeQueueSyncLock(nextQueue, 2200);
      return true;
    } catch (error) {
      this.uiPushToast({
        title: '堆积失败',
        message: error?.message || '请稍后重试',
        type: 'error'
      });
      return false;
    }
  },
  async handleDeleteRuntimeMessage(messageId) {
    const targetId = String(messageId || '').trim();
    if (!targetId) {
      return;
    }
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      const taskId = taskStore.currentTaskId;
      if (!taskId) {
        const nextQueue = (this.runtimeQueuedMessages || []).filter(
          (item) => item?.id !== targetId
        );
        this.runtimeQueuedMessages = nextQueue;
        this.setRuntimeQueueSyncLock(nextQueue, 1200);
        return;
      }
      const response = await fetch(
        `/api/tasks/${encodeURIComponent(taskId)}/runtime_queue/${encodeURIComponent(targetId)}`,
        {
          method: 'DELETE'
        }
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || '删除失败');
      }
      const nextQueue = this.applyRuntimeQueuedMessages(payload?.data?.messages || []) || [];
      this.setRuntimeQueueSyncLock(nextQueue, 2200);
    } catch (error) {
      this.uiPushToast({
        title: '删除失败',
        message: error?.message || '请稍后重试',
        type: 'error'
      });
    }
  },
  async handleGuideRuntimeMessage(messageId) {
    const item = (this.runtimeQueuedMessages || []).find((entry) => entry?.id === messageId);
    if (!item?.text) {
      return;
    }
    if (!this.composerBusy) {
      const sent = !!(await this.sendMessage({
        presetText: String(item.text || '').trim(),
        source: 'runtime_queue_manual_guide',
        files: Array.isArray(item?.files) ? [...item.files] : []
      }));
      if (!sent) {
        this.uiPushToast({
          title: '发送失败',
          message: '引导消息未发出，请稍后重试',
          type: 'warning'
        });
        return;
      }
      const currentQueue = Array.isArray(this.runtimeQueuedMessages)
        ? [...this.runtimeQueuedMessages]
        : [];
      const nextQueue = currentQueue.filter((entry) => entry?.id !== item.id);
      if (nextQueue.length !== currentQueue.length) {
        this.runtimeQueuedMessages = nextQueue;
        this.setRuntimeQueueSyncLock(nextQueue, 2200);
      }
      this.ensureRuntimeQueueSuppressionState();
      if (item?.id) {
        this.runtimeQueueSuppressedMessageIds.delete(item.id);
      }
      return;
    }
    try {
      const { useTaskStore } = await import('../../../stores/task');
      const taskStore = useTaskStore();
      const taskId = taskStore.currentTaskId;
      if (!taskId) {
        this.uiPushToast({
          title: '暂不可引导',
          message: '未检测到活跃任务，请稍后再试',
          type: 'warning'
        });
        return;
      }
      const response = await fetch(
        `/api/tasks/${encodeURIComponent(taskId)}/runtime_queue/${encodeURIComponent(messageId)}/guide`,
        {
          method: 'POST'
        }
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload?.success) {
        throw new Error(payload?.error || '提交引导失败');
      }
      const nextQueue = this.applyRuntimeQueuedMessages(payload?.data?.messages || []) || [];
      this.setRuntimeQueueSyncLock(nextQueue, 2200);
      this.uiPushToast({
        title: '已设为引导对话',
        message: '将在下一次工具结果后插入到当前对话',
        type: 'success',
        duration: 1800
      });
    } catch (error) {
      this.uiPushToast({
        title: '引导失败',
        message: error?.message || '请稍后重试',
        type: 'error'
      });
    }
  },
  async tryAutoSendRuntimeQueuedMessages(reason = 'unspecified') {
    if (this.runtimeQueueAutoSendInProgress) {
      return false;
    }
    if (
      this.composerBusy ||
      this.streamingMessage ||
      this.taskInProgress ||
      this.stopRequested ||
      this.waitingForSubAgent
    ) {
      return false;
    }

    const fallbackQueue = Array.isArray(this.runtimeGuidanceFallbackQueue)
      ? this.runtimeGuidanceFallbackQueue
      : [];
    const runtimeQueue = Array.isArray(this.runtimeQueuedMessages)
      ? this.runtimeQueuedMessages
      : [];
    let nextText = '';
    let source = 'runtime_queue';
    let runtimeQueueMessageId = null;
    let nextFiles: string[] = [];
    let fallbackIndex = -1;

    if (fallbackQueue.length > 0) {
      for (let i = 0; i < fallbackQueue.length; i += 1) {
        const candidate = (fallbackQueue[i] || '').toString().trim();
        if (!candidate) {
          continue;
        }
        if (this.consumeSuppressedRuntimeGuidanceText(candidate)) {
          continue;
        }
        nextText = candidate;
        source = 'runtime_guidance_fallback';
        fallbackIndex = i;
        break;
      }
    }

    if (!nextText && runtimeQueue.length > 0) {
      for (let i = 0; i < runtimeQueue.length; i += 1) {
        const next = runtimeQueue[i];
        const candidateId = String(next?.id || '').trim();
        const candidateText = (next?.text || '').toString().trim();
        if (!candidateId || !candidateText) {
          continue;
        }
        if (this.consumeSuppressedRuntimeQueueMessageId(candidateId)) {
          continue;
        }
        nextText = candidateText;
        source = next?.source || 'runtime_queue';
        runtimeQueueMessageId = candidateId;
        nextFiles = Array.isArray(next?.files) ? [...next.files] : [];
        break;
      }
    }

    if (!nextText) {
      return false;
    }

    this.runtimeQueueAutoSendInProgress = true;
    let sent = false;
    try {
      sent = !!(await this.sendMessage({
        presetText: nextText,
        source,
        files: nextFiles
      }));
    } catch {
      sent = false;
    } finally {
      this.runtimeQueueAutoSendInProgress = false;
    }

    if (!sent) {
      return false;
    }

    if (source === 'runtime_guidance_fallback') {
      const current = Array.isArray(this.runtimeGuidanceFallbackQueue)
        ? [...this.runtimeGuidanceFallbackQueue]
        : [];
      const removeIndex =
        fallbackIndex >= 0
          ? fallbackIndex
          : current.findIndex((item) => String(item || '').trim() === nextText);
      if (removeIndex >= 0) {
        current.splice(removeIndex, 1);
        this.runtimeGuidanceFallbackQueue = current;
      }
    } else {
      const current = Array.isArray(this.runtimeQueuedMessages)
        ? [...this.runtimeQueuedMessages]
        : [];
      if (runtimeQueueMessageId) {
        const nextQueue = current.filter((item) => item?.id !== runtimeQueueMessageId);
        this.runtimeQueuedMessages = nextQueue;
        this.setRuntimeQueueSyncLock(nextQueue, 1500);
      } else {
        const removeIndex = current.findIndex(
          (item) => String(item?.text || '').trim() === nextText
        );
        if (removeIndex >= 0) {
          current.splice(removeIndex, 1);
          this.runtimeQueuedMessages = current;
          this.setRuntimeQueueSyncLock(current, 1500);
        }
      }
    }
    debugLog('[RuntimeQueue] auto-send success', {
      reason,
      source,
      messagePreview: nextText.slice(0, 60)
    });
    return true;
  }
};
