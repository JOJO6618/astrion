import { defineStore } from 'pinia';
import { t } from '@/locales';

interface FocusedFile {
  size: number;
  content: string;
}

interface FocusState {
  focusedFiles: Record<string, FocusedFile>;
}

export const useFocusStore = defineStore('focus', {
  state: (): FocusState => ({
    focusedFiles: {}
  }),
  actions: {
    async fetchFocusedFiles() {
      try {
        const response = await fetch('/api/focused');
        if (!response.ok) {
          throw new Error(response.statusText || t('stores.requestFailed'));
        }
        const data = await response.json();
        this.focusedFiles = data || {};
      } catch (error) {
        console.error('获取聚焦文件失败:', error);
      }
    },
    setFocusedFiles(payload: Record<string, FocusedFile> | null) {
      this.focusedFiles = payload || {};
    },
    clearFocusedFiles() {
      this.focusedFiles = {};
    }
  }
});
