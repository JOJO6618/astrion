// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';

export const dragMethods = {
  handleDragEnter(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    // 检查是否包含文件
    if (event.dataTransfer?.types?.includes('Files')) {
      this.dragOverActive = true;
    }
  },
  handleDragOver(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
  },
  handleDragLeave(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    // 只有当真正离开窗口时才关闭
    const rect = document.body.getBoundingClientRect();
    const x = event.clientX;
    const y = event.clientY;
    if (x <= rect.left || x >= rect.right || y <= rect.top || y >= rect.bottom) {
      this.dragOverActive = false;
    }
  },
  handleDrop(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.dragOverActive = false;

    if (!this.isConnected) {
      this.uiPushToast({
        title: '未连接',
        message: '请等待服务器连接后再上传',
        type: 'warning'
      });
      return;
    }

    const policyStore = usePolicyStore();
    if (policyStore.uiBlocks?.block_upload) {
      this.uiPushToast({
        title: '上传被禁用',
        message: '已被管理员禁用上传功能',
        type: 'warning'
      });
      return;
    }

    const files = event.dataTransfer?.files;
    if (!files || files.length === 0) {
      return;
    }

    // 处理拖拽的文件
    this.processDroppedFiles(Array.from(files));
  },
  async processDroppedFiles(files: File[]) {
    if (!files.length) return;

    // 分离图片和非图片文件
    const imageFiles = files.filter(file => this.isImageFile(file));
    const videoFiles = files.filter(file => this.isVideoFile(file));
    const otherFiles = files.filter(file => !this.isImageFile(file) && !this.isVideoFile(file));

    // 优先处理图片，其次视频
    if (imageFiles.length > 0) {
      await this.handleDroppedImages(imageFiles);
    } else if (videoFiles.length > 0) {
      await this.handleDroppedVideo(videoFiles[0]); // 视频只取第一个
    }

    // 处理其他文件（上传后附加到输入栏，随消息一同发送）
    if (otherFiles.length > 0) {
      await this.handleLocalGenericFiles(otherFiles);
    }
  },
  async handleDroppedImages(files: File[]) {
    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (!currentModel?.supportsImage) {
      this.uiPushToast({
        title: '当前模型不支持图片',
        message: '请选择支持图片输入的模型后再发送图片',
        type: 'error'
      });
      return;
    }

    // 只上传并添加到输入栏，不自动发送
    await this.handleLocalImageFiles(files);
  },
  async handleDroppedVideo(file: File) {
    const modelStore = useModelStore();
    const currentModel = modelStore.models.find((m) => m.key === this.currentModelKey);
    if (!currentModel?.supportsVideo) {
      this.uiPushToast({
        title: '当前模型不支持视频',
        message: '请切换到支持视频输入的模型后再发送视频',
        type: 'error'
      });
      return;
    }

    // 只上传并添加到输入栏，不自动发送
    await this.handleLocalVideoFiles([file]);
  },
  _boundDragEnter: null as ((e: DragEvent) => void) | null,
  _boundDragOver: null as ((e: DragEvent) => void) | null,
  _boundDragLeave: null as ((e: DragEvent) => void) | null,
  _boundDrop: null as ((e: DragEvent) => void) | null,
  setupDragAndDrop() {
    // 存储绑定后的函数以便后续移除
    this._boundDragEnter = this.handleDragEnter.bind(this);
    this._boundDragOver = this.handleDragOver.bind(this);
    this._boundDragLeave = this.handleDragLeave.bind(this);
    this._boundDrop = this.handleDrop.bind(this);

    // 使用 document.body 作为拖拽监听目标
    document.body.addEventListener('dragenter', this._boundDragEnter);
    document.body.addEventListener('dragover', this._boundDragOver);
    document.body.addEventListener('dragleave', this._boundDragLeave);
    document.body.addEventListener('drop', this._boundDrop);
  },
  teardownDragAndDrop() {
    if (this._boundDragEnter) {
      document.body.removeEventListener('dragenter', this._boundDragEnter);
    }
    if (this._boundDragOver) {
      document.body.removeEventListener('dragover', this._boundDragOver);
    }
    if (this._boundDragLeave) {
      document.body.removeEventListener('dragleave', this._boundDragLeave);
    }
    if (this._boundDrop) {
      document.body.removeEventListener('drop', this._boundDrop);
    }
    this._boundDragEnter = null;
    this._boundDragOver = null;
    this._boundDragLeave = null;
    this._boundDrop = null;
  }
};
