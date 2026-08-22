// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';

export const pasteMethods = {
  /**
   * 处理输入框粘贴事件中携带的文件（截图/复制的图片等）。
   * 复用拖拽上传的路由逻辑：图片走模型能力校验后批量上传并挂到输入栏，
   * 视频取第一个上传，其余文件走普通文件上传。
   */
  handlePastedFiles(files: File[]) {
    const list = Array.isArray(files) ? files.filter(Boolean) : [];
    if (!list.length) {
      return;
    }

    if (!this.isConnected) {
      this.uiPushToast({
        title: '未连接',
        message: '请等待服务器连接后再上传',
        type: 'warning'
      });
      return;
    }

    const policyStore = usePolicyStore();
    if (policyStore.uiBlocks?.block_upload) {
      this.uiPushToast({
        title: '上传被禁用',
        message: '已被管理员禁用上传功能',
        type: 'warning'
      });
      return;
    }

    this.processDroppedFiles(list);
  }
};
