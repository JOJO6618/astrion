// @ts-nocheck
import { defineStore } from 'pinia';
import { t } from '@/locales';
import { debugLog, goalModeDebugLog } from '../app/methods/common';
import {
  snapshotTaskState,
  runtimeQueueSnapshot,
  terminalFallbackEvent,
  type EventHandler,
  type TaskStatus
} from './taskPolling';

const jsonDebug = (...args: any[]) => void args;
const TASK_POLL_DIAG_MAX = 2000;
// 内存诊断始终记录；console 仅在显式打开 connDiag 时输出。
const taskPollDiag = (event: string, payload: Record<string, any> = {}) => {
  const record = { ts: new Date().toISOString(), event, ...payload };
  try {
    if (typeof window === 'undefined') return;
    const w = window as any;
    if (!Array.isArray(w.__CONN_DIAG_LOGS__)) w.__CONN_DIAG_LOGS__ = [];
    w.__CONN_DIAG_LOGS__.push(record);
    if (w.__CONN_DIAG_LOGS__.length > TASK_POLL_DIAG_MAX) {
      w.__CONN_DIAG_LOGS__.splice(0, w.__CONN_DIAG_LOGS__.length - TASK_POLL_DIAG_MAX);
    }
    const flag = w.localStorage?.getItem('connDiag');
    if (w.__CONN_DIAG__ === true || w.__CONN_DIAG__ === '1' || flag === '1' || flag === 'true') {
      console.log('[CONN_DIAG]', event, record);
    }
  } catch {
    /* ignore diagnostic storage errors */
  }
};

export const useTaskStore = defineStore('task', {
  state: () => ({
    currentTaskId: null as string | null,
    // stop 保留游标所属任务，防止同任务暂停后 resume 隐式重放；clear 才清除。
    cursorTaskId: null as string | null,
    lastEventIndex: 0,
    pollGeneration: 0,
    pollingRequestId: null as string | null,
    pollingAbortController: null as AbortController | null,
    pollingInterval: null as number | null,
    taskStatus: 'idle' as TaskStatus,
    isPolling: false,
    pollingError: null as string | null,
    pollingErrorCount: 0,
    pollingInFlight: false,
    pollingSeq: 0,
    pollingWarned: false,
    pollingRequestTimeoutMs: 12000,
    pollingFailThreshold: 8,
    pollingIntervalMs: 250,
    taskCreatedAt: null as number | null,
    taskUpdatedAt: null as number | null,
    runtimeQueueSnapshotKey: ''
  }),
  getters: {
    hasActiveTask: (state) => state.currentTaskId !== null && state.taskStatus === 'running',
    isTaskCompleted: (state) =>
      ['succeeded', 'failed', 'canceled', 'stopped'].includes(state.taskStatus)
  },
  actions: {
    async pollTaskEvents(eventHandler: EventHandler) {
      if (!this.currentTaskId) {
        this.stopPolling('no-active-task');
        return;
      }
      if (this.pollingInFlight) return;
      const taskId = this.currentTaskId;
      const generation = this.pollGeneration;
      const fromOffset = this.lastEventIndex;
      const requestId = `${Date.now()}-${++this.pollingSeq}`;
      const controller = new AbortController();
      const startedAt = Date.now();
      this.pollingInFlight = true;
      this.pollingRequestId = requestId;
      this.pollingAbortController = controller;
      const ownsRequest = () =>
        this.pollGeneration === generation &&
        this.currentTaskId === taskId &&
        this.pollingRequestId === requestId;
      let timedOut = false;
      const timeout = setTimeout(() => {
        if (!ownsRequest()) return;
        timedOut = true;
        controller.abort(new DOMException('Task poll timed out', 'TimeoutError'));
      }, this.pollingRequestTimeoutMs);
      const emit = (event: any) => {
        if (!ownsRequest()) return;
        try {
          eventHandler(event);
        } catch (error) {
          console.error('[Task] 处理事件失败:', error, event);
        }
      };
      try {
        const response = await fetch(`/api/tasks/${taskId}?from=${fromOffset}`, {
          signal: controller.signal,
          headers: { 'X-Task-Poll': requestId }
        });
        if (!ownsRequest()) {
          taskPollDiag('task-poll-stale-response-ignored', {
            requestId,
            responseTaskId: taskId,
            activeTaskId: this.currentTaskId,
            generation,
            from: fromOffset
          });
          return;
        }
        if (timedOut) throw controller.signal.reason;
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (!ownsRequest()) {
          taskPollDiag('task-poll-stale-response-ignored', {
            requestId,
            responseTaskId: result?.data?.task_id || taskId,
            activeTaskId: this.currentTaskId,
            generation,
            from: fromOffset
          });
          return;
        }
        if (timedOut) throw controller.signal.reason;
        if (!result.success) throw new Error(result.error || t('stores.pollTaskFailed'));
        const data = result.data;
        const events = Array.isArray(data?.events) ? data.events : [];
        jsonDebug('taskStore.poll:response', {
          taskId,
          from: fromOffset,
          status: data.status,
          nextOffset: data.next_offset,
          eventsCount: events.length
        });
        if (
          events.length ||
          this.pollingErrorCount ||
          data.status !== 'running' ||
          fromOffset === 0
        ) {
          taskPollDiag('task-poll-ok', {
            requestId,
            taskId,
            generation,
            from: fromOffset,
            nextOffset: data.next_offset,
            status: data.status,
            eventsCount: events.length,
            elapsedMs: Date.now() - startedAt
          });
        }
        // 缺口包括 from=0。仅通知上层重取快照，本批状态/队列/事件/游标都不消费。
        const windowStart = Number(data.window_start ?? 0);
        if (fromOffset < windowStart) {
          debugLog('[Task] 事件窗口缺口:', { from: fromOffset, windowStart });
          taskPollDiag('task-poll-window-gap', {
            requestId,
            taskId,
            generation,
            from: fromOffset,
            windowStart,
            nextOffset: data.next_offset
          });
          emit({
            type: 'event_window_gap',
            data: {
              task_id: data.task_id || taskId,
              conversation_id: data.conversation_id || null,
              from_offset: fromOffset,
              window_start: windowStart
            }
          });
          return;
        }
        goalModeDebugLog('taskStore.poll:status', {
          taskId,
          from: fromOffset,
          status: data.status,
          isTaskCompleted: this.isTaskCompleted
        });
        this.taskStatus = data.status;
        this.taskUpdatedAt = data.updated_at;
        const queueSnapshot = runtimeQueueSnapshot(data, taskId);
        if (queueSnapshot.key !== this.runtimeQueueSnapshotKey) {
          this.runtimeQueueSnapshotKey = queueSnapshot.key;
          emit(queueSnapshot.event);
          if (!ownsRequest()) return;
        }
        let sawTaskCompleteEvent = false;
        let sawTaskStoppedEvent = false;
        for (const event of events) {
          if (!ownsRequest()) {
            taskPollDiag('task-poll-stale-event-loop-abort', { requestId, taskId, generation });
            return;
          }
          if (['task_complete', 'task_stopped', 'error'].includes(event?.type)) {
            jsonDebug('taskStore.poll:event', {
              idx: event.idx,
              type: event.type,
              data: event.data
            });
          }
          if (event?.type === 'task_complete') sawTaskCompleteEvent = true;
          if (event?.type === 'task_stopped') sawTaskStoppedEvent = true;
          emit(event);
          // 终态 handler 可以清 store 或启动新任务，甚至重新接管同一 task ID。
          if (!ownsRequest()) return;
        }
        if (!ownsRequest()) return;
        const nextOffset = Number(data.next_offset);
        if (Number.isSafeInteger(nextOffset) && nextOffset >= fromOffset) {
          this.lastEventIndex = nextOffset;
          this.cursorTaskId = taskId;
        }
        const terminalEvent = terminalFallbackEvent(
          data,
          taskId,
          queueSnapshot.messages,
          sawTaskCompleteEvent,
          sawTaskStoppedEvent
        );
        if (terminalEvent) {
          const logName = data.status === 'canceled' ? 'task-stopped' : 'task-complete';
          jsonDebug(`taskStore.poll:emit-synthetic-${logName}`, { taskId });
          emit(terminalEvent);
          if (!ownsRequest()) return;
        }
        if (!ownsRequest()) return;
        this.pollingError = null;
        this.pollingErrorCount = 0;
        this.pollingWarned = false;
        if (this.isTaskCompleted) {
          debugLog('[Task] 任务已完成，停止轮询:', this.taskStatus);
          this.stopPolling(`task-${this.taskStatus}`);
        }
      } catch (error) {
        // stop 的 abort、旧 404、旧 timeout 均无权影响新的接管 session。
        if (!ownsRequest()) return;
        const message = error?.message || String(error);
        this.pollingErrorCount++;
        this.pollingError = message;
        const isTimeoutLike = timedOut || /timeout|timed out|abort|aborted/i.test(message);
        console.error('[Task] 轮询失败:', error);
        taskPollDiag('task-poll-failed', {
          requestId,
          taskId,
          generation,
          from: fromOffset,
          elapsedMs: Date.now() - startedAt,
          error: message,
          isTimeoutLike,
          pollingErrorCount: this.pollingErrorCount
        });
        if (/HTTP 404|HTTP 410/.test(message)) {
          this.stopPolling('task-not-found');
          return;
        }
        if (this.pollingErrorCount >= this.pollingFailThreshold && !this.pollingWarned) {
          this.pollingWarned = true;
          (window as any).__vueApp?.uiPushToast?.({
            title: t('stores.pollingFluctuation'),
            message: t('stores.pollingUnstableRetry'),
            type: 'warning',
            duration: 5000
          });
        }
      } finally {
        clearTimeout(timeout);
        // finally 仅释放自己的 singleflight；不能清除后代请求的 controller/标志。
        if (ownsRequest()) {
          this.pollingInFlight = false;
          this.pollingRequestId = null;
          this.pollingAbortController = null;
        }
      }
    },

    startPolling(eventHandler: EventHandler) {
      if (this.isPolling) {
        goalModeDebugLog('taskStore.startPolling_already_running', {
          taskId: this.currentTaskId,
          lastEventIndex: this.lastEventIndex
        });
        return;
      }
      if (!this.currentTaskId) {
        goalModeDebugLog('taskStore.startPolling_no_task_id');
        return;
      }
      const handler = eventHandler;
      if (!handler) {
        console.error('[Task] 没有事件处理器，无法启动轮询');
        taskPollDiag('task-poll-start-failed-no-handler', { taskId: this.currentTaskId });
        return;
      }
      const generation = this.pollGeneration;
      const taskId = this.currentTaskId;
      this.isPolling = true;
      this.pollingError = null;
      this.pollingErrorCount = 0;
      this.pollingWarned = false;
      taskPollDiag('task-poll-start', {
        taskId,
        generation,
        from: this.lastEventIndex,
        intervalMs: this.pollingIntervalMs,
        timeoutMs: this.pollingRequestTimeoutMs
      });
      goalModeDebugLog('taskStore.startPolling', {
        taskId,
        lastEventIndex: this.lastEventIndex,
        hasHandler: true
      });
      const tick = () => {
        if (this.isPolling && this.pollGeneration === generation && this.currentTaskId === taskId) {
          void this.pollTaskEvents(handler);
        }
      };
      // 先注册 interval，保证立即请求同步失败/handler 停止时也能正确清理它。
      this.pollingInterval = window.setInterval(tick, this.pollingIntervalMs);
      tick();
    },

    // 调用方完成 snapshot hydrate 后，原子安装绝对 cursor，仅续取快照之后的事件。
    attachSnapshot(task: any, nextOffset: number, eventHandler: EventHandler) {
      const snapshotState = snapshotTaskState(task, nextOffset);
      if (typeof eventHandler !== 'function')
        throw new TypeError('Snapshot requires an owned event handler');
      this.stopPolling('snapshot-attach');
      this.$patch(snapshotState);
      this.startPolling(eventHandler);
    },

    stopPolling(reason = 'manual') {
      taskPollDiag('task-poll-stop', {
        reason,
        taskId: this.currentTaskId,
        generation: this.pollGeneration,
        status: this.taskStatus,
        from: this.lastEventIndex,
        pollingErrorCount: this.pollingErrorCount
      });
      // 必须先撤销所有权再 abort；abort listener 可以同步触发请求失败。
      this.pollGeneration++;
      const controller = this.pollingAbortController;
      this.pollingRequestId = null;
      this.pollingAbortController = null;
      this.pollingInFlight = false;
      if (this.pollingInterval !== null) clearInterval(this.pollingInterval);
      this.pollingInterval = null;
      this.isPolling = false;
      this.pollingWarned = false;
      this.runtimeQueueSnapshotKey = '';
      this.currentTaskId = null;
      controller?.abort();
      goalModeDebugLog('taskStore.stopPolling', {
        reason,
        taskStatus: this.taskStatus,
        currentTaskId: this.currentTaskId
      });
    },

    async cancelTask() {
      if (!this.currentTaskId) return;
      try {
        debugLog('[Task] 取消任务:', this.currentTaskId);
        const response = await fetch(`/api/tasks/${this.currentTaskId}/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });
        if (!response.ok) throw new Error(t('stores.cancelTaskFailed'));
        const result = await response.json();
        if (!result.success) throw new Error(result.error || t('stores.cancelTaskFailed'));
        debugLog('[Task] 已发送取消请求，等待后端停止确认');
      } catch (error) {
        console.error('[Task] 取消任务失败:', error);
        throw error;
      }
    },

    clearTask() {
      debugLog('[Task] 清理任务状态');
      this.stopPolling('clear-task');
      this.cursorTaskId = null;
      this.lastEventIndex = 0;
      this.taskStatus = 'idle';
      this.pollingError = null;
      this.pollingErrorCount = 0;
      this.taskCreatedAt = null;
      this.taskUpdatedAt = null;
    }
  }
});
