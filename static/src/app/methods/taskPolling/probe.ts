// @ts-nocheck
import { debugLog } from '../common';
import { useTaskStore } from '../../../stores/task';
import { debugNotifyLog } from './shared';

export const probeMethods = {
  scheduleTodoListRefresh(delayMs = 120) {
    const delay = Number.isFinite(delayMs) ? Math.max(0, Number(delayMs)) : 120;
    if (this._todoRefreshTimer) {
      clearTimeout(this._todoRefreshTimer);
      this._todoRefreshTimer = null;
    }
    this._todoRefreshTimer = setTimeout(() => {
      this._todoRefreshTimer = null;
      if (typeof this.fetchTodoList === 'function') {
        Promise.resolve(this.fetchTodoList()).catch(() => {});
      }
    }, delay);
  },
  // ---------- 运行状态对账（事件为主，对账纠偏） ----------
  // 架构：事件流（250ms 任务轮询 + socket）负责即时性；本对账循环每 2.5s 调用
  // GET /api/conversations/<id>/running-status 获取服务端权威聚合状态，负责正确性。
  // 冲突以对账为准：恢复方向立即执行，清理方向需连续 2 次服务端空闲确认
  // （防止 notice 任务创建间隙、多智能体 idle dispatch 间隙造成误清抖动）。
  startRunningStateReconcile() {
    if (this.runningStateReconcileTimer) {
      return;
    }
    this._runningStateIdleStreak = 0;
    this.runningStateReconcileTimer = setInterval(() => {
      void this.reconcileRunningStateOnce();
    }, 2500);
    void this.reconcileRunningStateOnce();
  },
  stopRunningStateReconcile() {
    if (this.runningStateReconcileTimer) {
      clearInterval(this.runningStateReconcileTimer);
      this.runningStateReconcileTimer = null;
    }
    this._runningStateIdleStreak = 0;
  },
  async reconcileRunningStateOnce() {
    const conversationId = this.currentConversationId;
    if (!conversationId) {
      return;
    }
    let status: any = null;
    try {
      const wsParam =
        (this.versioningHostMode || this.dockerProjectMode) && this.currentHostWorkspaceId
          ? `?workspace_id=${encodeURIComponent(this.currentHostWorkspaceId)}`
          : '';
      const resp = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}/running-status${wsParam}`
      );
      if (!resp.ok) {
        return; // 网络/鉴权异常不做任何状态变更，避免误清
      }
      const result = await resp.json().catch(() => ({}));
      if (!result?.success || !result?.data) {
        return;
      }
      status = result.data;
    } catch (error) {
      return; // 请求失败不做任何状态变更
    }
    if (this.currentConversationId !== conversationId) {
      return; // 请求期间切换了对话，丢弃过期结果
    }

    const taskStore = useTaskStore();

    if (status.is_truly_active) {
      this._runningStateIdleStreak = 0;
      // 恢复方向（立即执行，宁多勿漏）：服务端活跃但本地缺轮询/缺标志位
      // 幂等约束：本地已在轮询同一任务时不介入。服务端把 pending/cancel_requested
      // 也算活跃，而 hasActiveTask 只认 'running'，旧判定（!hasActiveTask）会把
      // 排队中/停止收尾中的活任务误判为「缺轮询」，每 2.5s 触发一次「清去重 +
      // 偏移归零」的全量事件重放，导致进度块周期性整份重复追加（页面卡死根因）。
      const alreadyTrackingMain =
        !!status.main_task_id &&
        taskStore.currentTaskId === status.main_task_id &&
        taskStore.isPolling;
      if (status.is_main_running && status.main_task_id && !alreadyTrackingMain) {
        debugNotifyLog('[DEBUG_NOTIFY][ui] reconcile:resume-main-task', {
          taskId: status.main_task_id,
          conversationId
        });
        if (typeof this.clearProcessedEvents === 'function') {
          this.clearProcessedEvents();
        }
        taskStore.resumeTask(status.main_task_id, {
          status: 'running',
          resetOffset: true,
          eventHandler: (event: any) => this.handleTaskEvent(event)
        });
        this.taskInProgress = true;
      } else if (status.is_main_running && alreadyTrackingMain) {
        // 已在跟踪：仅幂等对齐标志位，绝不触碰去重集合与事件偏移
        this.taskInProgress = true;
      }
      if (status.has_running_sub_agents) {
        this.waitingForSubAgent = true;
        this.taskInProgress = true;
      }
      if (status.has_running_background_commands) {
        this.waitingForBackgroundCommand = true;
        this.taskInProgress = true;
      }
      if (status.has_running_multi_agent) {
        // 多智能体活跃不阻塞输入区（不设置 waitingForSubAgent）
        this.taskInProgress = true;
      }
      return;
    }

    // 清理方向（保守，需连续确认）：服务端确认空闲
    const localUiActive =
      this.streamingMessage ||
      this.taskInProgress ||
      this.waitingForSubAgent ||
      this.waitingForBackgroundCommand;
    if (!localUiActive && !taskStore.hasActiveTask) {
      this._runningStateIdleStreak = 0;
      return; // 双端一致空闲
    }
    if (taskStore.isPolling) {
      // 事件轮询仍活跃：终态事件会正常收尾，对账不干预（避免截断尾部事件）
      return;
    }
    this._runningStateIdleStreak = (this._runningStateIdleStreak || 0) + 1;
    if (this._runningStateIdleStreak < 2) {
      return;
    }
    this._runningStateIdleStreak = 0;
    debugLog('[TaskPolling] 对账清理：服务端确认对话已空闲，清除本地卡住的运行状态', {
      conversationId,
      streamingMessage: this.streamingMessage,
      taskInProgress: this.taskInProgress,
      waitingForSubAgent: this.waitingForSubAgent,
      waitingForBackgroundCommand: this.waitingForBackgroundCommand,
      taskStatus: taskStore.taskStatus
    });
    this.streamingMessage = false;
    this.taskInProgress = false;
    this.stopRequested = false;
    this.waitingForSubAgent = false;
    this.waitingForBackgroundCommand = false;
    if (typeof this.clearPendingTools === 'function') {
      this.clearPendingTools('reconcile_auto_clear');
    }
    this.clearTaskState();
    this.$forceUpdate();
  },
  // 兼容旧调用点：原"等待子智能体探测"与"多智能体任务探测"已收敛为单一对账循环，
  // 对账循环按会话常驻，不再随单个探测启动/停止。
  stopWaitingTaskProbe() {
    // no-op：保留方法签名避免改动所有调用点
  },
  startWaitingTaskProbe() {
    this.startRunningStateReconcile();
  },

  // ---------- 多智能体任务探测（已收敛为对账循环的兼容入口） ----------
  stopMultiAgentTaskProbe() {
    // no-op：对账循环常驻，保留方法签名避免改动所有调用点
  },
  startMultiAgentTaskProbe() {
    this.startRunningStateReconcile();
  },
  async restoreSubAgentWaitingState(retry = 0) {
    // 初始化恢复与周期对账共用同一接口：启动对账循环即会立即对账一次，
    // 服务端活跃则恢复等待态/事件轮询，服务端空闲则不设置任何运行标志。
    if (!this.currentConversationId) {
      if (retry < 5) {
        setTimeout(() => {
          this.restoreSubAgentWaitingState(retry + 1);
        }, 300);
      }
      return;
    }
    this.startRunningStateReconcile();
  },
  clearProcessedEvents() {
    if (this._processedEventIndices) {
      this._processedEventIndices.clear();
    }
  }
};
