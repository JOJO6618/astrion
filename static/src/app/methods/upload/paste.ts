// @ts-nocheck
import { t } from '@/locales';
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
        title: t('appUi.notConnected'),
        message: t('appUi.waitForConnectionBeforeUpload'),
        type: 'warning'
      });
      return;
    }

    const policyStore = usePolicyStore();
    if (policyStore.uiBlocks?.block_upload) {
      this.uiPushToast({
        title: t('appUi.uploadDisabled'),
        message: t('appUi.uploadDisabledByAdmin'),
        type: 'warning'
      });
      return;
    }

    this.processDroppedFiles(list);
  }
};
