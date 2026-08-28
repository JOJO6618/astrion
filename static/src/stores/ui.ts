import { defineStore } from 'pinia';
import { t } from '@/locales';

type ResizingPanel = 'left' | 'right' | null;
type MobileOverlayTarget = 'conversation' | 'focus' | 'approval' | null;

interface QuotaToast {
  message: string;
  type?: string;
}

interface ToastItem {
  id: number;
  title?: string;
  message: string;
  type?: string;
  closable: boolean;
  timeoutId: ReturnType<typeof setTimeout> | null;
}

interface ToastOptions {
  title?: string;
  message?: string;
  type?: string;
  closable?: boolean;
  duration?: number | null;
}

interface ConfirmDialogOptions {
  title?: string;
  message?: string;
  confirmText?: string;
  cancelText?: string;
  warningText?: string;
  closeOnBackdrop?: boolean;
  confirmVariant?: 'primary' | 'danger';
  confirmOnLeft?: boolean;
}

interface ConfirmDialogState extends ConfirmDialogOptions {
  visible: boolean;
}

interface EasterEggState {
  active: boolean;
  effect: string | null;
  payload: any;
  instance: any;
  cleanupTimer: ReturnType<typeof setTimeout> | null;
  destroying: boolean;
  destroyPromise: Promise<void> | null;
}

interface UiState {
  sidebarCollapsed: boolean;
  chatDisplayMode: 'chat' | 'monitor';
  rightWidth: number;
  rightCollapsed: boolean;
  rightSplitRatio: number;
  isResizing: boolean;
  resizingPanel: ResizingPanel;
  minPanelWidth: number;
  maxPanelWidth: number;
  quotaToast: QuotaToast | null;
  quotaToastTimer: ReturnType<typeof setTimeout> | null;
  toastQueue: ToastItem[];
  nextToastId: number;
  confirmDialog: ConfirmDialogState | null;
  pendingConfirmResolver: ((value: boolean) => void) | null;
  easterEgg: EasterEggState;
  isMobileViewport: boolean;
  mobileOverlayMenuOpen: boolean;
  activeMobileOverlay: MobileOverlayTarget;
  // 图片大图预览（Lightbox）：url 为空表示关闭
  imagePreview: { url: string; name: string } | null;
}

// 首帧即判定移动端视口：初始值不能等 mounted 里的 matchMedia 监听，
// 否则移动端在对话加载完成前会被当作桌面端，QuickDock 等桌面 UI 会短暂渲染挤压页面。
const initialIsMobileViewport =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(max-width: 768px)').matches
    : false;

export const useUiStore = defineStore('ui', {
  state: (): UiState => ({
    sidebarCollapsed: true,
    chatDisplayMode: 'chat',
    rightWidth: 420,
    rightCollapsed: true,
    rightSplitRatio: 0.5,
    isResizing: false,
    resizingPanel: null,
    minPanelWidth: 350,
    maxPanelWidth: 600,
    quotaToast: null,
    quotaToastTimer: null,
    toastQueue: [],
    nextToastId: 1,
    confirmDialog: null,
    pendingConfirmResolver: null,
    easterEgg: {
      active: false,
      effect: null,
      payload: null,
      instance: null,
      cleanupTimer: null,
      destroying: false,
      destroyPromise: null
    },
    isMobileViewport: initialIsMobileViewport,
    mobileOverlayMenuOpen: false,
    activeMobileOverlay: null,
    imagePreview: null
  }),
  actions: {
    openImagePreview(payload: { url: string; name?: string }) {
      const url = String(payload?.url || '');
      if (!url) return;
      this.imagePreview = { url, name: String(payload?.name || '') };
    },
    closeImagePreview() {
      this.imagePreview = null;
    },
    setSidebarCollapsed(collapsed: boolean) {
      this.sidebarCollapsed = collapsed;
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed;
    },
    setChatDisplayMode(mode: 'chat' | 'monitor') {
      this.chatDisplayMode = mode;
    },
    toggleChatDisplayMode() {
      this.chatDisplayMode = this.chatDisplayMode === 'chat' ? 'monitor' : 'chat';
    },
    setRightWidth(width: number) {
      this.rightWidth = width;
    },
    setRightCollapsed(collapsed: boolean) {
      this.rightCollapsed = collapsed;
    },
    setRightSplitRatio(ratio: number) {
      this.rightSplitRatio = Math.max(0.15, Math.min(0.85, ratio));
    },
    setResizing(state: boolean, panel: ResizingPanel = null) {
      this.isResizing = state;
      this.resizingPanel = state ? panel : null;
    },
    setPanelBounds(min: number, max: number) {
      this.minPanelWidth = min;
      this.maxPanelWidth = max;
    },
    setIsMobileViewport(isMobile: boolean) {
      this.isMobileViewport = isMobile;
      if (!isMobile) {
        this.mobileOverlayMenuOpen = false;
        this.activeMobileOverlay = null;
      }
    },
    setMobileOverlayMenuOpen(open: boolean) {
      this.mobileOverlayMenuOpen = open;
    },
    toggleMobileOverlayMenu() {
      this.mobileOverlayMenuOpen = !this.mobileOverlayMenuOpen;
    },
    setActiveMobileOverlay(target: MobileOverlayTarget) {
      this.activeMobileOverlay = target;
    },
    closeMobileOverlay() {
      this.activeMobileOverlay = null;
    },
    showQuotaToastMessage(message: string, type: string = 'fast', duration = 5000) {
      this.quotaToast = { message, type };
      if (this.quotaToastTimer) {
        clearTimeout(this.quotaToastTimer);
      }
      this.quotaToastTimer = setTimeout(() => {
        this.dismissQuotaToast();
      }, duration);
    },
    dismissQuotaToast() {
      if (this.quotaToastTimer) {
        clearTimeout(this.quotaToastTimer);
        this.quotaToastTimer = null;
      }
      this.quotaToast = null;
    },
    pushToast(options: ToastOptions = {}) {
      const title = options.title || '';
      const message = options.message || '';
      if (!title && !message) {
        return 0;
      }
      const id = this.nextToastId++;
      const entry: ToastItem = {
        id,
        title,
        message,
        type: options.type || 'info',
        closable: options.closable !== false,
        timeoutId: null
      };
      const duration = Object.prototype.hasOwnProperty.call(options, 'duration')
        ? options.duration
        : 4000;
      if (duration !== null) {
        entry.timeoutId = setTimeout(() => this.dismissToast(id), duration);
      }
      this.toastQueue.push(entry);
      return id;
    },
    updateToast(id: number, patch: ToastOptions = {}) {
      const entry = this.toastQueue.find((item) => item.id === id);
      if (!entry) {
        return;
      }
      if (patch.title !== undefined) {
        entry.title = patch.title;
      }
      if (patch.message !== undefined) {
        entry.message = patch.message;
      }
      if (patch.type !== undefined) {
        entry.type = patch.type;
      }
      if (patch.closable !== undefined) {
        entry.closable = patch.closable;
      }
      if (Object.prototype.hasOwnProperty.call(patch, 'duration')) {
        if (entry.timeoutId) {
          clearTimeout(entry.timeoutId);
          entry.timeoutId = null;
        }
        if (patch.duration !== null && typeof patch.duration === 'number') {
          entry.timeoutId = setTimeout(() => this.dismissToast(id), patch.duration);
        }
      }
    },
    dismissToast(id: number) {
      const index = this.toastQueue.findIndex((item) => item.id === id);
      if (index === -1) {
        return;
      }
      const [entry] = this.toastQueue.splice(index, 1);
      if (entry && entry.timeoutId) {
        clearTimeout(entry.timeoutId);
      }
    },
    requestConfirm(options: ConfirmDialogOptions = {}) {
      return new Promise<boolean>((resolve) => {
        if (this.pendingConfirmResolver) {
          const previous = this.pendingConfirmResolver;
          this.pendingConfirmResolver = null;
          previous(false);
        }
        this.confirmDialog = {
          visible: true,
          title: options.title || t('stores.confirmAction'),
          message: options.message || t('stores.confirmOperation'),
          confirmText: options.confirmText || t('common.confirm'),
          cancelText: options.cancelText || t('common.cancel'),
          warningText: options.warningText || '',
          closeOnBackdrop: options.closeOnBackdrop !== false,
          confirmVariant: options.confirmVariant === 'danger' ? 'danger' : 'primary',
          confirmOnLeft: options.confirmOnLeft === true
        };
        this.pendingConfirmResolver = resolve;
      });
    },
    resolveConfirm(choice: boolean) {
      if (this.confirmDialog) {
        this.confirmDialog.visible = false;
      }
      const resolver = this.pendingConfirmResolver;
      this.pendingConfirmResolver = null;
      this.confirmDialog = null;
      if (resolver) {
        resolver(!!choice);
      }
    },
    setEasterEggState(patch: Partial<EasterEggState>) {
      this.easterEgg = {
        ...this.easterEgg,
        ...patch
      };
    },
    clearEasterEgg() {
      this.easterEgg = {
        active: false,
        effect: null,
        payload: null,
        instance: null,
        cleanupTimer: null,
        destroying: false,
        destroyPromise: null
      };
    }
  }
});
