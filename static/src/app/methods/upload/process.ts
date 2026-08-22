// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';

export const processMethods = {
  normalizeLocalFiles(files) {
    if (!files) return [];
    const list = Array.isArray(files) ? files : Array.from(files);
    return list.filter(Boolean);
  },
  isImageFile(file) {
    const name = file?.name || '';
    const type = (file?.type || '').toLowerCase();
    // SVG 不走图片链路（模型图片输入与 view 工具均不支持 svg），统一按普通文件处理
    if (type === 'image/svg+xml' || /\.svg$/i.test(name)) return false;
    return type.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp)$/i.test(name);
  },
  isVideoFile(file) {
    const name = file?.name || '';
    const type = file?.type || '';
    return type.startsWith('video/') || /\.(mp4|mov|m4v|webm|avi|mkv|flv|mpg|mpeg)$/i.test(name);
  },
  async handleLocalImageFiles(files) {
    if (!this.isConnected) {
      return;
    }
    if (this.mediaUploading) {
      this.uiPushToast({
        title: '上传中',
        message: '请等待当前图片上传完成',
        type: 'info'
      });
      return;
    }
    const list = this.normalizeLocalFiles(files);
    if (!list.length) {
      this.uiPushToast({
        title: '未获取到文件',
        message: '系统未返回有效的文件内容，请重试',
        type: 'warning'
      });
      return;
    }
    const existingCount = Array.isArray(this.selectedImages) ? this.selectedImages.length : 0;
    const remaining = Math.max(0, 9 - existingCount);
    if (!remaining) {
      this.uiPushToast({
        title: '已达上限',
        message: '最多只能选择 9 张图片',
        type: 'warning'
      });
      return;
    }
    const valid = list.filter((file) => this.isImageFile(file));
    // 兼容 Android WebView 部分机型：返回的 File 可能缺失 type/扩展名，导致识别失败
    // 若来自图片选择器但未识别出有效类型，回退为“按选择结果直接上传”。
    const candidates = valid.length ? valid : list;
    if (valid.length < list.length && valid.length > 0) {
      this.uiPushToast({
        title: '已忽略',
        message: '已跳过非图片文件',
        type: 'info'
      });
    }
    const limited = candidates.slice(0, remaining);
    if (candidates.length > remaining) {
      this.uiPushToast({
        title: '已超出数量',
        message: `最多还能添加 ${remaining} 张图片，已自动截断`,
        type: 'warning'
      });
    }
    const uploaded = await this.uploadBatchFiles(limited, {
      markUploading: true,
      markMediaUploading: true
    });
    if (!uploaded.length) {
      return;
    }
    uploaded.forEach((item) => {
      if (!item?.path) return;
      this.inputAddSelectedImage(item.path);
      this.upsertImageEntry(item.path, item.filename);
    });
    // 上传完成后自动关闭选择窗口
    this.closeImagePicker();
  },
  // 普通文件上传后附加到输入栏（与图片/视频并列，随消息一同发送）
  async handleLocalGenericFiles(files) {
    if (!this.isConnected) {
      return;
    }
    if (this.uploading) {
      this.uiPushToast({
        title: '上传中',
        message: '请等待当前文件上传完成',
        type: 'info'
      });
      return;
    }
    const list = this.normalizeLocalFiles(files);
    if (!list.length) {
      this.uiPushToast({
        title: '未获取到文件',
        message: '系统未返回有效的文件内容，请重试',
        type: 'warning'
      });
      return;
    }
    const existingCount = Array.isArray(this.selectedFiles) ? this.selectedFiles.length : 0;
    const remaining = Math.max(0, 9 - existingCount);
    if (!remaining) {
      this.uiPushToast({
        title: '已达上限',
        message: '最多只能附加 9 个文件',
        type: 'warning'
      });
      return;
    }
    const limited = list.slice(0, remaining);
    if (list.length > remaining) {
      this.uiPushToast({
        title: '已超出数量',
        message: `最多还能附加 ${remaining} 个文件，已自动截断`,
        type: 'warning'
      });
    }
    const uploaded = await this.uploadBatchFiles(limited, {
      markUploading: true,
      markMediaUploading: false
    });
    if (!uploaded.length) {
      return;
    }
    uploaded.forEach((item) => {
      if (!item?.path) return;
      this.inputAddSelectedFile(item.path);
    });
  },
  async handleLocalVideoFiles(files) {
    if (!this.isConnected) {
      return;
    }
    if (this.mediaUploading) {
      this.uiPushToast({
        title: '上传中',
        message: '请等待当前视频上传完成',
        type: 'info'
      });
      return;
    }
    const list = this.normalizeLocalFiles(files);
    if (!list.length) {
      this.uiPushToast({
        title: '未获取到文件',
        message: '系统未返回有效的文件内容，请重试',
        type: 'warning'
      });
      return;
    }
    const valid = list.filter((file) => this.isVideoFile(file));
    // 兼容 Android WebView：文件元数据缺失时回退按选择结果直接上传
    const candidates = valid.length ? valid : list;
    if (valid.length < list.length && valid.length > 0) {
      this.uiPushToast({
        title: '已忽略',
        message: '已跳过非视频文件',
        type: 'info'
      });
    }
    if (candidates.length > 1) {
      this.uiPushToast({
        title: '视频数量过多',
        message: '一次只能选择 1 个视频，已使用第一个',
        type: 'warning'
      });
    }
    const [file] = candidates;
    if (!file) {
      return;
    }
    const uploaded = await this.uploadBatchFiles([file], {
      markUploading: true,
      markMediaUploading: true
    });
    const [item] = uploaded;
    if (!item?.path) {
      return;
    }
    this.inputSetSelectedVideos([item.path]);
    this.inputClearSelectedImages();
    this.upsertVideoEntry(item.path, item.filename);
  }
};
