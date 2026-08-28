import { defineStore } from 'pinia';
import { t } from '@/locales';
import { useInputStore } from './input';

interface ToastPayload {
  title?: string;
  message?: string;
  type?: string;
}

interface ChatActionDependencies {
  pushToast: (payload: ToastPayload) => void;
  autoResizeInput: () => void;
  focusComposer: () => void;
  isConnected: () => boolean;
  executeCommand: (command: string) => Promise<{ success?: boolean; message?: string } | void>;
  downloadResource: (url: string, filename: string) => Promise<void>;
}

const defaultDependencies: ChatActionDependencies = {
  pushToast: () => {},
  autoResizeInput: () => {},
  focusComposer: () => {},
  isConnected: () => false,
  executeCommand: async () => ({ success: false, message: t('stores.commandChannelUnavailable') }),
  downloadResource: async () => {}
};

function escapeAttributeSelector(value: string) {
  if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
    return CSS.escape(value);
  }
  return value.replace(/["\\]/g, '\\$&');
}

function decodeHtmlEntities(input: string) {
  const textarea = document.createElement('textarea');
  textarea.innerHTML = input;
  return textarea.value;
}

export const useChatActionStore = defineStore('chatActions', {
  state: () => ({
    dependencies: { ...defaultDependencies }
  }),
  actions: {
    registerDependencies(partial: Partial<ChatActionDependencies>) {
      this.dependencies = {
        ...this.dependencies,
        ...partial
      };
    },
    async copyActionContent(action: any, blockId?: string | number) {
      const content = (action && (action.content || action.text || ''))?.toString();
      if (!content) {
        this.dependencies.pushToast({
          title: t('common.copyFailed'),
          message: t('stores.copyContentNotFound'),
          type: 'warning'
        });
        return false;
      }
      try {
        await this.copyText(content);
        this.dependencies.pushToast({
          title: t('common.copied'),
          message: blockId ? t('stores.blockCopied', { id: blockId }) : t('stores.contentCopied'),
          type: 'success'
        });
        return true;
      } catch (error) {
        console.warn('复制失败:', error);
        this.dependencies.pushToast({
          title: t('common.copyFailed'),
          message: t('stores.copyBlockedByBrowser'),
          type: 'error'
        });
        return false;
      }
    },
    async copyCodeBlock(blockId: string) {
      if (!blockId) {
        return false;
      }
      const selector = `[data-code-id="${escapeAttributeSelector(blockId)}"]`;
      const codeEl = document.querySelector(selector) as HTMLElement | null;
      if (!codeEl) {
        this.dependencies.pushToast({
          title: t('common.copyFailed'),
          message: t('stores.codeBlockNotFound'),
          type: 'warning'
        });
        return false;
      }
      const encoded = codeEl.getAttribute('data-original-code');
      const content = encoded ? decodeHtmlEntities(encoded) : codeEl.textContent || '';
      if (!content.trim()) {
        this.dependencies.pushToast({
          title: t('common.copyFailed'),
          message: t('stores.codeContentEmpty'),
          type: 'warning'
        });
        return false;
      }
      try {
        await this.copyText(content);
        this.dependencies.pushToast({
          title: t('common.copied'),
          message: t('stores.codeCopied'),
          type: 'success'
        });
        return true;
      } catch (error) {
        console.warn('复制代码失败:', error);
        this.dependencies.pushToast({
          title: t('common.copyFailed'),
          message: t('stores.copyBlockedManual'),
          type: 'error'
        });
        return false;
      }
    },
    applyActionContent(action: any) {
      if (!action || !action.content) {
        this.dependencies.pushToast({
          title: t('stores.applyFailed'),
          message: t('stores.applyContentMissing'),
          type: 'warning'
        });
        return false;
      }
      const inputStore = useInputStore();
      inputStore.setInputMessage(action.content);
      this.dependencies.autoResizeInput();
      this.dependencies.focusComposer();
      this.dependencies.pushToast({
        title: t('stores.filled'),
        message: t('stores.checkContentBeforeSend'),
        type: 'info'
      });
      return true;
    },
    async runCommand(action: any) {
      const command = (action && (action.command || action.content))?.toString();
      if (!command) {
        this.dependencies.pushToast({
          title: t('stores.cannotExecute'),
          message: t('stores.commandContentMissing'),
          type: 'warning'
        });
        return false;
      }
      if (!this.dependencies.isConnected()) {
        this.dependencies.pushToast({
          title: t('stores.connectionLost'),
          message: t('stores.reconnectAndRetry'),
          type: 'error'
        });
        return false;
      }
      try {
        const result = await this.dependencies.executeCommand(command);
        if (result && result.success === false) {
          this.dependencies.pushToast({
            title: t('stores.commandFailed'),
            message: result.message || t('common.retryLater'),
            type: 'error'
          });
          return false;
        }
        this.dependencies.pushToast({
          title: t('stores.commandExecuted'),
          message: command,
          type: 'success'
        });
        return true;
      } catch (error) {
        this.dependencies.pushToast({
          title: t('stores.commandFailed'),
          message: (error as Error)?.message || t('common.retryLater'),
          type: 'error'
        });
        return false;
      }
    },
    async downloadActionAttachment(action: any) {
      if (!action || !action.path) {
        this.dependencies.pushToast({
          title: t('common.downloadFailed'),
          message: t('stores.downloadTargetMissing'),
          type: 'warning'
        });
        return false;
      }
      return this.downloadFile(action.path);
    },
    async downloadFile(path: string) {
      if (!path) {
        this.dependencies.pushToast({
          title: t('common.downloadFailed'),
          message: t('stores.downloadPathInvalid'),
          type: 'warning'
        });
        return false;
      }
      const url = `/api/download/file?path=${encodeURIComponent(path)}`;
      const name = path.split('/').pop() || 'file';
      try {
        await this.dependencies.downloadResource(url, name);
        return true;
      } catch (error) {
        console.warn('下载失败:', error);
        if (error && (error as Error).message) {
          this.dependencies.pushToast({
            title: t('common.downloadFailed'),
            message: (error as Error).message,
            type: 'error'
          });
        }
        return false;
      }
    },
    async copyText(content: string) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(content);
        return;
      }
      const textarea = document.createElement('textarea');
      textarea.value = content;
      textarea.style.position = 'fixed';
      textarea.style.top = '-1000px';
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
  }
});
