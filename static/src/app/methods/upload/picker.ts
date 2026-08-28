// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';
import { t } from '@/locales';

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
    const list = Array.isArray(files) ? files.filter(Boolean) : Array.from(files || []).filter(Boolean);
    if (!list.length) return;
    this.processDroppedFiles(list);
  },
  async openImagePicker() {
    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (!currentModel?.supportsImage) {
      this.uiPushToast({
        title: t('appUi.modelDoesNotSupportImage'),
        message: t('appUi.chooseImageModelMessage'),
        type: 'error'
      });
      return;
    }
    this.closeQuickMenu();
    this.inputSetImagePickerOpen(true);
    await this.loadWorkspaceImages();
  },
  closeImagePicker() {
    this.inputSetImagePickerOpen(false);
  },
  async openVideoPicker() {
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
    this.inputSetVideoPickerOpen(true);
    await this.loadWorkspaceVideos();
  },
  closeVideoPicker() {
    this.inputSetVideoPickerOpen(false);
  }
};
