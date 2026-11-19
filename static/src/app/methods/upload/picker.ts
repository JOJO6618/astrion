// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import './shared';
import { t } from '@/locales';

const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,image/bmp';

/**
 * 打开系统文件选择。input 挂在 body 上（Android WebView 的 onShowFileChooser 需要在文档内的 input），
 * 选完或下次打开前移除；必须在用户点击的同步调用栈里执行，不能放在 await 之后。
 */
let pendingInput: HTMLInputElement | null = null;
function openSystemFilePicker(accept: string, multiple: boolean, onFiles: (files: File[]) => void) {
  pendingInput?.remove();
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = accept;
  input.multiple = multiple;
  input.hidden = true;
  input.addEventListener('change', () => {
    const files = Array.from(input.files || []);
    input.remove();
    if (pendingInput === input) pendingInput = null;
    if (files.length) onFiles(files);
  });
  document.body.appendChild(input);
  pendingInput = input;
  input.click();
}

export const pickerMethods = {
  triggerFileUpload() {
    if (this.uploading) {
      return;
    }
    const input = this.getComposerElement('fileUploadInput');
    if (input) {
      input.click();
    }
  },
  handleFileSelected(files) {
    const policyStore = usePolicyStore();
    if (policyStore.uiBlocks?.block_upload) {
      this.uiPushToast({
        title: t('appUi.uploadDisabled'),
        message: t('appUi.uploadDisabledByAdmin'),
        type: 'warning'
      });
      return;
    }
    // 快速上传与拖拽/粘贴统一走三路分发：图片→图片附加，视频→视频附加，其余→文件附加
    const list = Array.isArray(files)
      ? files.filter(Boolean)
      : Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    this.processDroppedFiles(list);
  },
  /** 发送图片：校验模型后直接打开系统文件选择（不再有工作区图片选择窗口） */
  pickLocalImages() {
    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (
      !this.streamingUi &&
      !this.compressionActiveForCurrentConversation &&
      !currentModel?.supportsImage
    ) {
      this.uiPushToast({
        title: t('appUi.modelDoesNotSupportImage'),
        message: t('appUi.chooseImageModelMessage'),
        type: 'error'
      });
      return;
    }
    this.closeQuickMenu();
    openSystemFilePicker(IMAGE_ACCEPT, true, (files) => this.handleLocalImageFiles(files));
  },
  /** 发送视频：同上，单选 */
  pickLocalVideo() {
    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (!currentModel?.supportsVideo) {
      this.uiPushToast({
        title: t('appUi.modelDoesNotSupportVideo'),
        message: t('appUi.switchToVideoModelMessage'),
        type: 'error'
      });
      return;
    }
    this.closeQuickMenu();
    openSystemFilePicker('video/*', false, (files) => this.handleLocalVideoFiles(files));
  }
};
