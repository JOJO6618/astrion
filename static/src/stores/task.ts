// @ts-nocheck
import { defineStore } from 'pinia';
import { debugLog, goalModeDebugLog } from '../app/methods/common';

const debugNotifyLog = (...args: any[]) => {
  void args;
};
const keyNotifyLog = (...args: any[]) => {
};
const jsonDebug = (...args: any[]) => {
};
const CONN_DIAG_PREFIX = '[CONN_DIAG]';
const TASK_POLL_DIAG_MAX = 2000;
// 默认关闭 console 输出（心跳类高频日志）；排障时通过 window.__CONN_DIAG__ = true
// 或 localStorage.connDiag = '1' 显式打开。内存记录 __CONN_DIAG_LOGS__ 始终收集。
const isConnDiagConsoleEnabled = () => {
  if (typeof window === 'undefined') return false;
  try {
    const w = window as any;
    if (w.__CONN_DIAG__ === true || w.__CONN_DIAG__ === '1') return true;
    const localFlag = window.localStorage?.getItem('connDiag');
    return localFlag === '1' || localFlag === 'true';
  } catch {
    return false;
  }
};
const taskPollDiag = (event: string, payload: Record<string, any> = {}) => {
  const ts = new Date().toISOString();
  const record = { ts, event, ...payload };
  try {
    if (typeof window !== 'undefined') {
      const w = window as any;
      if (!Array.isArray(w.__CONN_DIAG_LOGS__)) {
        w.__CONN_DIAG_LOGS__ = [];
      }
      w.__CONN_DIAG_LOGS__.push(record);
      if (w.__CONN_DIAG_LOGS__.length > TASK_POLL_DIAG_MAX) {
        w.__CONN_DIAG_LOGS__.splice(0, w.__CONN_DIAG_LOGS__.length - TASK_POLL_DIAG_MAX);
      }
    }
  } catch {
    // ignore
  }
  if (isConnDiagConsoleEnabled()) {
    console.log(CONN_DIAG_PREFIX, event, record);
  }
};

export const useTaskStore = defineStore('task', {
  state: () => ({
    currentTaskId: null as string | null,
    lastEventIndex: 0,
    pollingInterval: null as number | null,
    taskStatus: 'idle' as 'idle' | 'running' | 'succeeded' | 'failed' | 'canceled',
    isPolling: false,
    pollingError: null as string | null,
    pollingErrorCount: 0, // 新增错误计数
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
    isTaskCompleted: (state) => ['succeeded', 'failed', 'canceled', 'stopped'].includes(state.taskStatus)
  },

  actions: {
    async createTask(
      message: string,
      images: any[] = [],
      videos: any[] = [],
      conversationId: string | null = null,
      options: {
        model_key?: string | null;
        run_mode?: 'fast' | 'thinking' | null;
        thinking_mode?: boolean | null;
        message_source?: string | null;
        goal_mode?: boolean | null;
        skill_refs?: Array<{ name?: string; path: string }> | null;
        files?: string[] | null;
        eventHandler?: (event: any) => void;
      } = {}
    ) {
      try {
        debugLog('[Task] 创建任务:', { message, conversationId });

        const response = await fetch('/api/tasks', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            message,
            images,
            videos,
            conversation_id: conversationId,
            model_key: options.model_key ?? undefined,
            run_mode: options.run_mode ?? undefined,
            thinking_mode:
              typeof options.thinking_mode === 'boolean' ? options.thinking_mode : undefined,
            message_source: options.message_source ?? undefined,
            goal_mode: options.goal_mode === true ? true : undefined,
            skill_refs: Array.isArray(options.skill_refs) ? options.skill_refs : undefined,
            files:
              Array.isArray(options.files) && options.files.length ? options.files : undefined
          })
        });

        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.error || '创建任务失败');
        }

        const result = await response.json();
        if (!result.success) {
          throw new Error(result.error || '创建任务失败');
        }

        this.currentTaskId = result.data.task_id;
        this.taskStatus = result.data.status;
        this.lastEventIndex = 0;
        this.taskCreatedAt = result.data.created_at;
        this.runtimeQueueSnapshotKey = '';
        this.pollingError = null;
        this.pollingErrorCount = 0;
        this.pollingWarned = false;

        debugLog('[Task] 任务创建成功:', result.data.task_id);
        taskPollDiag('task-created', {
          taskId: result.data.task_id,
          conversationId
        });

        // 立即开始轮询
        this.startPolling(options.eventHandler);

        return result.data;
      } catch (error) {
        debugLog('[Task] 创建任务失败:', error);
        taskPollDiag('task-create-failed', {
          error: error?.message || String(error),
          conversationId
        });
        throw error;
      }
    },

    async pollTaskEvents(eventHandler: (event: any) => void) {
      if (!this.currentTaskId) {
        debugLog('[Task] 没有活跃任务，停止轮询');
        this.stopPolling('no-active-task');
        return;
      }

      if (this.pollingInFlight) {
        return;
      }

      this.pollingInFlight = true;
      const taskId = this.currentTaskId;
      const fromOffset = this.lastEventIndex;
      const requestId = `${Date.now()}-${++this.pollingSeq}`;
      const startedAt = Date.now();

      try {
        const response = await fetch(
          `/api/tasks/${taskId}?from=${fromOffset}`,
          {
            signal: AbortSignal.timeout(this.pollingRequestTimeoutMs),
            headers: {
              'X-Task-Poll': requestId
            }
          }
        );

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const result = await response.json();
        if (!result.success) {
          throw new Error(result.error || '轮询任务失败');
        }

        const data = result.data;
        // 如果用户已切换/新建对话并清除了当前轮询，忽略这次已在路上的旧响应。
        // 否则旧任务事件可能被写入当前新对话视图。
        if (this.currentTaskId !== taskId) {
          taskPollDiag('task-poll-stale-response-ignored', {
            requestId,
            responseTaskId: data?.task_id || taskId,
            activeTaskId: this.currentTaskId,
            from: fromOffset
          });
          return;
        }
        const eventsCount = Array.isArray(data?.events) ? data.events.length : 0;
        jsonDebug('taskStore.poll:response', {
          taskId,
          from: fromOffset,
          status: data?.status,
          nextOffset: data?.next_offset,
          eventsCount
        });
        if (eventsCount > 0 || this.pollingErrorCount > 0 || data?.status !== 'running' || fromOffset === 0) {
          taskPollDiag('task-poll-ok', {
            requestId,
            taskId,
            from: fromOffset,
            nextOffset: data?.next_offset,
            status: data?.status,
            eventsCount,
            elapsedMs: Date.now() - startedAt
          });
        }

        // 更新任务状态
        goalModeDebugLog('taskStore.poll:status', {
          taskId,
          from: fromOffset,
          status: data.status,
          isTaskCompleted: this.isTaskCompleted,
        });
        this.taskStatus = data.status;
        this.taskUpdatedAt = data.updated_at;
        const runtimeQueueMessages = Array.isArray(data?.runtime_queued_messages)
          ? data.runtime_queued_messages
          : [];
        const runtimeQueueSnapshotKey = JSON.stringify(
          runtimeQueueMessages.map((item: any) => [
            String(item?.id || ''),
            String(item?.text || '')
          ])
        );
        if (runtimeQueueSnapshotKey !== this.runtimeQueueSnapshotKey) {
          this.runtimeQueueSnapshotKey = runtimeQueueSnapshotKey;
          try {
            eventHandler({
              type: 'runtime_queue_sync',
              data: {
                task_id: data?.task_id || this.currentTaskId,
                conversation_id: data?.conversation_id || null,
                messages: runtimeQueueMessages
              }
            });
          } catch (err) {
            console.warn('[Task] 处理 runtime_queue_sync 失败:', err);
          }
        }
        let sawTaskCompleteEvent = false;
        let sawTaskStoppedEvent = false;

        // 处理新事件
        if (data.events && data.events.length > 0) {
          debugLog(`[Task] 收到 ${data.events.length} 个新事件`);

          for (const event of data.events) {
            if (this.currentTaskId !== taskId) {
              taskPollDiag('task-poll-stale-event-loop-abort', {
                requestId,
                responseTaskId: data?.task_id || taskId,
                activeTaskId: this.currentTaskId
              });
              return;
            }
            if (
              event?.type === 'task_complete' ||
              event?.type === 'task_stopped' ||
              event?.type === 'error'
            ) {
              jsonDebug('taskStore.poll:event', {
                idx: event?.idx,
                type: event?.type,
                data: event?.data
              });
            }
            if (event?.type === 'task_complete') {
              sawTaskCompleteEvent = true;
            }
            if (event?.type === 'task_stopped') {
              sawTaskStoppedEvent = true;
            }
            try {
              eventHandler(event);
            } catch (err) {
              console.error('[Task] 处理事件失败:', err, event);
            }
          }
          const interesting = data.events.filter((e: any) =>
            [
              'user_message',
              'sub_agent_waiting',
              'task_complete',
              'task_stopped',
              'error'
            ].includes(e?.type)
          );
          if (interesting.length > 0) {
            keyNotifyLog('[DEBUG_NOTIFY_KEY][task] events', {
              taskId: this.currentTaskId,
              from: this.lastEventIndex,
              nextOffset: data.next_offset,
              count: interesting.length,
              items: interesting.map((e: any) => ({
                idx: e?.idx,
                type: e?.type,
                sub_agent_notice: !!e?.data?.sub_agent_notice,
                has_running_sub_agents: e?.data?.has_running_sub_agents,
                has_running_background_commands: e?.data?.has_running_background_commands,
                task_id: e?.data?.task_id
              }))
            });
          }

          this.lastEventIndex = data.next_offset;
        }

        // 兜底：如果后端状态已经是 canceled 但未返回 task_stopped 事件，
        // 补发一个本地停止事件，确保前端解除“停止中”状态。
        if (data.status === 'canceled' && !sawTaskStoppedEvent) {
          debugLog('[Task] 状态已取消但未收到 task_stopped，补发本地事件');
          jsonDebug('taskStore.poll:emit-synthetic-task-stopped', {
            taskId: data.task_id || this.currentTaskId,
            conversation_id: data.conversation_id || null
          });
          try {
            eventHandler({
              type: 'task_stopped',
              data: {
                task_id: data.task_id || this.currentTaskId,
                conversation_id: data.conversation_id || null,
                synthetic: true
              }
            });
          } catch (err) {
            console.error('[Task] 处理补发 task_stopped 失败:', err);
          }
        }

        // 兜底：某些异常流程（如工具参数解析失败后恢复执行）可能没有 task_complete 事件，
        // 但任务状态已经 succeeded。补发一次完成事件，避免前端一直显示“工作中”。
        if (data.status === 'succeeded' && !sawTaskCompleteEvent) {
          debugLog('[Task] 状态已成功但未收到 task_complete，补发本地事件');
          jsonDebug('taskStore.poll:emit-synthetic-task-complete', {
            taskId: data.task_id || this.currentTaskId,
            conversation_id: data.conversation_id || null
          });
          try {
            eventHandler({
              type: 'task_complete',
              data: {
                task_id: data.task_id || this.currentTaskId,
                conversation_id: data.conversation_id || null,
                synthetic: true,
                has_running_sub_agents: false,
                has_running_background_commands: false
              }
            });
          } catch (err) {
            console.error('[Task] 处理补发 task_complete 失败:', err);
          }
        }

        // 如果任务已完成，停止轮询
        if (this.isTaskCompleted) {
          debugLog('[Task] 任务已完成，停止轮询:', this.taskStatus);
          this.stopPolling(`task-${this.taskStatus}`);
        }

        // 重置错误计数
        this.pollingError = null;
        this.pollingErrorCount = 0;
        this.pollingWarned = false;
      } catch (error) {
        this.pollingErrorCount++;
        this.pollingError = error.message;
        const message = error?.message || String(error);
        const isTimeoutLike = /timeout|timed out|abort|aborted/i.test(message);

        console.error('[Task] 轮询失败:', error);
        taskPollDiag('task-poll-failed', {
          requestId,
          taskId,
          from: fromOffset,
          elapsedMs: Date.now() - startedAt,
          error: message,
          isTimeoutLike,
          pollingErrorCount: this.pollingErrorCount
        });
        debugNotifyLog('[DEBUG_NOTIFY][task] pollTaskEvents:error', {
          taskId: this.currentTaskId,
          from: this.lastEventIndex,
          error: error?.message || String(error),
          pollingErrorCount: this.pollingErrorCount
        });

        // 任务不存在时结束轮询
        if (/HTTP 404|HTTP 410/.test(message)) {
          this.stopPolling('task-not-found');
          return;
        }

        // 连续失败达到阈值仅告警，不直接停轮询（避免后端仍运行但前端停止更新）
        if (this.pollingErrorCount >= this.pollingFailThreshold && !this.pollingWarned) {
          this.pollingWarned = true;
          if ((window as any).__vueApp?.uiPushToast) {
            (window as any).__vueApp.uiPushToast({
              title: '轮询波动',
              message: '消息更新暂时不稳定，正在自动重试',
              type: 'warning',
              duration: 5000
            });
          }
        }
      } finally {
        this.pollingInFlight = false;
      }
    },

    startPolling(eventHandler?: (event: any) => void) {
      if (this.isPolling) {
        debugLog('[Task] 轮询已在运行');
        goalModeDebugLog('taskStore.startPolling_already_running', {
          taskId: this.currentTaskId,
          lastEventIndex: this.lastEventIndex
        });
        debugNotifyLog('[DEBUG_NOTIFY][task] startPolling:already-running', {
          taskId: this.currentTaskId,
          lastEventIndex: this.lastEventIndex
        });
        return;
      }

      if (!this.currentTaskId) {
        debugLog('[Task] 没有任务ID，无法启动轮询');
        goalModeDebugLog('taskStore.startPolling_no_task_id');
        return;
      }

      debugLog('[Task] 启动轮询:', this.currentTaskId);
      goalModeDebugLog('taskStore.startPolling', {
        taskId: this.currentTaskId,
        lastEventIndex: this.lastEventIndex,
        hasHandler: !!(eventHandler || (window as any).__taskEventHandler)
      });
      debugNotifyLog('[DEBUG_NOTIFY][task] startPolling', {
        taskId: this.currentTaskId,
        lastEventIndex: this.lastEventIndex
      });
      this.isPolling = true;
      this.pollingError = null;
      this.pollingErrorCount = 0;
      this.pollingWarned = false;

      // 如果没有传入 eventHandler，从根实例获取
      const handler = eventHandler || (window as any).__taskEventHandler;

      if (!handler) {
        console.error('[Task] 没有事件处理器，无法启动轮询');
        taskPollDiag('task-poll-start-failed-no-handler', {
          taskId: this.currentTaskId
        });
        this.isPolling = false;
        return;
      }

      taskPollDiag('task-poll-start', {
        taskId: this.currentTaskId,
        from: this.lastEventIndex,
        intervalMs: this.pollingIntervalMs,
        timeoutMs: this.pollingRequestTimeoutMs
      });

      // 立即执行一次
      void this.pollTaskEvents(handler);

      // 设置定时轮询（单飞由 pollTaskEvents 内部保证）
      this.pollingInterval = window.setInterval(() => {
        void this.pollTaskEvents(handler);
      }, this.pollingIntervalMs);
    },

    resumeTask(
      taskId: string,
      options: {
        status?: 'running' | 'pending' | 'succeeded' | 'failed' | 'canceled';
        resetOffset?: boolean;
        eventHandler?: (event: any) => void;
      } = {}
    ) {
      if (!taskId) {
        goalModeDebugLog('taskStore.resumeTask_no_task_id');
        return;
      }
      goalModeDebugLog('taskStore.resumeTask', {
        incomingTaskId: taskId,
        currentTaskId: this.currentTaskId,
        isPolling: this.isPolling,
        resetOffset: options?.resetOffset !== false
      });
      debugNotifyLog('[DEBUG_NOTIFY][task] resumeTask', {
        incomingTaskId: taskId,
        currentTaskId: this.currentTaskId,
        options
      });
      keyNotifyLog('[DEBUG_NOTIFY_KEY][task] resumeTask', {
        incomingTaskId: taskId,
        currentTaskId: this.currentTaskId,
        resetOffset: options?.resetOffset !== false
      });
      const isSameTask = !!this.currentTaskId && this.currentTaskId === taskId;
      const wasPolling = this.isPolling;
      if (this.currentTaskId && this.currentTaskId !== taskId) {
        this.stopPolling();
      }
      this.currentTaskId = taskId;
      this.taskStatus = options.status || 'running';
      // resetOffset 收编：仅允许「真正的冷启动恢复」（不同任务或轮询已停）归零偏移。
      // 同任务且轮询在跑时归零，下一次轮询会从 0 全量重拉事件；若去重集合恰好
      // 被清（对账/恢复路径），进度块会整份重复追加。
      if (options.resetOffset !== false && !(isSameTask && wasPolling)) {
        this.lastEventIndex = 0;
      } else if (options.resetOffset !== false) {
        goalModeDebugLog('taskStore.resumeTask_resetOffset_skipped', {
          taskId,
          reason: 'same-task-polling-active',
          lastEventIndex: this.lastEventIndex
        });
      }
      this.pollingError = null;
      this.runtimeQueueSnapshotKey = '';
      this.startPolling(options.eventHandler);
      goalModeDebugLog('taskStore.resumeTask_started', {
        taskId,
        isPolling: this.isPolling,
        lastEventIndex: this.lastEventIndex
      });
    },

    stopPolling(reason = 'manual') {
      taskPollDiag('task-poll-stop', {
        reason,
        taskId: this.currentTaskId,
        status: this.taskStatus,
        from: this.lastEventIndex,
        pollingErrorCount: this.pollingErrorCount
      });
      if (this.pollingInterval) {
        debugLog('[Task] 停止轮询');
        clearInterval(this.pollingInterval);
        this.pollingInterval = null;
      }
      this.isPolling = false;
      this.pollingInFlight = false;
      this.pollingWarned = false;
      this.runtimeQueueSnapshotKey = '';
      goalModeDebugLog('taskStore.stopPolling', { reason, taskStatus: this.taskStatus, currentTaskId: this.currentTaskId });
      this.currentTaskId = null; // 清除任务 ID
    },

    async cancelTask() {
      if (!this.currentTaskId) {
        return;
      }

      try {
        debugLog('[Task] 取消任务:', this.currentTaskId);

        const response = await fetch(`/api/tasks/${this.currentTaskId}/cancel`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });

        if (!response.ok) {
          throw new Error('取消任务失败');
        }

        const result = await response.json();
        if (!result.success) {
          throw new Error(result.error || '取消任务失败');
        }

        debugLog('[Task] 已发送取消请求，等待后端停止确认');
      } catch (error) {
        console.error('[Task] 取消任务失败:', error);
        throw error;
      }
    },

    async loadRunningTask(conversationId: string | null = null) {
      try {
        debugLog('[Task] 查找运行中的任务');
        goalModeDebugLog('taskStore.loadRunningTask_start', { conversationId });

        const response = await fetch('/api/tasks');
        if (!response.ok) {
          throw new Error('获取任务列表失败');
        }

        const result = await response.json();
        if (!result.success) {
          throw new Error(result.error || '获取任务列表失败');
        }

        // 查找当前对话的活动任务
        const activeStatuses = new Set(['pending', 'running', 'cancel_requested']);
        const runningTask = result.data.find(
          (task: any) =>
            activeStatuses.has(String(task.status || '')) &&
            (!conversationId || task.conversation_id === conversationId)
        );

        if (runningTask) {
          debugLog('[Task] 发现运行中的任务:', runningTask.task_id);
          goalModeDebugLog('taskStore.loadRunningTask_found', {
            taskId: runningTask.task_id,
            status: runningTask.status,
            conversationId: runningTask.conversation_id
          });

          this.currentTaskId = runningTask.task_id;
          this.taskStatus = runningTask.status;
          this.taskCreatedAt = runningTask.created_at;
          this.taskUpdatedAt = runningTask.updated_at;
          this.pollingError = null;
          this.pollingErrorCount = 0;
          this.pollingWarned = false;
          this.runtimeQueueSnapshotKey = '';

          // 获取任务详情，计算已处理的事件数量
          try {
            const detailResponse = await fetch(`/api/tasks/${runningTask.task_id}`);
            if (detailResponse.ok) {
              const detailResult = await detailResponse.json();
              if (detailResult.success && detailResult.data.events) {
                // 设置为当前事件数量，只获取新事件
                this.lastEventIndex =
                  detailResult.data.next_offset || detailResult.data.events.length;
                debugLog('[Task] 设置起始偏移量:', this.lastEventIndex);
              } else {
                this.lastEventIndex = 0;
              }
            } else {
              this.lastEventIndex = 0;
            }
          } catch (error) {
            console.warn('[Task] 获取任务详情失败，从头开始:', error);
            this.lastEventIndex = 0;
          }

          return runningTask;
        }

        debugLog('[Task] 没有运行中的任务');
        goalModeDebugLog('taskStore.loadRunningTask_not_found', { conversationId });
        return null;
      } catch (error) {
        console.error('[Task] 加载运行中任务失败:', error);
        goalModeDebugLog('taskStore.loadRunningTask_error', {
          conversationId,
          error: String(error)
        });
        return null;
      }
    },

    clearTask() {
      debugLog('[Task] 清理任务状态');
      this.stopPolling();
      this.currentTaskId = null;
      this.lastEventIndex = 0;
      this.taskStatus = 'idle';
      this.pollingError = null;
      this.pollingErrorCount = 0;
      this.pollingWarned = false;
      this.taskCreatedAt = null;
      this.taskUpdatedAt = null;
      this.runtimeQueueSnapshotKey = '';
    },

    resetForNewConversation() {
      debugLog('[Task] 重置任务状态（新对话）');
      this.stopPolling();
      this.currentTaskId = null;
      this.lastEventIndex = 0;
      this.taskStatus = 'idle';
      this.pollingError = null;
      this.pollingErrorCount = 0;
      this.pollingWarned = false;
      this.runtimeQueueSnapshotKey = '';
    }
  }
});
