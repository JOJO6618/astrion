// @ts-nocheck
import './shared';

export const confirmMethods = {
  handleRemoveImage(path) {
    this.inputRemoveSelectedImage(path);
  },
  handleRemoveVideo(path) {
    this.inputRemoveSelectedVideo(path);
  },
  handleRemoveFile(path) {
    this.inputRemoveSelectedFile(path);
  }
};
