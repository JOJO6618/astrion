// @ts-nocheck
import { useWorkflowStore } from '../../../stores/workflow';

/**
 * 工作流运行时事件处理（任务轮询链路）。
 *
 * - workflow_progress：进度快照（推进/审核结果/激活/退出都会广播），live=true 播窗口动画
 * - workflow_review_progress：审核智能体实时进度，复用右侧自动审批面板（对齐 goal_review_progress）
 */
export const workflowMethods = {
  handleWorkflowProgress(data: any) {
    // 对话隔离：忽略不属于当前对话的工作流事件
    if (data?.conversation_id && this.currentConversationId && data.conversation_id !== this.currentConversationId) {
      return;
    }
    useWorkflowStore().setWorkflow(data, true);
  },

  handleWorkflowReviewProgress(data: any) {
    if (data?.conversation_id && this.currentConversationId && data.conversation_id !== this.currentConversationId) {
      return;
    }
    const progress = data?.progress || data || {};
    if (!progress || typeof progress !== 'object') {
      return;
    }
    if (!Array.isArray(this.autoApprovalFeedLines)) {
      this.autoApprovalFeedLines = [];
    }
    this.autoApprovalTitle = '工作流审核';
    if (progress.stage === 'start') {
      this.autoApprovalFeedLines = ['开始审核'];
      this.autoApprovalFinalMessage = '';
    } else if (progress.stage === 'model_call') {
      this.autoApprovalFeedLines.push(String(progress.message || `审核轮次 ${progress.round || ''}`).trim());
    } else if (progress.stage === 'run_command' && progress.command) {
      this.autoApprovalFeedLines.push(String(progress.command));
    } else if (progress.message) {
      this.autoApprovalFeedLines.push(String(progress.message));
    }
    this.autoApprovalFeedLines = this.autoApprovalFeedLines.slice(-20);
    this.$forceUpdate();
  }
};
