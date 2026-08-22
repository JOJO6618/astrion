import { defineStore } from 'pinia';
import { useUiStore } from './ui';
import { useFileStore } from './file';
import { usePolicyStore } from './policy';

function deriveFilenameFromType(mimeType: string | undefined): string | null {
  if (!mimeType) return null;
  const map: Record<string, string> = {
    'image/jpeg': 'image.jpg',
    'image/jpg': 'image.jpg',
    'image/png': 'image.png',
    'image/gif': 'image.gif',
    'image/webp': 'image.webp',
    'image/bmp': 'image.bmp',
    'image/svg+xml': 'image.svg',
    'video/mp4': 'video.mp4',
    'video/quicktime': 'video.mov',
    'video/x-matroska': 'video.mkv',
    'video/avi': 'video.avi',
    'video/webm': 'video.webm',
    'application/pdf': 'document.pdf',
    'text/plain': 'document.txt',
    'text/markdown': 'document.md',
  };
  // 兼容类似 image/jpeg; charset=utf-8 的情况
  const normalized = mimeType.split(';')[0].trim().toLowerCase();
  return map[normalized] || null;
}

interface UploadResult {
  path: string;
  filename: string;
}

interface UploadState {
  uploading: boolean;
  mediaUploading: boolean;
}

export const useUploadStore = defineStore('upload', {
  state: (): UploadState => ({
    uploading: false,
    mediaUploading: false
  }),
  actions: {
    setUploading(value: boolean) {
      this.uploading = value;
    },
    setMediaUploading(value: boolean) {
      this.mediaUploading = value;
    },
    async handleSelectedFiles(files: FileList | File[] | null) {
      if (!files) {
        return;
      }
      const list = Array.isArray(files) ? files : Array.from(files);
      const [file] = list;
      if (!file) {
        return;
      }
      await this.uploadFile(file);
    },
    async uploadFiles(
      files: FileList | File[] | null,
      options: { markUploading?: boolean; markMediaUploading?: boolean } = {}
    ): Promise<UploadResult[]> {
      if (!files) {
        return [];
      }
      const list = Array.isArray(files) ? files : Array.from(files);
      if (!list.length) {
        return [];
      }
      const { markUploading = true, markMediaUploading = false } = options;
      const policyStore = usePolicyStore();
      if (policyStore.uiBlocks?.block_upload) {
        const uiStore = useUiStore();
        uiStore.pushToast({
          title: '上传被禁用',
          message: '已被管理员禁用上传功能',
          type: 'warning'
        });
        return [];
      }
      const fileStore = useFileStore();
      const results: UploadResult[] = [];
      if (markUploading) {
        this.setUploading(true);
      }
      if (markMediaUploading) {
        this.setMediaUploading(true);
      }
      try {
        for (const file of list) {
          const uploaded = await this.uploadFile(file, {
            manageUploading: false,
            refreshFileTree: false,
            skipPolicyCheck: true
          });
          if (uploaded?.path) {
            results.push(uploaded);
          }
        }
        if (results.length) {
          await fileStore.fetchFileTree();
        }
      } finally {
        if (markMediaUploading) {
          this.setMediaUploading(false);
        }
        if (markUploading) {
          this.setUploading(false);
        }
      }
      return results;
    },
    async uploadFile(
      file: File,
      options: {
        manageUploading?: boolean;
        refreshFileTree?: boolean;
        skipPolicyCheck?: boolean;
      } = {}
    ): Promise<UploadResult | null> {
      const { manageUploading = true, refreshFileTree = true, skipPolicyCheck = false } = options;
      if (!file || (manageUploading && this.uploading)) {
        return null;
      }
      const policyStore = usePolicyStore();
      if (!skipPolicyCheck && policyStore.uiBlocks?.block_upload) {
        const uiStore = useUiStore();
        uiStore.pushToast({
          title: '上传被禁用',
          message: '已被管理员禁用上传功能',
          type: 'warning'
        });
        return null;
      }
      const uiStore = useUiStore();
      const fileStore = useFileStore();
      const isVideoFile =
        (file.type && file.type.startsWith('video/')) ||
        /\.(mp4|mov|m4v|webm|avi|mkv|flv|mpg|mpeg)$/i.test(file.name || '');
      if (isVideoFile) {
        uiStore.pushToast({
          title: '视频处理中',
          message: '读取视频需要较长时间，请耐心等待',
          type: 'info',
          duration: 5000
        });
      }
      if (manageUploading) {
        this.setUploading(true);
      }
      const toastId = uiStore.pushToast({
        title: '上传文件',
        message: `正在上传 ${file.name}...`,
        type: 'info',
        duration: null
      });
      try {
        const safeName = file.name || deriveFilenameFromType(file.type) || `upload_${Date.now()}`;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('filename', safeName);
        const response = await fetch('/api/upload', {
          method: 'POST',
          body: formData
        });
        if (toastId) {
          uiStore.updateToast(toastId, {
            message: '文件已上传，正在执行安全扫描...',
            type: 'info'
          });
        }
        let result: any = {};
        try {
          result = await response.json();
        } catch (error) {
          throw new Error('服务器响应无法解析');
        }
        if (!response.ok || !result.success) {
          const message = result.error || result.message || '上传失败';
          throw new Error(message);
        }
        if (refreshFileTree) {
          await fileStore.fetchFileTree();
        }
        const successMessage = `上传成功：${result.path || file.name}`;
        if (toastId) {
          uiStore.updateToast(toastId, {
            title: '上传完成',
            message: successMessage,
            type: 'success',
            duration: 4500
          });
        } else {
          uiStore.pushToast({
            title: '上传完成',
            message: successMessage,
            type: 'success'
          });
        }
        if (result?.path) {
          return {
            path: result.path,
            filename: result.filename || file.name || result.path
          };
        }
        return null;
      } catch (error: any) {
        const originalMessage = error && error.message ? String(error.message) : '';
        const fileLabel = file && file.name ? file.name : '文件';
        let displayMessage = originalMessage || '请稍后重试';
        if (/安全审核未通过/.test(originalMessage) || /Eicar/i.test(originalMessage)) {
          displayMessage = `${fileLabel} 安全审核未通过`;
        } else if (/文件类型不在允许列表中/.test(originalMessage)) {
          displayMessage = `${fileLabel} 文件类型不在允许列表中`;
        } else if (displayMessage) {
          displayMessage = `${fileLabel} 上传失败：${displayMessage}`;
        } else {
          displayMessage = `${fileLabel} 上传失败，请稍后重试`;
        }
        const uiStore = useUiStore();
        if (toastId) {
          uiStore.updateToast(toastId, {
            title: '上传失败',
            message: displayMessage,
            type: 'error',
            duration: 5000
          });
        } else {
          uiStore.pushToast({
            title: '上传失败',
            message: displayMessage,
            type: 'error'
          });
        }
        return null;
      } finally {
        if (manageUploading) {
          this.setUploading(false);
        }
      }
    }
  }
});
