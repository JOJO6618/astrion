// @ts-nocheck
import { usePolicyStore } from '../../../stores/policy';
import { useModelStore } from '../../../stores/model';
import {

} from './shared';

export const confirmMethods = {
  handleImagesConfirmed(list) {
    this.inputSetSelectedImages(Array.isArray(list) ? list : []);
    this.inputSetImagePickerOpen(false);
  },
  handleRemoveImage(path) {
    this.inputRemoveSelectedImage(path);
  },
  handleVideosConfirmed(list) {
    const arr = Array.isArray(list) ? list.slice(0, 1) : [];
    this.inputSetSelectedVideos(arr);
    this.inputSetVideoPickerOpen(false);
    if (arr.length) {
      this.inputClearSelectedImages();
    }
  },
  handleRemoveVideo(path) {
    this.inputRemoveSelectedVideo(path);
  },
  handleRemoveFile(path) {
    this.inputRemoveSelectedFile(path);
  }
};
