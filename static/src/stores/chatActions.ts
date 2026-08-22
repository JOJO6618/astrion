import { defineStore } from 'pinia';
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
  executeCommand: async () => ({ success: false, message: '命令通道不可用' }),
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
          title: '复制失败',
          message: '未找到可复制的内容',
          type: 'warning'
        });
        return false;
      }
      try {
        await this.copyText(content);
        this.dependencies.pushToast({
          title: '已复制',
          message: blockId ? `片段 ${blockId} 已复制到剪贴板` : '内容已复制到剪贴板',
          type: 'success'
        });
        return true;
      } catch (error) {
        console.warn('复制失败:', error);
        this.dependencies.pushToast({
          title: '复制失败',
          message: '浏览器阻止了复制操作，请手动选择文本后复制。',
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
          title: '复制失败',
          message: '未找到对应的代码块',
          type: 'warning'
        });
        return false;
      }
      const encoded = codeEl.getAttribute('data-original-code');
      const content = encoded ? decodeHtmlEntities(encoded) : codeEl.textContent || '';
      if (!content.trim()) {
        this.dependencies.pushToast({
          title: '复制失败',
          message: '代码内容为空',
          type: 'warning'
        });
        return false;
      }
      try {
        await this.copyText(content);
        this.dependencies.pushToast({
          title: '已复制',
          message: '代码已复制到剪贴板',
          type: 'success'
        });
        return true;
      } catch (error) {
        console.warn('复制代码失败:', error);
        this.dependencies.pushToast({
          title: '复制失败',
          message: '浏览器阻止了复制操作，请手动复制。',
          type: 'error'
        });
        return false;
      }
    },
    applyActionContent(action: any) {
      if (!action || !action.content) {
        this.dependencies.pushToast({
          title: '无法应用',
          message: '缺少可应用的内容',
          type: 'warning'
        });
        return false;
      }
      const inputStore = useInputStore();
      inputStore.setInputMessage(action.content);
      this.dependencies.autoResizeInput();
      this.dependencies.focusComposer();
      this.dependencies.pushToast({
        title: '已填充',
        message: '请检查内容后发送以应用这些修改。',
        type: 'info'
      });
      return true;
    },
    async runCommand(action: any) {
      const command = (action && (action.command || action.content))?.toString();
      if (!command) {
        this.dependencies.pushToast({
          title: '无法执行',
          message: '没有可执行的命令内容',
          type: 'warning'
        });
        return false;
      }
      if (!this.dependencies.isConnected()) {
        this.dependencies.pushToast({
          title: '连接已断开',
          message: '请重新连接后再试。',
          type: 'error'
        });
        return false;
      }
      try {
        const result = await this.dependencies.executeCommand(command);
        if (result && result.success === false) {
          this.dependencies.pushToast({
            title: '命令执行失败',
            message: result.message || '请稍后重试',
            type: 'error'
          });
          return false;
        }
        this.dependencies.pushToast({
          title: '命令已执行',
          message: command,
          type: 'success'
        });
        return true;
      } catch (error) {
        this.dependencies.pushToast({
          title: '命令执行失败',
          message: (error as Error)?.message || '请稍后重试',
          type: 'error'
        });
        return false;
      }
    },
    async downloadActionAttachment(action: any) {
      if (!action || !action.path) {
        this.dependencies.pushToast({
          title: '下载失败',
          message: '没有可下载的目标文件',
          type: 'warning'
        });
        return false;
      }
      return this.downloadFile(action.path);
    },
    async downloadFile(path: string) {
      if (!path) {
        this.dependencies.pushToast({
          title: '下载失败',
          message: '没有提供有效的文件路径',
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
            title: '下载失败',
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
