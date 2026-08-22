import { defineStore } from 'pinia';

interface InputState {
  inputMessage: string;
  inputLineCount: number;
  inputIsMultiline: boolean;
  inputIsFocused: boolean;
  quickMenuOpen: boolean;
  toolMenuOpen: boolean;
  settingsOpen: boolean;
  imagePickerOpen: boolean;
  selectedImages: string[];
  videoPickerOpen: boolean;
  selectedVideos: string[];
  // 待发送的附加文件（工作区相对路径，随消息一同发送，上限 9 个）
  selectedFiles: string[];
  // 目标模式（Goal Mode）
  goalModeArmed: boolean; // 已就绪：下一条消息作为目标发送
  goalRunning: boolean; // 运行中：目标循环正在进行
  goalProgress: Record<string, any> | null; // 最新进度快照（轮数/token/工具次数/用时/总结等）
  goalDialogOpen: boolean; // 进度/完成弹窗
}

export const useInputStore = defineStore('input', {
  state: (): InputState => ({
    inputMessage: '',
    inputLineCount: 1,
    inputIsMultiline: false,
    inputIsFocused: false,
    quickMenuOpen: false,
    toolMenuOpen: false,
    settingsOpen: false,
    imagePickerOpen: false,
    selectedImages: [],
    videoPickerOpen: false,
    selectedVideos: [],
    selectedFiles: [],
    goalModeArmed: false,
    goalRunning: false,
    goalProgress: null,
    goalDialogOpen: false
  }),
  actions: {
    setInputMessage(value: string) {
      this.inputMessage = value;
    },
    clearInputMessage() {
      this.inputMessage = '';
    },
    setInputLineCount(count: number) {
      this.inputLineCount = count;
    },
    setInputMultiline(multiline: boolean) {
      this.inputIsMultiline = multiline;
    },
    setInputFocused(focused: boolean) {
      this.inputIsFocused = focused;
    },
    openQuickMenu() {
      this.quickMenuOpen = true;
    },
    setQuickMenuOpen(open: boolean) {
      this.quickMenuOpen = open;
      if (!open) {
        this.toolMenuOpen = false;
        this.settingsOpen = false;
      }
    },
    toggleQuickMenu() {
      const next = !this.quickMenuOpen;
      this.setQuickMenuOpen(next);
      return next;
    },
    closeMenus() {
      this.quickMenuOpen = false;
      this.toolMenuOpen = false;
      this.settingsOpen = false;
    },
    toggleToolMenu() {
      this.toolMenuOpen = !this.toolMenuOpen;
      return this.toolMenuOpen;
    },
    setToolMenuOpen(open: boolean) {
      this.toolMenuOpen = open;
    },
    toggleSettingsMenu() {
      this.settingsOpen = !this.settingsOpen;
      return this.settingsOpen;
    },
    setSettingsOpen(open: boolean) {
      this.settingsOpen = open;
    },
    setImagePickerOpen(open: boolean) {
      this.imagePickerOpen = open;
    },
    setVideoPickerOpen(open: boolean) {
      this.videoPickerOpen = open;
    },
    setSelectedImages(list: string[]) {
      this.selectedImages = list.slice(0, 9);
    },
    addSelectedImage(path: string) {
      if (!path) return;
      const next = Array.from(new Set([...this.selectedImages, path]));
      this.selectedImages = next.slice(0, 9);
    },
    removeSelectedImage(path: string) {
      this.selectedImages = this.selectedImages.filter((item) => item !== path);
    },
    clearSelectedImages() {
      this.selectedImages = [];
    },
    setSelectedVideos(list: string[]) {
      this.selectedVideos = list.slice(0, 1);
    },
    addSelectedVideo(path: string) {
      if (!path) return;
      this.selectedVideos = [path];
    },
    removeSelectedVideo(path: string) {
      this.selectedVideos = this.selectedVideos.filter((item) => item !== path);
    },
    clearSelectedVideos() {
      this.selectedVideos = [];
    },
    addSelectedFile(path: string) {
      if (!path) return;
      const next = Array.from(new Set([...this.selectedFiles, path]));
      this.selectedFiles = next.slice(0, 9);
    },
    removeSelectedFile(path: string) {
      this.selectedFiles = this.selectedFiles.filter((item) => item !== path);
    },
    clearSelectedFiles() {
      this.selectedFiles = [];
    },
    // ---- 目标模式 ----
    toggleGoalArmed() {
      // 运行中不允许通过开关切换
      if (this.goalRunning) return this.goalModeArmed;
      this.goalModeArmed = !this.goalModeArmed;
      return this.goalModeArmed;
    },
    setGoalArmed(armed: boolean) {
      this.goalModeArmed = armed;
    },
    setGoalRunning(running: boolean) {
      this.goalRunning = running;
      if (running) {
        this.goalModeArmed = false;
      }
    },
    setGoalProgress(progress: Record<string, any> | null) {
      this.goalProgress = progress;
    },
    setGoalDialogOpen(open: boolean) {
      this.goalDialogOpen = open;
    }
  }
});
