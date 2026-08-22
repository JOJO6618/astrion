import { defineStore } from 'pinia';

/**
 * 工作流（Workflow）运行状态 store（对话级）。
 *
 * 承载当前对话激活工作流的进度快照，供快捷窗口 WorkflowWindow 渲染。
 * 数据源（后续运行时实施接入）：
 * - 轮询事件 workflow_progress / workflow_review_progress / workflow_completed / ...（live=true，播动画）
 * - GET /api/workflow/status 刷新恢复 / bootstrap 回填（live=false，静态呈现）
 *
 * 列表语义（与 demo/workflow_dock_window.html 定稿一致）：
 * history（已完成，可向上滚动查看）+ current（进行中）+ next（未来仅一步可见）。
 */

export interface WorkflowStepRecord {
  name: string;
  rounds: number | null;
}

export interface WorkflowFootnote {
  kind: 'info' | 'success' | 'danger';
  text: string;
}

export interface WorkflowSnapshot {
  /** 当前对话是否有活跃工作流（控制窗口出现/消失） */
  active: boolean;
  /** 工作流显示名 */
  name: string;
  status: 'active' | 'completed' | 'stopped' | 'failed' | null;
  /** 已完成步骤（含「刚完成」，按完成顺序） */
  history: WorkflowStepRecord[];
  /** 当前进行中的阶段；完成/未激活时为 null */
  current: WorkflowStepRecord | null;
  /** 下一阶段名（分支未选择时为 null）；当前已是最后阶段时为「结束」；完成态为 null */
  next: string | null;
  /** 当前阶段是否处于审核中（review 瞬态节点的副状态） */
  reviewing: boolean;
  /** 瞬时脚注提示（审核中/驳回/完成等） */
  footnote: WorkflowFootnote | null;
}

interface WorkflowState {
  snapshot: WorkflowSnapshot;
  /** true = 来自任务期实时事件（播动画）；false = 来自加载/恢复（静态呈现） */
  live: boolean;
  /**
   * 退出动画进行中：收到 live 的 active=false（deactivate 广播 / slash 退出）时
   * 不立即清空，保留快照供窗口播退出动画，播完由窗口调 finishExit 真正清空。
   * QuickDock 的 hasContent 读取此标志，动画期间保持容器展开（否则容器 300ms
   * 收起会吞掉窗口自身的 240ms 退出动画）。
   */
  exiting: boolean;
}

const emptySnapshot = (): WorkflowSnapshot => ({
  active: false,
  name: '',
  status: null,
  history: [],
  current: null,
  next: null,
  reviewing: false,
  footnote: null
});

export const useWorkflowStore = defineStore('workflow', {
  state: (): WorkflowState => ({
    snapshot: emptySnapshot(),
    live: false,
    exiting: false
  }),
  getters: {
    isActive(state): boolean {
      return state.snapshot.active;
    }
  },
  actions: {
    /** 写入新快照。live=true 触发窗口动画；false 静态呈现（加载/刷新恢复）。 */
    setWorkflow(snapshot: Partial<WorkflowSnapshot> | null | undefined, live = false) {
      this.live = live;
      if (!snapshot || snapshot.active !== true) {
        // 退出动画进行中再收到停用事件（如 slash 退出后摘牌广播又到）：忽略，
        // 等动画播完由 finishExit 一次性清空。
        // 例外：静态校正（切换对话/刷新恢复 live=false）优先，直接清空并打断动画
        if (this.exiting) {
          if (!live) {
            this.exiting = false;
            this.snapshot = emptySnapshot();
          }
          return;
        }
        // live 事件驱动的消失：标记 exiting 保留快照，窗口播退出动画；
        // 静态校正（切换对话/刷新恢复 live=false）：瞬间清空不播动画
        if (live && this.snapshot.active) {
          this.exiting = true;
          return;
        }
        this.snapshot = emptySnapshot();
        return;
      }
      // 新活跃快照到达：中止可能进行中的退出流程（快速重新激活场景）
      this.exiting = false;
      this.snapshot = { ...emptySnapshot(), ...snapshot, active: true };
    },
    /** 窗口退出动画播完后由 WorkflowWindow 调用：真正清空状态 */
    finishExit() {
      this.exiting = false;
      this.live = false;
      this.snapshot = emptySnapshot();
    },
    /** 对话切换 / 离开对话时清空 */
    reset() {
      this.snapshot = emptySnapshot();
      this.live = false;
      this.exiting = false;
    },
    /**
     * 对话切换时的状态回填：拉取目标对话的工作流状态覆盖本地（live=false 静态校正）。
     * 目标对话无激活工作流或请求失败时清空。与 QuickDock「保留旧内容直到回填」的
     * 防闪烁机制对齐：切换期间不清空，等本请求回来后一次性校正。
     */
    async fetchStatus(conversationId: string) {
      try {
        const resp = await fetch(`/api/workflow/status?conversation_id=${encodeURIComponent(conversationId)}`, {
          credentials: 'same-origin'
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data?.success && data?.snapshot?.active === true) {
          this.setWorkflow(data.snapshot, false);
        } else {
          this.reset();
        }
      } catch (err) {
        this.reset();
      }
    }
  }
});
