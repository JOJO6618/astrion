// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';
import { t } from '@/locales';

export const quickMethods = {
  handleQuickUpload() {
    if (this.uploading || !this.isConnected) {
      return;
    }
    if (this.isPolicyBlocked('block_upload', t('appUi.uploadDisabledByAdmin'))) {
      return;
    }
    this.triggerFileUpload();
  }
};
