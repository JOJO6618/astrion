// @ts-nocheck
import { useQuickDockStore } from '../../../stores/quickDock';
import { usePreviewStore } from '../../../stores/preview';
import {
  decideNarrowCollapse,
  getRightPanelsFootprint,
  hasRoomFor,
  measureChatWidth
} from '../../../components/chat/quickdock/narrowLayout';

/** 折叠动画（面板宽度过渡）结束前不再做下一步折叠，避免用过渡中的宽度误判。 */
const SETTLE_MS = 500;
const DEBOUNCE_MS = 120;
const ATTACH_RETRY_MS = 1000;

let observer: ResizeObserver | null = null;
let observedEl: Element | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let settleTimer: ReturnType<typeof setTimeout> | null = null;
let attachTimer: ReturnType<typeof setTimeout> | null = null;
let onWindowResize: (() => void) | null = null;
let onMouseUp: (() => void) | null = null;

function isDragging() {
  // 终端/Git 与预览面板的拖拽调宽都会把 body cursor 设为 col-resize
  return typeof document !== 'undefined' && document.body.style.cursor === 'col-resize';
}

export const narrowLayoutMethods = {
  setupNarrowLayoutGuard() {
    if (typeof window === 'undefined' || typeof ResizeObserver === 'undefined') return;
    this.teardownNarrowLayoutGuard();
    const schedule = () => this.scheduleNarrowLayoutCheck();
    onWindowResize = schedule;
    onMouseUp = schedule; // 拖拽结束后补一次检查（拖拽期间跳过）
    window.addEventListener('resize', onWindowResize);
    document.addEventListener('mouseup', onMouseUp);
    observer = new ResizeObserver(schedule);
    const attach = () => {
      attachTimer = null;
      const el = document.querySelector('.chat-container');
      if (el) {
        if (el !== observedEl) {
          if (observedEl) observer?.unobserve(observedEl);
          observer?.observe(el);
          observedEl = el;
        }
        schedule();
      }
      // 对话区可能随路由重建，持续低频确认监听目标仍是当前元素
      attachTimer = setTimeout(attach, ATTACH_RETRY_MS);
    };
    attach();
  },

  teardownNarrowLayoutGuard() {
    if (onWindowResize) window.removeEventListener('resize', onWindowResize);
    if (onMouseUp) document.removeEventListener('mouseup', onMouseUp);
    onWindowResize = null;
    onMouseUp = null;
    observer?.disconnect();
    observer = null;
    observedEl = null;
    for (const t of [debounceTimer, settleTimer, attachTimer]) {
      if (t) clearTimeout(t);
    }
    debounceTimer = settleTimer = attachTimer = null;
  },

  scheduleNarrowLayoutCheck() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      this.runNarrowLayoutCheck();
    }, DEBOUNCE_MS);
  },

  runNarrowLayoutCheck() {
    // 移动端快捷窗口是整层悬浮，不占对话宽度；设置页没有对话区
    if (this.isMobileViewport || this.settingsRoute) return;
    if (settleTimer || isDragging()) return;

    const quickDock = useQuickDockStore();
    const preview = usePreviewStore();
    const quickGroupOpen = !!(quickDock.expanded || quickDock.previewPath || preview.activeKey);
    const rightPanelsOpen = !!(this.terminalPanelOpen || this.gitChangesPanelOpen);

    // 整组关闭后，强制保留标记失效（"直到被关闭"）
    if (!quickGroupOpen && quickDock.quickGroupForced) quickDock.quickGroupForced = false;
    if (!rightPanelsOpen && this.rightPanelsForced) this.rightPanelsForced = false;

    const target = decideNarrowCollapse({
      chatWidth: measureChatWidth(),
      quickGroupOpen,
      quickGroupForced: quickDock.quickGroupForced,
      rightPanelsOpen,
      rightPanelsForced: !!this.rightPanelsForced
    });
    if (!target) return;

    if (target === 'quick-group') {
      // 快捷面板、文件内容预览、预览窗口绑定：一起收
      quickDock.userCollapsed = true;
      quickDock.resetTransient();
      preview.closeWithQuickDock();
    } else {
      this.closeTerminalPanel();
      this.closeGitChangesPanel();
    }
    // 动画结束后再复查一次：第一组收完仍不足时继续收下一组
    settleTimer = setTimeout(() => {
      settleTimer = null;
      this.scheduleNarrowLayoutCheck();
    }, SETTLE_MS);
  },

  /** 顶部按钮：手动展开/收起快捷面板；空间不足时的展开为强制展开 */
  toggleQuickDock() {
    const quickDock = useQuickDockStore();
    if (quickDock.userCollapsed) {
      quickDock.manualExpand();
    } else {
      quickDock.userCollapsed = true;
    }
  },

  /** 打开终端/Git 前调用：空间不足时标记右侧面板组强制保留 */
  markRightPanelsManualOpen() {
    if (this.terminalPanelOpen || this.gitChangesPanelOpen) return;
    if (!hasRoomFor(getRightPanelsFootprint(this.rightWidth))) {
      this.rightPanelsForced = true;
    }
  },

  /** 终端自动打开前的空间检查（自动路径不得在窄布局下打开） */
  canAutoOpenRightPanels() {
    return hasRoomFor(getRightPanelsFootprint(this.rightWidth));
  }
};
