// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';

export const quickMethods = {
  handleQuickUpload() {
    if (this.uploading || !this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_upload', '上传功能已被管理员禁用')) {
      return;
    }
    this.triggerFileUpload();
  }
};
