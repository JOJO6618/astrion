import type { MonitorBubbleOptions, MonitorDriver, MonitorSceneRuntime } from './types';
import { getSceneProgressLabel } from './progressMap';
import { t } from '@/locales';

type SceneHandler = (payload: Record<string, any>, runtime: MonitorSceneRuntime) => Promise<void>;

type ContextMenuType = 'desktop' | 'folder' | 'file' | 'browser' | 'terminal' | 'focus';
type TerminalMenuAction = 'snapshot' | 'reset' | 'close';

interface MonitorAssets {
  brainIcon: string;
  folderIcon: string;
  folderOpenIcon: string;
  fileIcon: string;
  apps: Record<string, string>;
}

export interface MonitorElements {
  screen: HTMLElement;
  appsGrid: HTMLElement;
  desktopGrid: HTMLElement;
  browserWindow: HTMLElement;
  browserSearchText: HTMLElement;
  browserStatus: HTMLElement;
  browserResults: HTMLElement;
  extractionWindow: HTMLElement;
  extractionUrl: HTMLElement;
  extractionState: HTMLElement;
  extractionSummary: HTMLElement;
  folderWindow: HTMLElement;
  folderHeaderText: HTMLElement;
  folderBody: HTMLElement;
  editorWindow: HTMLElement;
  editorHeaderText: HTMLElement;
  editorBody: HTMLElement;
  terminalWindow: HTMLElement;
  terminalHeaderText: HTMLElement;
  terminalTabs: HTMLElement;
  terminalTabList: HTMLElement;
  terminalAddButton: HTMLElement;
  terminalBody: HTMLElement;
  commandWindow: HTMLElement;
  commandTitle: HTMLElement;
  commandInput: HTMLElement;
  commandOutput: HTMLElement;
  pythonWindow: HTMLElement;
  pythonTitle: HTMLElement;
  pythonBody: HTMLElement;
  pythonInput: HTMLElement;
  pythonOutput: HTMLElement;
  readerWindow: HTMLElement;
  readerTitle: HTMLElement;
  readerLines: HTMLElement;
  readerOcr: HTMLElement;
  memoryWindow: HTMLElement;
  memoryList: HTMLElement;
  memoryStatus: HTMLElement;
  memoryCount: HTMLElement;
  memoryTime: HTMLElement;
  todoWindow: HTMLElement;
  todoSummary: HTMLElement;
  todoList: HTMLElement;
  waitWindow: HTMLElement;
  waitDisplay: HTMLElement;
  waitOverlay: HTMLElement;
  waitCountdown: HTMLElement;
  desktopMenu: HTMLElement;
  folderMenu: HTMLElement;
  fileMenu: HTMLElement;
  focusMenu: HTMLElement;
  browserMenu: HTMLElement;
  terminalMenu: HTMLElement;
  speechBubble: HTMLElement;
  bubbleIconSlot: HTMLElement;
  bubbleTextSlot: HTMLElement;
  mousePointer: HTMLElement;
}

type ReaderLine = {
  text: string;
  lineNumber?: number;
  highlight?: boolean;
};

type EditorOperation = {
  type: 'insert' | 'delete' | 'replace';
  index: number;
  text?: string;
};

const EDITOR_MAX_RENDER_LINES = 360;
const EDITOR_DIFF_LIMIT = 2000;
const EDITOR_MAX_ANIMATION_STEPS = 4000;
const EDITOR_TYPING_THRESHOLD = 180;
const EDITOR_TYPING_INTERVAL = 34;
const EDITOR_ERASE_INTERVAL = 26;
const RENAME_ERASE_INTERVAL = 30;
const RENAME_TYPE_INTERVAL = 32;
const MONITOR_EDITOR_DEBUG = false;

const MONITOR_READER_DEBUG = false;
const MONITOR_RENAME_DEBUG = false;
const readerDebug = (...args: any[]) => {
  if (!MONITOR_READER_DEBUG) {
    return;
  }
  console.debug('[MonitorReader]', ...args);
};
const renameDebug = (...args: any[]) => {
  if (!MONITOR_RENAME_DEBUG) {
    return;
  }
  console.info('[MonitorRename]', ...args);
};
const editorDebug = (...args: any[]) => {
  if (!MONITOR_EDITOR_DEBUG) {
    return;
  }
  console.log('[MonitorEditor]', ...args);
};
const MONITOR_PROGRESS_DEBUG = false;
const progressDebug = (...args: any[]) => {
  if (!MONITOR_PROGRESS_DEBUG) {
    return;
  }
  console.debug('[MonitorProgress]', ...args);
};
const MONITOR_LIFECYCLE_DEBUG = false;
const monitorLifecycleDebug = (...args: any[]) => {
  if (!MONITOR_LIFECYCLE_DEBUG) {
    return;
  }
  console.info('[MonitorDirector]', ...args);
};
const MONITOR_TERMINAL_DEBUG = true;
const terminalMenuDebug = (...args: any[]) => {
  if (!MONITOR_TERMINAL_DEBUG) {
    return;
  }
  // 使用 warn 级别，避免浏览器控制台过滤掉 info 级别
  console.warn('[TerminalMenu]', ...args);
};

// 桌面应用图标标签存 key，渲染处再 t(labelKey) 解析（i18n_spec §3.2：避免顶层常量固化语言）
const DESKTOP_APPS: Array<{ id: string; labelKey: string; assetKey: string }> = [
  { id: 'browser', labelKey: 'monitor.appBrowser', assetKey: 'browser' },
  { id: 'terminal', labelKey: 'monitor.appTerminal', assetKey: 'terminal' },
  { id: 'command', labelKey: 'monitor.appCommand', assetKey: 'command' },
  { id: 'python', labelKey: 'monitor.appPython', assetKey: 'python' },
  { id: 'memory', labelKey: 'monitor.appMemory', assetKey: 'memory' },
  { id: 'todo', labelKey: 'monitor.appTodo', assetKey: 'todo' },
  { id: 'subagent', labelKey: 'monitor.appSubagent', assetKey: 'subagent' }
];

const WINDOW_PADDING = 18;
const WINDOW_TOP_OFFSET = 120;
const MAX_VISIBLE_WINDOWS = 5;
const POINTER_TIP_OFFSET = { x: 8, y: 6 };
const BUBBLE_SCREEN_PADDING = 12;
const BUBBLE_VERTICAL_GAP = 26;
const BUBBLE_ARROW_GUTTER = 24;
const BUBBLE_POINTER_LEFT_ANCHOR = 32;
const SCREEN_INTERACTIVE_CLASS = 'manual-interactive';

type FolderEntry = { name: string; type: 'folder' | 'file'; path: string };
type ExtractionWindowInstance = {
  id: string;
  element: HTMLElement;
  urlEl: HTMLElement;
  stateEl: HTMLElement;
  summaryEl: HTMLElement;
  titleEl: HTMLElement | null;
};

type TerminalSessionRecord = {
  id: string;
  name: string;
  rawName?: string;
};

type TerminalShell = {
  element: HTMLElement;
  bodyEl: HTMLElement;
  titleEl: HTMLElement | null;
};

type TerminalLine = {
  text: string;
  role: 'prompt' | 'output' | 'note';
};


const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class MonitorDirector implements MonitorDriver {
  private elements: MonitorElements;
  private assets: MonitorAssets;
  private desktopRoots: string[] = [];
  private screenRect: DOMRect;
  private pointerBase = { x: 60, y: 120 };
  private pendingPointerTransform: { x: number; y: number; duration: number } | null = null;
  private screenObserver: ResizeObserver | null = null;
  private bubbleTimer: number | null = null;
  private windowAnchors = new Map<HTMLElement, { x: number; y: number }>();
  private destroyFns: Array<() => void> = [];
  private appIcons = new Map<string, HTMLElement>();
  private folderIcons = new Map<string, HTMLElement>();
  // 用于控制编辑器动画全局加速（根据本次补丁的总体改动量动态调整）
  private editorSpeedBoost = 1;
  private pythonRunToken = 0;
  private pendingDesktopFolders = new Set<string>();
  private pendingCreateEntries = new Set<string>();
  private fileIcons = new Map<string, HTMLElement>();
  private browserResultMap = new Map<string, HTMLLIElement>();
  private folderEntries = new Map<string, FolderEntry[]>();
  private activeFolder: string | null = null;
  private sceneHandlers: Record<string, SceneHandler> = {};
  private windowOrder: HTMLElement[] = [];
  private maxVisibleWindows = MAX_VISIBLE_WINDOWS;
  private extractionAnchor = { x: 0.6, y: 0.04 };
  private extractionWindows = new Map<string, ExtractionWindowInstance>();
  private extractionTemplate: ExtractionWindowInstance | null = null;
  private terminalSessions = new Map<string, TerminalSessionRecord>();
  private terminalSessionTitleMap = new Map<string, string>();
  private terminalRawNameMap = new Map<string, string>();
  private terminalSessionNames = new Map<string, string>();
  private terminalHistories = new Map<string, TerminalLine[]>();
  // 用于右键菜单记住目标终端
  private terminalContextSessionId: string | null = null;
  private activeTerminalSessionId: string | null = null;
  private manualInteractionEnabled = false;
  private manualListenersAttached = false;
  private manualPositions = new Map<HTMLElement, { left: number; top: number }>();
  private manualDragState: {
    pointerId: number;
    target: HTMLElement;
    offsetX: number;
    offsetY: number;
  } | null = null;
  private editorScene = {
    lines: [] as string[],
    placeholder: false
  };
  private editorSnapshots = new Map<string, string[]>();
  private commandCurrentText = '';
  private progressBubbleTimer: number | null = null;
  private progressBubbleBase: string | null = null;
  private progressSceneName: string | null = null;
  private latestMemoryScroll = 0;
  private desktopRenderLocked = false;
  private pendingDesktopRoots: string[] | null = null;
  private thinkingBubbleTimer: number | null = null;
  private thinkingBubblePhase = 0;
  private waitingBubbleTimer: number | null = null;
  private waitingBubbleBase: string | null = null;
  private progressBubbleActive = false;
  private secondaryMenu: HTMLElement | null = null;
  private waitDigits: HTMLElement[] = [];
  private waitOverlayTimer: number | null = null;
  // 当实际执行进度快于动画播放时，用于压制“正在 xxx”提示
  private playbackLagging = false;
  // 记录最近一次 Python 执行的ID，用于丢弃过期动画/结果
  private latestPythonExecutionId: string | number | null = null;
  private lastTerminalSessionId: string | null = null;
  private terminalLastFocusedAt = 0;

  private refreshScreenRect() {
    const prev = this.screenRect;
    const rect = this.elements.screen.getBoundingClientRect();
    // 隐藏状态下 rect 可能为 0，避免把指针归零
    if (rect.width < 1 || rect.height < 1) {
      return;
    }
    this.screenRect = rect;
    if (prev && prev.width > 0 && prev.height > 0) {
      const relX = this.pointerBase.x / prev.width;
      const relY = this.pointerBase.y / prev.height;
      this.pointerBase = {
        x: relX * rect.width,
        y: relY * rect.height
      };
    }
  }

  private applySceneStatus(runtime: MonitorSceneRuntime, sceneName: string, fallback: string) {
    if (!runtime || typeof runtime.setStatus !== 'function') {
      return;
    }
    const label = getSceneProgressLabel(sceneName) || fallback;
    try {
      runtime.setStatus(label);
    } catch (error) {
      console.warn('[MonitorDirector] failed to set scene status', sceneName, error);
    }
  }

  constructor(elements: MonitorElements, assets: MonitorAssets) {
    this.elements = elements;
    this.assets = assets;
    this.screenRect = elements.screen.getBoundingClientRect();
    this.setupAnchors();
    this.setupScenes();
    this.bindTerminalInteractions();
    this.populateDesktop();
    this.setupScreenObserver();
    this.extractionAnchor =
      this.windowAnchors.get(this.elements.extractionWindow) || this.extractionAnchor;
    this.prepareExtractionTemplate();
    this.layoutFloatingWindows();
    // 标记当前构建，便于用户确认是否加载了最新前端代码
    (window as any).__TERMINAL_MENU_DEBUG_BUILD = '2025-12-13-1';
    terminalMenuDebug('constructor:init', { build: (window as any).__TERMINAL_MENU_DEBUG_BUILD });
    const resizeHandler = () => {
      this.refreshScreenRect();
      this.layoutFloatingWindows();
      this.flushPendingPointerTransform();
    };
    window.addEventListener('resize', resizeHandler, { passive: true });
    this.destroyFns.push(() => window.removeEventListener('resize', resizeHandler));
  }

  destroy() {
    this.setManualInteractionEnabled(false);
    this.destroyFns.forEach((fn) => fn());
    this.destroyFns = [];
    this.stopBubbleTimers();
  }

  resetScene(options?: {
    desktopRoots?: string[];
    preserveBubble?: boolean;
    preservePointer?: boolean;
    preserveWindows?: boolean;
  }) {
    // 清理挂起的指针位置，避免后续尺寸变化时覆盖当前重置
    this.pendingPointerTransform = null;
    const preserveWindows = !!options?.preserveWindows;
    const preservePointer = options?.preservePointer !== false; // 默认保留指针位置
    this.cancelManualDrag();
    if (!preserveWindows) {
      this.resetManualPositions();
      this.manualPositions.clear();
      this.hideAllWindows();
      this.activeFolder = null;
      this.refreshFolderIconStates();
    } else {
      this.windowOrder = this.windowOrder.filter((win) => win && win.classList.contains('visible'));
    }
    this.hideContextMenus();
    if (!options?.preserveBubble) {
      this.dismissBubble(true, { force: true });
    } else {
      this.stopBubbleTimers();
    }
    if (!preservePointer) {
      this.pointerBase = { x: 60, y: 120 };
      this.elements.mousePointer.style.transform = 'translate3d(60px, 120px, 0)';
    }
    if (!preserveWindows) {
      this.elements.browserSearchText.textContent = '';
      this.elements.browserStatus.textContent = t('monitor.browserReady');
      this.elements.browserResults.innerHTML = '';
      this.browserResultMap.clear();
      this.elements.extractionSummary.innerHTML = '';
      this.elements.extractionState.textContent = t('monitor.extractWaiting');
      this.elements.folderBody.innerHTML = '';
      this.elements.editorBody.innerHTML = '';
      this.elements.terminalBody.innerHTML = '';
      this.terminalHistories.clear();
      this.terminalSessions.clear();
      this.terminalSessionNames.clear();
      this.terminalSessionTitleMap.clear();
      this.terminalContextSessionId = null;
      this.activeTerminalSessionId = null;
      this.terminalLastFocusedAt = 0;
      this.elements.readerLines.innerHTML = '';
      this.elements.readerOcr.innerHTML = '';
      this.elements.todoSummary.textContent = '';
      this.elements.todoList.innerHTML = '';
      this.folderIcons.clear();
      this.fileIcons.clear();
    }
    if (Array.isArray(options?.desktopRoots) && options?.desktopRoots.length) {
      this.setDesktopRoots(options.desktopRoots, { immediate: true });
    } else if (this.desktopRoots.length) {
      // 保持当前根目录重新渲染，避免在重置后桌面空白
      this.setDesktopRoots(this.desktopRoots, { immediate: true });
    }
    this.renderTerminalTabs();
  }

  setDesktopRoots(roots: string[], options?: { immediate?: boolean }) {
    if (this.desktopRenderLocked) {
      this.pendingDesktopRoots = Array.isArray(roots) ? [...roots] : [];
      monitorLifecycleDebug('setDesktopRoots:locked', { pending: this.pendingDesktopRoots.length });
      return;
    }
    const nextRoots = Array.isArray(roots) ? [...roots] : [];
    const previousRoots = [...this.desktopRoots];
    this.desktopRoots = nextRoots;
    if (!this.desktopRoots.length) {
      this.folderEntries.clear();
    } else {
      const preserved = new Map<string, FolderEntry[]>();
      this.folderEntries.forEach((entries, key) => {
        const root = key.split('/')[0];
        if (this.desktopRoots.includes(root)) {
          preserved.set(key, entries);
        }
      });
      this.desktopRoots.forEach((root) => {
        if (!preserved.has(root)) {
          preserved.set(root, []);
        }
      });
      this.folderEntries = preserved;
    }
    if (options?.immediate) {
      this.pendingDesktopFolders.clear();
    } else {
      const previousSet = new Set(previousRoots);
      this.desktopRoots.forEach((folder) => {
        if (!previousSet.has(folder)) {
          const existing = this.folderIcons.get(folder);
          if (existing && !existing.classList.contains('pending-reveal')) {
            return;
          }
          this.pendingDesktopFolders.add(folder);
        }
      });
      Array.from(this.pendingDesktopFolders).forEach((folder) => {
        if (!this.desktopRoots.includes(folder)) {
          this.pendingDesktopFolders.delete(folder);
        }
      });
    }
    this.renderDesktopFolders();
    this.refreshFolderIconStates();
  }

  setManualInteractionEnabled(enabled: boolean) {
    if (enabled === this.manualInteractionEnabled) {
      return;
    }
    if (enabled) {
      this.manualInteractionEnabled = true;
      this.elements.screen.classList.add(SCREEN_INTERACTIVE_CLASS);
      this.bindManualInteractionListeners();
      return;
    }
    this.manualInteractionEnabled = false;
    this.elements.screen.classList.remove(SCREEN_INTERACTIVE_CLASS);
    this.cancelManualDrag();
    this.unbindManualInteractionListeners();
  }

  showSpeechBubble(text: string, options: MonitorBubbleOptions = {}) {
    const { variant = 'info', iconSvg = null, icon = null, duration } = options;
    const resolvedDuration = typeof duration === 'number' ? duration : 0;
    this.dismissBubble(true, { force: true });
    const bubble = this.elements.speechBubble;
    bubble.classList.remove('thinking', 'error', 'info');
    bubble.classList.add(variant);
    this.elements.bubbleTextSlot.textContent = text;
    this.elements.bubbleIconSlot.innerHTML = '';
    if (variant === 'thinking' && iconSvg) {
      const img = document.createElement('img');
      img.src = iconSvg;
      img.alt = '';
      this.elements.bubbleIconSlot.appendChild(img);
      this.elements.bubbleIconSlot.classList.add('show');
    } else if (variant === 'error' && icon) {
      this.elements.bubbleIconSlot.textContent = icon;
      this.elements.bubbleIconSlot.classList.add('show');
    } else {
      this.elements.bubbleIconSlot.classList.remove('show');
    }
    this.positionBubble();
    requestAnimationFrame(() => {
      bubble.classList.add('visible');
      bubble.style.visibility = 'visible';
    });
    this.elements.bubbleTextSlot.scrollTop = this.elements.bubbleTextSlot.scrollHeight;
    this.stopBubbleTimers();
    if (resolvedDuration > 0) {
      this.bubbleTimer = window.setTimeout(() => {
        bubble.classList.remove('visible');
        bubble.style.visibility = '';
      }, resolvedDuration);
    }
  }

  showThinkingBubble() {
    this.stopBubbleTimers();
    this.dismissBubble(true, { force: true });
    const bubble = this.elements.speechBubble;
    bubble.classList.remove('error', 'thinking', 'progress');
    bubble.classList.add('info');
    this.elements.bubbleIconSlot.classList.remove('show');
    this.elements.bubbleIconSlot.innerHTML = '';
    this.startThinkingBubble(t('monitor.thinkLabel'));
    this.positionBubble();
    requestAnimationFrame(() => {
      bubble.classList.add('visible');
      bubble.style.visibility = 'visible';
    });
  }

  hideBubble() {
    this.dismissBubble(true, { force: true });
  }

  previewSceneProgress(name: string) {
    // 先清理上一段进度气泡，避免多个计时器交替刷新导致闪烁
    this.stopProgressBubble();
    const progressLabel = getSceneProgressLabel(name);
    progressDebug('previewSceneProgress', { scene: name, label: progressLabel });
    if (!progressLabel) {
      return;
    }
    this.progressSceneName = name;
    monitorLifecycleDebug('previewSceneProgress', {
      scene: name,
      label: progressLabel,
      hasDriver: true
    });
    this.startProgressBubble(progressLabel);
  }

  async playScene(
    name: string,
    payload: Record<string, any>,
    runtime: MonitorSceneRuntime
  ): Promise<void> {
    const handler = this.sceneHandlers[name] || this.sceneHandlers.genericTool;
    const progressLabel = getSceneProgressLabel(name);
    const hasPreviewForScene = !!this.progressBubbleBase && this.progressSceneName === name;
    const isPlaybackPhase = runtime?.statusPhase === 'playback';
    // 预先缓存 wait 结果，避免重复请求并让错误尽早暴露
    const waitFn = runtime.waitForResult || (() => Promise.resolve(null));
    let waitedOnce = false;
    let cachedResult: any = null;
    const innerWait = async (id?: string | number | null) => {
      if (waitedOnce) {
        return cachedResult;
      }
      waitedOnce = true;
      cachedResult = await waitFn(id);
      return cachedResult;
    };

    // 结果已明确失败时，直接提示错误，避免播放后续动画
    const preStatus = String(payload?.status || payload?.result?.status || '').toLowerCase();
    const preFailed =
      ['failed', 'error'].includes(preStatus) ||
      payload?.error ||
      payload?.success === false ||
      payload?.result?.success === false ||
      (typeof payload?.result?.error === 'string' && payload.result.error);
    monitorLifecycleDebug('playScene:prefail-check', {
      scene: name,
      preStatus,
      preFailed,
      statusPhase: runtime?.statusPhase,
      hasPreviewForScene
    });
    if (preFailed) {
      const message =
        payload?.result?.error ||
        payload?.error ||
        payload?.result?.message ||
        payload?.message ||
        t('monitor.toolError');
      this.stopProgressBubble();
      this.showSpeechBubble(message, { variant: 'error', duration: 2600 });
      monitorLifecycleDebug('playScene:skip-on-error', { scene: name, status: preStatus, message });
      return;
    }

    monitorLifecycleDebug('playScene:start', {
      scene: name,
      progressLabel,
      hasPreviewForScene,
      statusPhase: runtime?.statusPhase
    });
    if (name.startsWith('terminal')) {
      terminalMenuDebug('playScene:enter', { scene: name, payload });
    }
    if (!hasPreviewForScene) {
      this.dismissBubble(true, { force: true });
    } else if (isPlaybackPhase) {
      progressDebug('playScene:stop-progress-for-playback', { scene: name, label: progressLabel });
      this.stopProgressBubble();
    } else {
      progressDebug('playScene:preserve-progress', { scene: name, label: progressLabel });
    }
    let progressActive = hasPreviewForScene && !isPlaybackPhase;
    const ensureProgressBubble = () => {
      if (!progressLabel || progressActive || isPlaybackPhase) {
        return;
      }
      progressActive = true;
      this.progressSceneName = name;
      progressDebug('playScene:ensure-progress', { scene: name, label: progressLabel });
      this.startProgressBubble(progressLabel);
    };
    const clearProgressBubble = () => {
      if (!progressActive) {
        return;
      }
      progressActive = false;
      progressDebug('playScene:clear-progress', { scene: name, label: progressLabel });
      this.stopProgressBubble();
    };
    if (progressLabel && !isPlaybackPhase) {
      ensureProgressBubble();
    }

    // 在回放阶段先等待结果，若失败直接返回以避免动画播放
    if (isPlaybackPhase) {
      const waitKey = payload?.executionId ?? payload?.id ?? payload?.arguments?.id ?? null;
      try {
        const preResult = await innerWait(waitKey);
        const preResultStatus = String(
          preResult?.status || preResult?.result?.status || ''
        ).toLowerCase();
        const preResultFailed =
          ['failed', 'error'].includes(preResultStatus) ||
          preResult?.success === false ||
          preResult?.result?.success === false ||
          preResult?.error ||
          (typeof preResult?.result?.error === 'string' && preResult.result.error);
        monitorLifecycleDebug('playScene:prefetch-result', {
          scene: name,
          waitKey,
          preResultStatus,
          preResultFailed
        });
        if (preResultFailed) {
          const message =
            preResult?.result?.error ||
            preResult?.error ||
            payload?.result?.error ||
            payload?.error ||
            t('monitor.toolError');
          this.stopProgressBubble();
          this.showSpeechBubble(message, { variant: 'error', duration: 2600 });
          monitorLifecycleDebug('playScene:skip-after-prefetch-error', { scene: name, message });
          return;
        }
      } catch (error: any) {
        monitorLifecycleDebug('playScene:prefetch-error', { scene: name, error: String(error) });
        this.stopProgressBubble();
        this.showSpeechBubble(t('monitor.toolError'), { variant: 'error', duration: 2600 });
        return;
      }
    }

    // 若进入播放阶段且有执行结果已完成，压制“正在…”提示
    if (isPlaybackPhase) {
      this.playbackLagging = true;
    } else {
      this.playbackLagging = false;
    }

    const wrappedRuntime: MonitorSceneRuntime = {
      ...runtime,
      waitForResult: async (id?: string | number | null) => {
        if (!isPlaybackPhase && !this.playbackLagging) {
          ensureProgressBubble();
        }
        const waitKey = id ?? payload?.executionId ?? payload?.id;
        progressDebug('playScene:waitForResult:start', { scene: name, id: waitKey });
        try {
          const result = await innerWait(id);
          progressDebug('playScene:waitForResult:resolved', { scene: name, id: waitKey });
          this.playbackLagging = false;
          const resultStatus = String(result?.status || result?.result?.status || '').toLowerCase();
          const failed =
            ['failed', 'error'].includes(resultStatus) ||
            result?.success === false ||
            result?.result?.success === false ||
            result?.error ||
            (typeof result?.result?.error === 'string' && result.result.error);
          monitorLifecycleDebug('playScene:waitForResult:status', {
            scene: name,
            id: waitKey,
            resultStatus,
            failed
          });
          if (failed) {
            const message =
              result?.result?.error ||
              result?.error ||
              payload?.result?.error ||
              payload?.error ||
              t('monitor.toolError');
            this.stopProgressBubble();
            this.showSpeechBubble(message, { variant: 'error', duration: 2600 });
            throw new Error('tool-failed');
          }
          return result;
        } finally {
          clearProgressBubble();
        }
      }
    };
    try {
      await handler(payload, wrappedRuntime);
    } finally {
      clearProgressBubble();
      monitorLifecycleDebug('playScene:end', {
        scene: name
      });
      this.playbackLagging = false;
    }
  }

  private stopBubbleTimers() {
    if (this.bubbleTimer) {
      clearTimeout(this.bubbleTimer);
      this.bubbleTimer = null;
    }
    this.clearThinkingBubbleTimer();
    if (this.waitingBubbleTimer) {
      clearTimeout(this.waitingBubbleTimer);
      this.waitingBubbleTimer = null;
    }
  }

  private dismissBubble(immediate = false, options?: { force?: boolean }) {
    // 当 progress 气泡处于活跃状态时，非强制模式下不打断；强制模式彻底清理
    if (this.progressBubbleActive && !options?.force) {
      progressDebug('dismissBubble:skip-active', { immediate });
      return;
    }
    const bubble = this.elements.speechBubble;
    this.stopBubbleTimers();
    if (options?.force) {
      if (this.progressBubbleTimer) {
        window.clearTimeout(this.progressBubbleTimer);
        this.progressBubbleTimer = null;
      }
      this.progressBubbleBase = null;
      this.progressSceneName = null;
      this.progressBubbleActive = false;
      this.clearThinkingBubbleTimer();
      if (this.waitingBubbleTimer) {
        clearTimeout(this.waitingBubbleTimer);
        this.waitingBubbleTimer = null;
        this.waitingBubbleBase = null;
      }
      bubble.classList.remove('progress');
      bubble.removeAttribute('data-progress');
    }
    if (!bubble.classList.contains('visible')) {
      if (immediate) {
        bubble.classList.remove('thinking', 'error', 'info');
        bubble.style.visibility = '';
      }
      return;
    }
    bubble.classList.remove('visible', 'thinking', 'error', 'info');
    if (immediate) {
      bubble.style.visibility = '';
    }
  }

  private startProgressBubble(text: string) {
    progressDebug('startProgressBubble', { text, scene: this.progressSceneName });
    // 确保旧的进度定时器被清除，避免多个气泡交错刷新
    if (this.progressBubbleTimer) {
      window.clearTimeout(this.progressBubbleTimer);
      this.progressBubbleTimer = null;
    }
    this.progressBubbleBase = text;
    this.progressBubbleActive = true;
    let phase = 0;
    const tick = () => {
      const dots = '.'.repeat(phase);
      const display = `${text}${dots}`;
      this.renderProgressBubble(display);
      phase = (phase + 1) % 4;
      this.progressBubbleTimer = window.setTimeout(tick, 520);
    };
    tick();
  }

  private renderProgressBubble(text: string) {
    progressDebug('renderProgressBubble', { text });
    const bubble = this.elements.speechBubble;
    bubble.classList.remove('thinking', 'error');
    bubble.classList.add('info');
    bubble.classList.add('progress');
    bubble.setAttribute('data-progress', '1');
    this.elements.bubbleIconSlot.classList.remove('show');
    this.elements.bubbleIconSlot.innerHTML = '';
    this.elements.bubbleTextSlot.textContent = text;
    this.positionBubble();
    this.elements.bubbleTextSlot.scrollTop = this.elements.bubbleTextSlot.scrollHeight;
    if (!bubble.classList.contains('visible')) {
      bubble.style.visibility = 'visible';
      requestAnimationFrame(() => bubble.classList.add('visible'));
    }
  }

  private startThinkingBubble(text: string) {
    this.clearThinkingBubbleTimer();
    // 直接复用等待气泡的动画逻辑，保证与“等待回复”一致
    this.showWaitingBubble(text || t('monitor.thinkLabel'));
    // 但保留独立的计时器引用，方便后续清理
    this.thinkingBubbleTimer = this.waitingBubbleTimer;
  }

  private clearThinkingBubbleTimer() {
    if (this.thinkingBubbleTimer) {
      clearInterval(this.thinkingBubbleTimer);
      this.thinkingBubbleTimer = null;
    }
    this.thinkingBubblePhase = 0;
  }

  showWaitingBubble(text = t('monitor.waitingReply')) {
    this.dismissBubble(true, { force: true });
    const bubble = this.elements.speechBubble;
    bubble.classList.remove('error', 'thinking', 'progress');
    bubble.classList.add('info');
    this.elements.bubbleIconSlot.classList.remove('show');
    this.elements.bubbleIconSlot.innerHTML = '';
    this.waitingBubbleBase = text;
    let phase = 0;
    const tick = () => {
      const dots = '.'.repeat(phase);
      this.elements.bubbleTextSlot.textContent = `${text}${dots}`;
      this.positionBubble();
      phase = (phase + 1) % 4;
      this.waitingBubbleTimer = window.setTimeout(tick, 520);
    };
    tick();
    requestAnimationFrame(() => {
      bubble.classList.add('visible');
      bubble.style.visibility = 'visible';
    });
  }

  private stopProgressBubble(options?: { preserveBubble?: boolean }) {
    progressDebug('stopProgressBubble', {
      label: this.progressBubbleBase,
      scene: this.progressSceneName,
      preserve: !!options?.preserveBubble
    });
    if (this.progressBubbleTimer) {
      window.clearTimeout(this.progressBubbleTimer);
      this.progressBubbleTimer = null;
    }
    const bubble = this.elements.speechBubble;
    const shouldHideBubble =
      this.progressBubbleActive &&
      !options?.preserveBubble &&
      bubble.classList.contains('progress');
    this.progressBubbleBase = null;
    this.progressSceneName = null;
    this.progressBubbleActive = false;
    bubble.classList.remove('progress');
    bubble.removeAttribute('data-progress');
    if (shouldHideBubble) {
      this.dismissBubble(true, { force: true });
    }
  }

  private positionBubble() {
    // 隐藏状态（display: none）时不更新气泡与指针，避免坐标被重置到原点
    if (this.elements.screen.clientWidth < 1 || this.elements.screen.clientHeight < 1) {
      return;
    }
    const bubble = this.elements.speechBubble;
    const bubbleRect = bubble.getBoundingClientRect();
    const width = bubbleRect.width || 220;
    const height = bubbleRect.height || 120;
    const tip = this.getPointerTip();
    const desiredLeft = tip.x - BUBBLE_POINTER_LEFT_ANCHOR;
    const desiredTop = tip.y - height - BUBBLE_VERTICAL_GAP;
    const horizontalMax = Math.max(
      BUBBLE_SCREEN_PADDING,
      this.elements.screen.clientWidth - width - BUBBLE_SCREEN_PADDING
    );
    const verticalMax = Math.max(
      BUBBLE_SCREEN_PADDING,
      this.elements.screen.clientHeight - height - BUBBLE_SCREEN_PADDING
    );
    const left = Math.min(Math.max(BUBBLE_SCREEN_PADDING, desiredLeft), horizontalMax);
    const top = Math.min(Math.max(BUBBLE_SCREEN_PADDING, desiredTop), verticalMax);
    const pointerTargetX = left + BUBBLE_POINTER_LEFT_ANCHOR - POINTER_TIP_OFFSET.x;
    const pointerTargetY = top + height + BUBBLE_VERTICAL_GAP - POINTER_TIP_OFFSET.y;
    this.updatePointerTransform(pointerTargetX, pointerTargetY, 200);
    bubble.style.left = `${left}px`;
    bubble.style.top = `${top}px`;
    const arrowOffset = Math.min(
      Math.max(BUBBLE_POINTER_LEFT_ANCHOR, BUBBLE_ARROW_GUTTER),
      width - BUBBLE_ARROW_GUTTER
    );
    bubble.style.setProperty('--arrow-offset', `${arrowOffset}px`);
  }

  private normalizePathSegments(raw?: string) {
    if (!raw) {
      return [] as string[];
    }
    return raw
      .replace(/\\/g, '/')
      .split('/')
      .map((segment) => segment.trim())
      .filter(Boolean);
  }

  private composePath(parts: string[]) {
    return parts.filter(Boolean).join('/');
  }

  private normalizeEntryPath(path?: string | null) {
    return this.composePath(this.normalizePathSegments(path || ''));
  }

  private markPendingCreation(path?: string | null) {
    const normalized = this.normalizeEntryPath(path || '');
    if (normalized) {
      this.pendingCreateEntries.add(normalized);
      this.applyPendingCreationState(normalized, true);
      const segments = this.normalizePathSegments(normalized);
      if (segments.length === 1) {
        // 立即刷新桌面，移除占位避免闪现空位
        this.renderDesktopFolders();
      } else if (segments.length > 1) {
        const parentKey = this.composePath(segments.slice(0, -1));
        if (this.activeFolder === parentKey) {
          this.renderFolderEntries(parentKey, false);
        }
      }
    }
  }

  private releasePendingCreation(path?: string | null) {
    const normalized = this.normalizeEntryPath(path || '');
    if (!normalized) {
      return;
    }
    if (this.pendingCreateEntries.has(normalized)) {
      this.pendingCreateEntries.delete(normalized);
    }
    this.applyPendingCreationState(normalized, false);
    const segments = this.normalizePathSegments(normalized);
    if (segments.length === 1) {
      // 根目录图标可能尚未渲染，重新绘制桌面
      this.renderDesktopFolders();
    } else if (segments.length > 1) {
      const parentKey = this.composePath(segments.slice(0, -1));
      if (this.activeFolder === parentKey) {
        this.renderFolderEntries(parentKey, false);
      }
    }
  }

  private isCreationPending(path?: string | null) {
    const normalized = this.normalizeEntryPath(path || '');
    return normalized ? this.pendingCreateEntries.has(normalized) : false;
  }

  private applyPendingCreationState(path: string, active: boolean) {
    const segments = this.normalizePathSegments(path);
    const targetRoot = segments.length === 1 ? segments[0] : null;
    const entryEl = this.findFolderEntryElement(path);
    const desktopEl = targetRoot
      ? this.folderIcons.get(targetRoot) || this.fileIcons.get(targetRoot) || null
      : null;
    const apply = (el: HTMLElement | null) => {
      if (!el) {
        return;
      }
      if (active) {
        el.classList.remove('visible');
        el.classList.add('pending-reveal');
        el.style.opacity = '0';
      } else {
        el.classList.remove('pending-reveal');
        el.style.opacity = '1';
        requestAnimationFrame(() => el.classList.add('visible'));
      }
    };
    apply(entryEl);
    apply(desktopEl);
  }

  // 提前标记待创建路径，供外部（store enqueue 阶段）调用，避免动画首帧闪现
  preparePendingCreation(path?: string | null) {
    this.markPendingCreation(path);
  }

  private lockDesktopRender() {
    this.desktopRenderLocked = true;
    monitorLifecycleDebug('desktopRender:lock');
  }

  private unlockDesktopRender() {
    if (!this.desktopRenderLocked) {
      return;
    }
    this.desktopRenderLocked = false;
    monitorLifecycleDebug('desktopRender:unlock', {
      pending: this.pendingDesktopRoots?.length || 0
    });
    if (this.pendingDesktopRoots) {
      const pending = this.pendingDesktopRoots;
      this.pendingDesktopRoots = null;
      this.setDesktopRoots(pending, { immediate: true });
    }
  }

  private ensureFolderKey(key: string) {
    if (!key) {
      return;
    }
    if (!this.folderEntries.has(key)) {
      this.folderEntries.set(key, []);
    }
  }

  private refreshFolderIconStates() {
    // 需求简化：不区分开关状态，统一使用同一图标
    this.folderIcons.forEach((_icon, name) => {
      this.setFolderIconState(name);
    });
  }

  private async loadFolderEntries(folderKey: string) {
    const path = folderKey || '';
    try {
      const resp = await fetch(`/api/gui/files/entries?path=${encodeURIComponent(path)}`, {
        method: 'GET',
        credentials: 'include',
        headers: { Accept: 'application/json' }
      });
      const data = await resp.json().catch(() => null);
      if (!data?.success) {
        return;
      }
      const items = Array.isArray(data?.data?.items) ? data.data.items : [];
      const entries: FolderEntry[] = items.map((item: any) => {
        const rawPath = item?.path || this.composePath([path, item?.name].filter(Boolean));
        return {
          name: item?.name || '',
          path: this.normalizeEntryPath(rawPath),
          type: item?.type === 'directory' ? 'folder' : 'file'
        };
      });
      this.folderEntries.set(path, entries);
    } catch (error) {
      console.warn('[MonitorDirector] loadFolderEntries failed', path, error);
    }
  }

  private renderFolderEntries(folderKey: string, animate = true) {
    this.ensureFolderKey(folderKey);
    const entries = this.folderEntries.get(folderKey) || [];
    this.elements.folderBody.innerHTML = '';
    entries.forEach((entry) => {
      const icon = document.createElement('div');
      icon.className = 'folder-entry';
      icon.dataset.entryName = entry.name;
      icon.dataset.entryPath = entry.path;
      const img = document.createElement('img');
      img.src = entry.type === 'folder' ? this.assets.folderIcon : this.assets.fileIcon;
      img.alt = entry.name;
      const span = document.createElement('span');
      span.textContent = entry.name;
      icon.appendChild(img);
      icon.appendChild(span);
      this.elements.folderBody.appendChild(icon);
      const pending = this.isCreationPending(entry.path);
      if (pending) {
        icon.classList.add('pending-reveal');
        icon.style.opacity = '0';
      }
      if (!animate || pending) {
        if (!pending) {
          icon.classList.add('visible');
        }
        return;
      }
      requestAnimationFrame(() => {
        if (!pending) {
          icon.classList.add('visible');
        }
      });
    });
  }

  private upsertFolderEntry(
    folderKey: string,
    entry: { name: string; type: 'folder' | 'file' },
    options: { animate?: boolean } = {}
  ) {
    if (!folderKey) {
      return;
    }
    this.ensureFolderKey(folderKey);
    const path = this.composePath([folderKey, entry.name].filter(Boolean));
    const list = this.folderEntries.get(folderKey) || [];
    const idx = list.findIndex((item) => item.name === entry.name && item.type === entry.type);
    if (idx >= 0) {
      list[idx] = { ...entry, path };
    } else {
      list.push({ ...entry, path });
    }
    this.folderEntries.set(folderKey, list);
    if (this.activeFolder === folderKey) {
      this.renderFolderEntries(folderKey, options.animate !== false);
    }
  }

  private findFolderEntryElement(entryPath: string) {
    if (!entryPath) {
      return null;
    }
    return this.elements.folderBody.querySelector<HTMLElement>(`[data-entry-path="${entryPath}"]`);
  }

  private async doubleClickDesktopFolder(folderName: string) {
    let icon = this.folderIcons.get(folderName);
    if (!icon) {
      icon = this.spawnDesktopFolder(folderName);
    }
    if (!icon) {
      return;
    }
    this.ensureFolderKey(folderName);
    await this.movePointerToElement(icon, { duration: 720 });
    await this.click({ count: 2 });
    await sleep(180);
    await this.openFolder(folderName, folderName);
    await sleep(60);
  }

  private async doubleClickFolderEntry(entryPath: string) {
    const entryEl = this.findFolderEntryElement(entryPath);
    if (!entryEl) {
      return;
    }
    await this.movePointerToElement(entryEl, { duration: 620 });
    await this.click({ count: 2 });
    await sleep(200);
  }

  private async revealFileTarget(
    path: string,
    options: { doubleClick?: boolean; spawnDesktopFile?: boolean } = {}
  ) {
    const segments = this.normalizePathSegments(path);
    const filename = segments.pop() || path;
    if (!filename) {
      return null;
    }
    if (segments.length) {
      const parentKey = await this.openFolderChain(segments);
      if (!parentKey) {
        return null;
      }
      this.upsertFolderEntry(parentKey, { name: filename, type: 'file' }, { animate: false });
      await this.openFolder(parentKey, parentKey);
      await sleep(40);
      const entryPath = this.composePath([parentKey, filename]);
      const entryEl = this.findFolderEntryElement(entryPath);
      if (!entryEl) {
        return null;
      }
      await this.movePointerToElement(entryEl, { duration: 620 });
      if (options.doubleClick) {
        await this.click({ count: 2 });
        await sleep(200);
      }
      return { element: entryEl, entryPath, parentKey };
    }
    let icon = this.fileIcons.get(filename);
    if (!icon && options.spawnDesktopFile) {
      icon = this.spawnDesktopFile(filename);
    }
    if (!icon) {
      return null;
    }
    await this.movePointerToElement(icon, { duration: 620 });
    if (options.doubleClick) {
      await this.click({ count: 2 });
      await sleep(200);
    }
    return { element: icon, entryPath: filename, parentKey: null };
  }

  private async openFileMenuAction(entryEl: HTMLElement | null, action: 'read' | 'edit') {
    if (!entryEl) {
      return false;
    }
    await this.movePointerToElement(entryEl, { duration: 540 });
    await this.click();
    this.showContextMenu('file');
    await sleep(160);
    const highlighted = await this.highlightMenu('file', action);
    if (highlighted) {
      await this.click();
    }
    this.hideContextMenus();
    return highlighted;
  }

  private async openFolderChain(segments: string[]) {
    if (!segments.length) {
      return null;
    }
    const targetSegments = segments.slice();
    let currentKey: string | null = null;
    let startIndex = 0;
    const folderWindowVisible = this.isWindowVisible(this.elements.folderWindow);
    const activeSegments = this.activeFolder ? this.normalizePathSegments(this.activeFolder) : [];
    const hasActiveMatch =
      activeSegments.length &&
      activeSegments.length <= targetSegments.length &&
      activeSegments.every((seg, idx) => seg === targetSegments[idx]);

    // 若文件夹窗口未开，或当前活跃文件夹不在目标路径前缀，强制从根目录开始双击打开，保证有明显“打开文件夹”动画
    if (!folderWindowVisible || !hasActiveMatch) {
      const root = targetSegments[0];
      await this.doubleClickDesktopFolder(root);
      currentKey = root;
      startIndex = 1;
    } else {
      currentKey = this.composePath(activeSegments);
      startIndex = activeSegments.length;
      await this.openFolder(currentKey, currentKey);
      await sleep(40);
    }

    for (let i = startIndex; i < targetSegments.length; i += 1) {
      const segment = targetSegments[i];
      const nextKey = this.composePath([currentKey, segment]);
      this.upsertFolderEntry(currentKey, { name: segment, type: 'folder' }, { animate: false });
      await this.openFolder(currentKey, currentKey);
      await sleep(40);
      await this.doubleClickFolderEntry(nextKey);
      currentKey = nextKey;
      await this.openFolder(currentKey, currentKey);
      await sleep(60);
    }
    return currentKey;
  }

  private populateDesktop() {
    this.appIcons.clear();
    this.elements.appsGrid.innerHTML = '';
    DESKTOP_APPS.forEach((app) => {
      const div = document.createElement('div');
      div.className = 'desktop-icon app';
      div.dataset.appId = app.id;
      const img = document.createElement('img');
      img.src = this.assets.apps[app.assetKey] || this.assets.apps.browser;
      img.alt = t(app.labelKey);
      div.appendChild(img);
      const span = document.createElement('span');
      span.textContent = t(app.labelKey);
      div.appendChild(span);
      this.elements.appsGrid.appendChild(div);
      this.appIcons.set(app.id, div);
    });
    this.renderDesktopFolders();
  }

  private renderDesktopFolders() {
    const grid = this.elements.desktopGrid;
    if (!grid) {
      return;
    }
    const desiredRoots = this.desktopRoots.filter(
      (folder) => !this.pendingDesktopFolders.has(folder) && !this.isCreationPending(folder)
    );
    const knownRoots = new Set(this.desktopRoots);

    // 清理已不存在的图标
    Array.from(this.folderIcons.entries()).forEach(([name, icon]) => {
      if (!knownRoots.has(name) || this.isCreationPending(name)) {
        if (icon.parentElement === grid) {
          grid.removeChild(icon);
        }
        this.folderIcons.delete(name);
      }
    });

    // 更新/创建所有根目录图标，pending 项先不挂载到 DOM
    this.desktopRoots.forEach((folder) => {
      let icon = this.folderIcons.get(folder) || null;
      if (!icon) {
        icon = this.createDesktopFolderIcon(folder);
        this.folderIcons.set(folder, icon);
      }
      if (this.pendingDesktopFolders.has(folder) || this.isCreationPending(folder)) {
        icon.classList.remove('visible');
        icon.classList.add('pending-reveal');
        if (icon.parentElement === grid) {
          grid.removeChild(icon);
        }
      } else {
        icon.classList.remove('pending-reveal');
      }
    });

    const currentOrder = Array.from(grid.querySelectorAll<HTMLElement>('[data-folder-name]')).map(
      (node) => node.dataset.folderName || ''
    );
    let requiresReorder = desiredRoots.length !== currentOrder.length;
    if (!requiresReorder) {
      for (let i = 0; i < desiredRoots.length; i += 1) {
        if (desiredRoots[i] !== currentOrder[i]) {
          requiresReorder = true;
          break;
        }
      }
    }
    if (requiresReorder) {
      const folderNodes = Array.from(grid.querySelectorAll<HTMLElement>('[data-folder-name]'));
      folderNodes.forEach((node) => node.remove());
      const firstNonFolderSibling =
        Array.from(grid.children).find((child) => {
          if (!(child instanceof HTMLElement)) {
            return false;
          }
          return !child.dataset.folderName;
        }) || null;
      let lastInserted: HTMLElement | null = null;
      desiredRoots.forEach((folder) => {
        const icon = this.folderIcons.get(folder);
        if (!icon) {
          return;
        }
        const referenceNode = lastInserted ? lastInserted.nextSibling : firstNonFolderSibling;
        if (referenceNode) {
          grid.insertBefore(icon, referenceNode);
        } else {
          grid.appendChild(icon);
        }
        if (!icon.classList.contains('visible') && !this.isCreationPending(folder)) {
          requestAnimationFrame(() => icon.classList.add('visible'));
        }
        lastInserted = icon;
      });
    }
  }

  private prepareExtractionTemplate() {
    const templateElement = this.elements.extractionWindow;
    if (!templateElement || this.extractionTemplate) {
      return;
    }
    templateElement.classList.add('extraction-template');
    templateElement.style.display = 'none';
    templateElement.classList.remove('visible', 'closing');
    this.extractionTemplate = {
      id: 'template',
      element: templateElement,
      urlEl: this.elements.extractionUrl,
      stateEl: this.elements.extractionState,
      summaryEl: this.elements.extractionSummary,
      titleEl: templateElement.querySelector('.window-title')
    };
    this.windowAnchors.delete(templateElement);
  }

  private createExtractionWindowDom(): ExtractionWindowInstance {
    let element: HTMLElement;
    if (this.extractionTemplate) {
      element = this.extractionTemplate.element.cloneNode(true) as HTMLElement;
    } else {
      element = this.buildExtractionWindowDom();
    }
    element.classList.remove('extraction-template');
    element.style.display = '';
    element.classList.remove('visible', 'closing');
    const body = element.querySelector('.extraction-body') || element;
    let urlEl = element.querySelector<HTMLElement>('.extract-url');
    if (!urlEl) {
      urlEl = document.createElement('div');
      urlEl.className = 'extract-url';
      body.appendChild(urlEl);
    }
    let stateEl = element.querySelector<HTMLElement>('.extract-status');
    if (!stateEl) {
      stateEl = document.createElement('div');
      stateEl.className = 'extract-status';
      body.appendChild(stateEl);
    }
    let summaryEl = element.querySelector<HTMLElement>('.extract-summary');
    if (!summaryEl) {
      summaryEl = document.createElement('div');
      summaryEl.className = 'extract-summary';
      body.appendChild(summaryEl);
    }
    const titleEl = element.querySelector<HTMLElement>('.window-title');
    return {
      id: '',
      element,
      urlEl,
      stateEl,
      summaryEl,
      titleEl
    };
  }

  private buildExtractionWindowDom() {
    const windowEl = document.createElement('div');
    windowEl.className = 'window extraction-window';
    const header = document.createElement('div');
    header.className = 'window-header';
    ['red', 'yellow', 'green'].forEach((color) => {
      const dot = document.createElement('span');
      dot.className = `traffic-dot ${color}`;
      header.appendChild(dot);
    });
    const title = document.createElement('span');
    title.className = 'window-title';
    title.textContent = t('monitor.extractTitle');
    header.appendChild(title);
    windowEl.appendChild(header);
    const body = document.createElement('div');
    body.className = 'extraction-body';
    const url = document.createElement('div');
    url.className = 'extract-url';
    body.appendChild(url);
    const status = document.createElement('div');
    status.className = 'extract-status';
    status.textContent = t('monitor.extractWaiting');
    body.appendChild(status);
    const summary = document.createElement('div');
    summary.className = 'extract-summary';
    body.appendChild(summary);
    windowEl.appendChild(body);
    return windowEl;
  }

  private ensureExtractionWindowInstance(id: string, meta?: { title?: string; url?: string }) {
    const key = id || this.nextExtractionId();
    if (this.extractionWindows.has(key)) {
      return this.extractionWindows.get(key)!;
    }
    const instance = this.createExtractionWindowDom();
    instance.id = key;
    instance.element.dataset.extractId = key;
    this.elements.screen.appendChild(instance.element);
    const anchor = this.computeExtractionAnchor(this.extractionWindows.size);
    this.windowAnchors.set(instance.element, anchor);
    this.positionWindow(instance.element, anchor);
    this.extractionWindows.set(key, instance);
    const displayIndex = this.extractionWindows.size;
    if (instance.titleEl) {
      const baseTitle = meta?.title && meta.title.trim().length ? meta.title.trim() : t('monitor.extractTitle');
      instance.titleEl.textContent = displayIndex > 1 ? `${baseTitle} #${displayIndex}` : baseTitle;
    }
    if (meta?.url) {
      instance.element.dataset.extractUrl = meta.url;
    }
    return instance;
  }

  private computeExtractionAnchor(index: number) {
    const columns = 2;
    const column = Math.floor(index / columns);
    const row = index % columns;
    const columnSpacing = 0.2;
    const rowSpacing = 0.3;
    const x = Math.min(0.92, this.extractionAnchor.x + column * columnSpacing);
    const y = Math.min(0.9, this.extractionAnchor.y + row * rowSpacing);
    return { x, y };
  }

  private clearExtractionWindows() {
    this.extractionWindows.forEach((instance) => {
      this.closeWindow(instance.element, { animate: false });
      this.windowAnchors.delete(instance.element);
      this.manualPositions.delete(instance.element);
      instance.element.remove();
    });
    this.extractionWindows.clear();
  }

  private clearTerminalSessions() {
    this.terminalSessions.clear();
    this.terminalSessionTitleMap.clear();
    this.terminalSessionNames.clear();
    this.terminalHistories.clear();
    this.activeTerminalSessionId = null;
    this.lastTerminalSessionId = null;
    this.terminalContextSessionId = null;
    this.terminalLastFocusedAt = 0;
    if (this.elements.terminalBody) {
      this.elements.terminalBody.innerHTML = '';
    }
    this.renderTerminalTabs();
  }

  private purgeExtractionWindowByElement(el: HTMLElement) {
    for (const [key, instance] of this.extractionWindows.entries()) {
      if (instance.element === el) {
        this.extractionWindows.delete(key);
        this.windowAnchors.delete(instance.element);
        this.manualPositions.delete(instance.element);
        setTimeout(() => {
          instance.element.remove();
        }, 340);
        break;
      }
    }
  }

  private generateNewSessionId() {
    return `session-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  }

  private isTerminalWindowOnTop(instance: TerminalShell | null) {
    if (!instance?.element) {
      return false;
    }
    const top = this.windowOrder[this.windowOrder.length - 1] || null;
    return top === instance.element;
  }

  private resetCommandWindow(title = t('monitor.commandTitle'), options: { clearOutput?: boolean } = {}) {
    if (this.elements.commandTitle) {
      this.elements.commandTitle.textContent = title;
    }
    if (this.elements.commandInput) {
      this.elements.commandInput.textContent = '';
    }
    if (options.clearOutput && this.elements.commandOutput) {
      this.elements.commandOutput.innerHTML = '';
    }
    this.commandCurrentText = '';
  }

  private async revealCommandWindow(
    title = t('monitor.commandTitle'),
    options: { reset?: boolean; focusInput?: boolean } = {}
  ) {
    const { reset = true, focusInput = false } = options;
    const visible = this.isWindowVisible(this.elements.commandWindow);
    if (!visible) {
      await this.movePointerToApp('command');
      await this.click();
    }
    if (reset) {
      this.resetCommandWindow(title, { clearOutput: true });
    } else {
      if (this.elements.commandTitle) {
        this.elements.commandTitle.textContent = title;
      }
    }
    this.showWindow(this.elements.commandWindow);
    if (focusInput) {
      await this.focusCommandInput();
    }
  }

  private async focusCommandInput() {
    if (!this.elements.commandInput) {
      return;
    }
    await this.movePointerToElement(this.elements.commandInput, { duration: 360 });
    await this.click();
  }

  private async typeCommandText(text: string) {
    if (!this.elements.commandInput) {
      return;
    }
    const target = this.elements.commandInput;
    await this.focusCommandInput();
    const toDelete = this.commandCurrentText;
    if (toDelete) {
      const len = toDelete.length;
      const boost = 1 + Math.min(len / 40, 6); // 行越长删除越快
      const interval = Math.max(6, Math.floor(18 / boost));
      for (let i = len; i > 0; i -= 1) {
        target.textContent = toDelete.slice(0, i - 1);
        await sleep(interval);
      }
      this.commandCurrentText = '';
    }
    await this.typeSmartText(target, text);
    this.commandCurrentText = text;
  }

  private appendCommandOutput(lines: string[]) {
    if (!this.elements.commandOutput) {
      return;
    }
    const frag = document.createDocumentFragment();
    lines.forEach((line) => {
      const row = document.createElement('div');
      row.className = 'command-line';
      row.textContent = line;
      frag.appendChild(row);
    });
    this.elements.commandOutput.appendChild(frag);
    this.elements.commandOutput.scrollTop = this.elements.commandOutput.scrollHeight;
  }

  private resetPythonWindow(title = 'Python') {
    if (this.elements.pythonTitle) {
      this.elements.pythonTitle.textContent = title;
    }
    if (this.elements.pythonInput) {
      this.elements.pythonInput.textContent = '';
    }
    if (this.elements.pythonOutput) {
      this.elements.pythonOutput.innerHTML = '';
    }
  }

  private appendPythonOutput(label: string, content: string) {
    if (!this.elements.pythonOutput) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'python-block output';
    const title = document.createElement('div');
    title.className = 'python-block-title';
    title.textContent = label;
    const pre = document.createElement('pre');
    pre.textContent = content;
    wrapper.appendChild(title);
    wrapper.appendChild(pre);
    this.elements.pythonOutput.innerHTML = '';
    this.elements.pythonOutput.appendChild(wrapper);
    this.scrollPythonToResult();
  }

  private async revealPythonWindow(title = 'Python') {
    await this.movePointerToApp('python');
    await this.click();
    this.resetPythonWindow(title);
    this.showWindow(this.elements.pythonWindow);
  }

  private async focusPythonInput() {
    if (!this.elements.pythonInput) return;
    await this.movePointerToElement(this.elements.pythonInput, { duration: 360 });
    await this.click();
  }

  private async typePythonCode(
    code: string,
    options: { deletePrevious?: boolean; animate?: boolean } = {}
  ) {
    if (!this.elements.pythonInput) return;
    const { deletePrevious = true, animate = true } = options;
    const target = this.elements.pythonInput;
    if (deletePrevious) {
      target.textContent = '';
    }
    await this.focusPythonInput();
    if (!animate) {
      target.textContent = code;
      return;
    }
    await this.typeSmartText(target, code);
  }

  /**
   * 根据长度自动选择“逐字符”或“逐行”动画的输入方式。
   * 过长内容按行填充，避免动画过慢。
   */
  private async typeSmartText(target: HTMLElement, text: string, forceLineMode = false) {
    const lines = text.split('\n');
    const long = forceLineMode || lines.length > 2 || text.length > 120;
    const lineDelay = 32;
    if (long) {
      target.textContent = '';
      for (let i = 0; i < lines.length; i += 1) {
        target.textContent += (i > 0 ? '\n' : '') + lines[i];
        await sleep(Math.max(12, lineDelay - Math.min(i, 6) * 3));
      }
      return;
    }
    const chars = Array.from(text);
    for (const ch of chars) {
      target.textContent = (target.textContent || '') + ch;
      await sleep(18);
    }
  }

  private scrollPythonToTop() {
    if (this.elements.pythonBody) {
      this.elements.pythonBody.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  private scrollPythonToResult() {
    if (this.elements.pythonBody && this.elements.pythonOutput) {
      const top = this.elements.pythonOutput.offsetTop - this.elements.pythonBody.offsetTop;
      this.elements.pythonBody.scrollTo({ top, behavior: 'smooth' });
    }
  }

  private async ensureMemoryWindowVisible(
    options: { initialEntries?: string[]; snapshotProvided?: boolean; memoryType?: string } = {}
  ) {
    const { initialEntries, snapshotProvided = false, memoryType = 'main' } = options;
    const hydrate = async () => {
      if (snapshotProvided) {
        this.renderMemoryEntries(initialEntries || []);
        return;
      }
      if (!this.getMemoryItems().length) {
        await this.loadMemoryEntries(memoryType);
      }
    };
    if (this.isWindowVisible(this.elements.memoryWindow)) {
      this.showWindow(this.elements.memoryWindow);
      if (!this.getMemoryItems().length || snapshotProvided) {
        await hydrate();
      }
      return;
    }
    await this.movePointerToApp('memory');
    await this.click();
    this.showWindow(this.elements.memoryWindow);
    await hydrate();
  }

  private getMemoryItems(): HTMLElement[] {
    if (!this.elements.memoryList) return [];
    return Array.from(this.elements.memoryList.children) as HTMLElement[];
  }

  private extractMemorySnapshotEntries(
    payload: any,
    stage: 'before' | 'after' = 'before'
  ): { entries: string[]; provided: boolean } {
    const key = stage === 'after' ? 'monitor_snapshot_after' : 'monitor_snapshot';
    const snapshot = payload?.[key];
    const entries = snapshot?.entries;
    if (Array.isArray(entries)) {
      return { entries: entries.map((entry) => String(entry ?? '')), provided: true };
    }
    return { entries: [], provided: !!snapshot };
  }

  private getMemoryItemByIndex(index: number): HTMLElement | null {
    const items = this.getMemoryItems();
    if (!index || index < 1 || index > items.length) {
      return null;
    }
    return items[index - 1];
  }

  private async waitForScrollSettled(el: HTMLElement, targetTop: number, timeout = 600) {
    const started = Date.now();
    return new Promise<void>((resolve) => {
      const tick = () => {
        const arrived = Math.abs(el.scrollTop - targetTop) < 2;
        const expired = Date.now() - started > timeout;
        if (arrived || expired) {
          resolve();
          return;
        }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  private async scrollMemoryToBottom(waitMs = 200) {
    const body = this.elements.memoryList;
    if (!body) return;
    const target = body.scrollHeight;
    body.scrollTo({ top: target, behavior: 'smooth' });
    await this.waitForScrollSettled(body, target);
    this.latestMemoryScroll = Date.now();
    if (waitMs > 0) {
      await sleep(waitMs);
    }
  }

  private async scrollMemoryItemIntoView(card: HTMLElement | null, waitMs = 200) {
    if (!card) return;
    const body = this.elements.memoryList;
    if (!body) return;
    const targetTop = card.offsetTop - body.clientHeight * 0.35;
    const clamped = Math.max(0, targetTop);
    body.scrollTo({ top: clamped, behavior: 'smooth' });
    await this.waitForScrollSettled(body, clamped);
    this.latestMemoryScroll = Date.now();
    if (waitMs > 0) {
      await sleep(waitMs);
    }
  }

  private ensureMemoryTypingVisible(card: HTMLElement | null) {
    if (!card) return;
    const body = this.elements.memoryList;
    if (!body) return;
    const bodyTop = body.scrollTop;
    const bodyBottom = bodyTop + body.clientHeight;
    const cardTop = card.offsetTop;
    const cardBottom = cardTop + card.offsetHeight;
    // 如果底部被遮挡，向下滚动到露出多 12px 缓冲
    if (cardBottom > bodyBottom - 4) {
      const delta = cardBottom - bodyBottom + 12;
      body.scrollTop = bodyTop + delta;
      this.latestMemoryScroll = Date.now();
    } else if (cardTop < bodyTop) {
      // 若顶部超出，滚回顶部
      body.scrollTop = Math.max(0, cardTop - 8);
      this.latestMemoryScroll = Date.now();
    }
  }

  private highlightMemoryCard(card: HTMLElement, active = true) {
    if (!card) return;
    card.classList.toggle('editing', !!active);
  }

  private updateMemoryMeta() {
    if (!this.elements.memoryList) return;
    const count = this.elements.memoryList.children.length;
    if (this.elements.memoryCount) {
      this.elements.memoryCount.textContent = String(count);
    }
    if (this.elements.memoryStatus) {
      this.elements.memoryStatus.textContent = t('monitor.memorySynced');
    }
    if (this.elements.memoryTime) {
      this.elements.memoryTime.textContent = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
      });
    }
  }

  private createMemoryCard(text: string) {
    const item = document.createElement('div');
    item.className = 'memory-item new';
    const body = document.createElement('div');
    body.className = 'memory-text';
    body.textContent = text;
    item.appendChild(body);
    return item;
  }

  private async typeIntoMemoryCard(
    card: HTMLElement,
    text: string,
    options: { clearFirst?: boolean } = {}
  ) {
    if (!card) return;
    const body = card.querySelector('.memory-text') as HTMLElement | null;
    if (!body) return;
    if (options.clearFirst) {
      const existing = body.textContent || '';
      for (let i = existing.length; i > 0; i--) {
        body.textContent = existing.slice(0, i - 1);
        await sleep(18);
      }
    }
    const chars = Array.from(text);
    let idx = 0;
    for (const ch of chars) {
      idx += 1;
      body.textContent = (body.textContent || '') + ch;
      if (idx % 4 === 0) {
        this.ensureMemoryTypingVisible(card);
      }
      await sleep(18);
    }
    // 最终再校准一次，确保结尾可见
    this.ensureMemoryTypingVisible(card);
  }

  private async animateMemoryAppend(text: string) {
    if (!this.elements.memoryList) return;
    await this.scrollMemoryToBottom();
    const card = this.createMemoryCard('');
    this.elements.memoryList.appendChild(card);
    requestAnimationFrame(() => card.classList.add('visible'));
    await sleep(160);
    await this.scrollMemoryToBottom();
    await this.movePointerToElement(card, { duration: 420 });
    await this.click();
    this.highlightMemoryCard(card, true);
    await this.typeIntoMemoryCard(card, text, { clearFirst: true });
    // 若内容超出单行，确保滚动至卡片底部
    await this.scrollMemoryItemIntoView(card);
    this.highlightMemoryCard(card, false);
  }

  private async animateMemoryReplace(index: number, text: string) {
    const card = this.getMemoryItemByIndex(index);
    if (!card) {
      await this.animateMemoryAppend(text);
      return;
    }
    await this.scrollMemoryItemIntoView(card);
    await this.movePointerToElement(card, { duration: 420 });
    await this.click();
    this.highlightMemoryCard(card, true);
    await this.typeIntoMemoryCard(card, text, { clearFirst: true });
    await this.scrollMemoryItemIntoView(card);
    this.highlightMemoryCard(card, false);
  }

  private async animateMemoryDelete(index: number) {
    const card = this.getMemoryItemByIndex(index);
    if (!card || !this.elements.memoryList) {
      return;
    }
    await this.scrollMemoryItemIntoView(card);
    await this.movePointerToElement(card, { duration: 360 });
    await this.click();
    this.highlightMemoryCard(card, true);
    card.classList.add('swipe-out');
    await this.movePointerToElement(card, { offsetX: card.clientWidth * 0.45, duration: 240 });
    await sleep(200);
    card.remove();
    this.highlightMemoryCard(card, false);
  }

  private async loadMemoryEntries(memoryType = 'main') {
    try {
      const resp = await fetch(`/api/memory?type=${encodeURIComponent(memoryType)}`);
      const data = await resp.json();
      if (!data?.success || !Array.isArray(data.entries)) {
        return;
      }
      this.renderMemoryEntries(data.entries);
    } catch (error) {
      console.warn('loadMemoryEntries failed', error);
    }
  }

  private renderMemoryEntries(entries: string[]) {
    if (!this.elements.memoryList) return;
    this.elements.memoryList.innerHTML = '';
    entries.forEach((text) => {
      const card = this.createMemoryCard(text);
      card.classList.add('visible');
      this.elements.memoryList.appendChild(card);
    });
    this.updateMemoryMeta();
  }

  private async revealTerminalWindow(instance: TerminalShell) {
    await this.movePointerToApp('terminal');
    await this.click();
    this.showWindow(instance.element);
    if (instance.titleEl) {
      instance.titleEl.textContent = t('monitor.terminalTitle');
    }
    this.terminalLastFocusedAt = Date.now();
  }

  private async focusTerminalHeader(instance: TerminalShell, options: { force?: boolean } = {}) {
    if (!instance?.element) {
      return;
    }
    const header = instance.element.querySelector('.window-header') as HTMLElement | null;
    const needFocus = options.force || !this.isTerminalWindowOnTop(instance);
    if (header && needFocus) {
      await this.movePointerToElement(header, { duration: 360 });
      await this.click();
      this.terminalLastFocusedAt = Date.now();
      return;
    }
    // 窗口本就在最前，保持层级即可
    this.raiseWindowForTarget(instance.element);
  }

  private async focusTerminalPrompt(sessionId: string, instance: TerminalShell | null) {
    this.ensurePromptLine(sessionId);
    const promptEl = (instance?.bodyEl?.lastElementChild as HTMLElement | null) || null;
    if (promptEl) {
      await this.movePointerToElement(promptEl, { duration: 520, offsetX: -6 });
      await this.click();
      this.terminalLastFocusedAt = Date.now();
    }
    return promptEl;
  }

  private getTerminalHistory(sessionId: string): TerminalLine[] {
    if (!this.terminalHistories.has(sessionId)) {
      this.terminalHistories.set(sessionId, []);
    }
    return this.terminalHistories.get(sessionId)!;
  }

  private renderTerminalHistory(sessionId: string) {
    const history = this.getTerminalHistory(sessionId);
    const { bodyEl } = this.getTerminalInstance();
    bodyEl.innerHTML = '';
    history.forEach((line) => {
      const pre = document.createElement('pre');
      pre.textContent = line.text;
      if (line.role === 'prompt') {
        pre.className = 'session-terminal-prompt-line';
      } else if (line.role === 'note') {
        pre.className = 'session-terminal-note-line';
      }
      bodyEl.appendChild(pre);
    });
    bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  private ensurePromptLine(sessionId: string) {
    const history = this.getTerminalHistory(sessionId);
    if (!history.length || history[history.length - 1].role !== 'prompt') {
      history.push({ text: '➜ ', role: 'prompt' });
    }
    this.renderTerminalHistory(sessionId);
  }

  private updatePromptText(sessionId: string, text: string) {
    const history = this.getTerminalHistory(sessionId);
    if (!history.length || history[history.length - 1].role !== 'prompt') {
      history.push({ text: text, role: 'prompt' });
    } else {
      history[history.length - 1].text = text;
    }
    this.renderTerminalHistory(sessionId);
  }

  private appendTerminalOutputs(sessionId: string, command: string, outputs: string[]) {
    const history = this.getTerminalHistory(sessionId);
    // 确保上一条提示行记录了命令
    if (!history.length || history[history.length - 1].role !== 'prompt') {
      history.push({ text: `➜ ${command}`.trimEnd(), role: 'prompt' });
    } else {
      history[history.length - 1].text = `➜ ${command}`.trimEnd();
    }
    outputs.forEach((line) => history.push({ text: line, role: 'output' }));
    history.push({ text: '➜ ', role: 'prompt' });
    this.renderTerminalHistory(sessionId);
  }

  private appendTerminalNote(sessionId: string, text: string) {
    const history = this.getTerminalHistory(sessionId);
    history.push({ text, role: 'note' });
    this.renderTerminalHistory(sessionId);
  }

  private closeTerminalSession(sessionId: string) {
    this.terminalSessions.delete(sessionId);
    for (const [title, mappedId] of this.terminalSessionTitleMap.entries()) {
      if (mappedId === sessionId) {
        this.terminalSessionTitleMap.delete(title);
      }
    }
    this.terminalSessionNames.delete(sessionId);
    this.terminalHistories.delete(sessionId);
    if (this.activeTerminalSessionId === sessionId) {
      this.activeTerminalSessionId = null;
    }
    if (this.lastTerminalSessionId === sessionId) {
      this.lastTerminalSessionId = null;
    }
    if (this.terminalContextSessionId === sessionId) {
      this.terminalContextSessionId = null;
    }
    const next = this.terminalSessions.size ? Array.from(this.terminalSessions.keys())[0] : null;
    if (next) {
      this.activateSession(next);
    } else {
      this.renderTerminalTabs();
      const { bodyEl } = this.getTerminalInstance();
      bodyEl.innerHTML = '';
      const hint = document.createElement('pre');
      hint.textContent = t('monitor.terminalEmptyHint');
      hint.className = 'session-terminal-note-line';
      bodyEl.appendChild(hint);
    }
  }

  private async typeSessionCommand(sessionId: string, command: string) {
    this.ensurePromptLine(sessionId);
    const history = this.getTerminalHistory(sessionId);
    const prompt = history[history.length - 1];
    prompt.text = '➜ ';
    this.renderTerminalHistory(sessionId);
    const { bodyEl } = this.getTerminalInstance();
    const promptEl = bodyEl.lastElementChild as HTMLElement | null;
    const long = command.length > 80 || command.split('\n').length > 1;
    if (long) {
      prompt.text = `➜ ${command}`;
      if (promptEl) {
        promptEl.textContent = prompt.text;
        this.scrollPromptIntoView(this.getTerminalInstance());
      }
      await sleep(50);
      return;
    }
    let charIndex = 0;
    const chars = command.split('');
    for (const ch of chars) {
      charIndex += 1;
      prompt.text += ch;
      if (promptEl) {
        promptEl.textContent = prompt.text;
      }
      if (promptEl && (charIndex % 6 === 0 || charIndex === chars.length)) {
        this.scrollPromptIntoView(this.getTerminalInstance());
      }
      await sleep(32);
    }
  }

  private getTerminalInstance() {
    return {
      element: this.elements.terminalWindow,
      bodyEl: this.elements.terminalBody,
      titleEl: this.elements.terminalHeaderText
    };
  }

  private nextTerminalName() {
    const existing = Array.from(this.terminalSessions.values()).map((s) => s.name);
    let idx = existing.length + 1;
    let candidate = t('monitor.terminalName', { n: idx });
    while (existing.includes(candidate)) {
      idx += 1;
      candidate = t('monitor.terminalName', { n: idx });
    }
    return candidate;
  }

  private ensureSessionRecord(sessionId: string, name?: string, rawName?: string) {
    const baseName =
      (name || rawName || this.terminalSessionNames.get(sessionId) || '').trim() ||
      this.nextTerminalName();
    this.terminalSessions.set(sessionId, {
      id: sessionId,
      name: baseName,
      rawName: rawName || name
    });
    this.terminalSessionNames.set(sessionId, baseName);
    this.terminalSessionTitleMap.set(baseName, sessionId);
    if (rawName) {
      this.terminalRawNameMap.set(rawName, sessionId);
    }
    if (!this.terminalHistories.has(sessionId)) {
      this.terminalHistories.set(sessionId, [{ text: '➜ ', role: 'prompt' }]);
    }
    this.renderTerminalTabs();
    return this.terminalSessions.get(sessionId)!;
  }

  private createSession(name?: string, rawName?: string) {
    const sessionId = this.generateNewSessionId();
    this.ensureSessionRecord(sessionId, (name || '').trim(), rawName);
    return sessionId;
  }

  private getTabElement(sessionId: string) {
    return this.elements.terminalTabList.querySelector<HTMLElement>(
      `[data-session-id="${sessionId}"]`
    );
  }

  private renderTerminalTabs() {
    if (!this.elements.terminalTabList || !this.elements.terminalAddButton) {
      return;
    }
    this.elements.terminalTabList.innerHTML = '';
    const tabs: Array<{ id: string; name: string }> = Array.from(
      this.terminalSessions.values()
    ).map((s) => ({
      id: s.id,
      name: s.name
    }));
    if (!tabs.length) {
      this.elements.terminalAddButton.classList.add('lonely');
      this.elements.terminalTabs.appendChild(this.elements.terminalAddButton);
      return;
    }
    this.elements.terminalAddButton.classList.remove('lonely');
    tabs.forEach((session) => {
      const btn = document.createElement('button');
      btn.className = 'terminal-tab';
      btn.dataset.sessionId = session.id;
      btn.textContent = session.name;
      if (this.activeTerminalSessionId && session.id === this.activeTerminalSessionId) {
        btn.classList.add('active');
      }
      this.elements.terminalTabList.appendChild(btn);
    });
    // 确保 + 按钮始终在最右
    this.elements.terminalTabs.appendChild(this.elements.terminalAddButton);
  }

  private activateSession(sessionId: string) {
    const record = this.terminalSessions.get(sessionId) || this.ensureSessionRecord(sessionId);
    const { element, bodyEl, titleEl } = this.getTerminalInstance();
    if (titleEl) {
      titleEl.textContent = t('monitor.terminalTitle');
    }
    this.activeTerminalSessionId = sessionId;
    this.lastTerminalSessionId = sessionId;
    this.renderTerminalTabs();
    this.renderTerminalHistory(sessionId);
    this.showWindow(element);
    bodyEl.scrollTop = bodyEl.scrollHeight;
    this.terminalLastFocusedAt = Date.now();
    return record;
  }

  private async ensureTerminalSessionReady(
    payload: any,
    options: {
      focusPrompt?: boolean;
      forceNew?: boolean;
      createIfMissing?: boolean;
      activate?: boolean;
    } = {}
  ) {
    const {
      focusPrompt = false,
      createIfMissing = true,
      forceNew = false,
      activate = true
    } = options;
    const requestedNew =
      payload?.arguments?.create_new === true ||
      payload?.arguments?.new_session === true ||
      payload?.arguments?.force_new === true ||
      payload?.arguments?.new === true;
    const nameHint =
      payload?.arguments?.session_name ||
      payload?.arguments?.session ||
      payload?.arguments?.name ||
      payload?.arguments?.title ||
      payload?.result?.session ||
      payload?.result?.name ||
      payload?.title ||
      null;
    const meta = this.resolveSessionMeta(payload, {
      persist: true,
      createIfMissing,
      forceNewIfAbsent: forceNew || requestedNew,
      preferExisting: !(forceNew || requestedNew || !!nameHint)
    });
    let sessionId = meta.sessionId;
    const needsNew =
      forceNew || requestedNew || !sessionId || !this.terminalSessions.has(sessionId);

    const shell = this.getTerminalInstance();
    const wasVisible = this.isWindowVisible(shell.element);
    if (!wasVisible) {
      await this.revealTerminalWindow(shell);
    } else {
      this.showWindow(shell.element);
    }

    let created = false;
    if (needsNew && this.elements.terminalAddButton) {
      await this.movePointerToElement(this.elements.terminalAddButton, { duration: 520 });
      await this.click();
      sessionId = this.createSession(meta.title, meta.rawName || undefined);
      created = true;
    } else if (sessionId) {
      // 已存在会话，仅保证名称映射，不改名
      this.ensureSessionRecord(sessionId, meta.title, meta.rawName || undefined);
    }

    if (!sessionId && this.terminalSessions.size) {
      sessionId = Array.from(this.terminalSessions.keys())[0];
    }
    if (!sessionId) {
      sessionId = this.createSession(meta.title, meta.rawName || undefined);
      created = true;
    }

    // 仅在需要激活时才点击标签/切换显示；否则保持当前显示不变
    if (activate && (created || this.activeTerminalSessionId !== sessionId)) {
      const tabEl = this.getTabElement(sessionId);
      if (tabEl) {
        await this.movePointerToElement(tabEl, { duration: 420 });
        await this.click();
      }
    }

    let promptEl: HTMLElement | null = null;
    if (activate) {
      this.activateSession(sessionId);
      promptEl = this.elements.terminalBody.lastElementChild as HTMLElement | null;
      if (focusPrompt && promptEl) {
        await this.movePointerToElement(promptEl, { duration: 520, offsetX: -6 });
        await this.click();
      }
    } else {
      this.renderTerminalTabs();
    }
    return {
      sessionId,
      title: meta.title,
      instance: this.getTerminalInstance(),
      prompt: promptEl,
      created,
      reopened: !wasVisible
    };
  }

  private scrollPromptIntoView(instance: TerminalShell) {
    if (!instance.bodyEl) {
      return;
    }
    instance.bodyEl.scrollTop = instance.bodyEl.scrollHeight;
  }

  private sanitizeTerminalOutput(lines: string[]): string[] {
    const promptLike = /^root@[\w.-]+:.*[#$]\s*/;
    return lines
      .map((line) => (line === null || line === undefined ? '' : String(line)))
      .filter((line) => !promptLike.test(line.trim()))
      .map((line) => line.replace(/\r$/, ''));
  }

  private resolveSessionMeta(
    payload: any,
    {
      persist = false,
      createIfMissing = false,
      forceNewIfAbsent = false,
      preferExisting = true
    }: {
      persist?: boolean;
      createIfMissing?: boolean;
      forceNewIfAbsent?: boolean;
      preferExisting?: boolean;
    } = {}
  ) {
    const nameCandidates = [
      payload?.arguments?.session_name,
      payload?.arguments?.session,
      payload?.arguments?.name,
      payload?.arguments?.title,
      payload?.result?.session,
      payload?.result?.session_name,
      payload?.result?.name,
      payload?.result?.title,
      payload?.name,
      payload?.title
    ].filter(Boolean) as string[];

    const rawName = nameCandidates.find(Boolean) || null;
    const explicitId =
      payload?.arguments?.session_id ||
      payload?.arguments?.connection_id ||
      payload?.result?.session_id ||
      payload?.session_id ||
      null;

    // 以 session_id 优先，其次用原始名字作为唯一键，保证同名复用同一会话
    let sessionId: string | null = explicitId;
    const nameKey = (rawName || '').trim() || null;
    if (!sessionId && nameKey && this.terminalRawNameMap.has(nameKey)) {
      sessionId = this.terminalRawNameMap.get(nameKey)!;
    }
    if (!sessionId && nameKey && this.terminalSessionTitleMap.has(nameKey)) {
      sessionId = this.terminalSessionTitleMap.get(nameKey)!;
    }
    if (preferExisting) {
      if (!sessionId && this.activeTerminalSessionId) {
        sessionId = this.activeTerminalSessionId;
      }
      if (!sessionId && this.lastTerminalSessionId) {
        sessionId = this.lastTerminalSessionId;
      }
      if (!sessionId && this.terminalSessions.size) {
        sessionId = Array.from(this.terminalSessions.keys())[0];
      }
    }

    let title: string;
    if (sessionId && this.terminalSessionNames.has(sessionId)) {
      title = this.terminalSessionNames.get(sessionId)!;
    } else {
      title = (rawName || '').trim() || this.nextTerminalName();
    }

    if (!sessionId && (createIfMissing || forceNewIfAbsent)) {
      sessionId = this.generateNewSessionId();
    }

    if (persist && sessionId) {
      this.lastTerminalSessionId = sessionId;
    }
    if (nameKey && sessionId) {
      this.terminalSessionTitleMap.set(nameKey, sessionId);
      this.terminalRawNameMap.set(nameKey, sessionId);
    }
    return { sessionId, title, rawName };
  }

  /**
   * 仅查找现有终端会话，不会新建
   */
  private resolveExistingSessionId(payload: any): string | null {
    const nameCandidates = [
      payload?.arguments?.session_name,
      payload?.arguments?.session,
      payload?.arguments?.name,
      payload?.arguments?.title,
      payload?.result?.session,
      payload?.result?.session_name,
      payload?.result?.name,
      payload?.result?.title,
      payload?.name,
      payload?.title
    ].filter(Boolean) as string[];
    const rawName = (nameCandidates.find(Boolean) || '').trim();
    if (rawName) {
      if (this.terminalRawNameMap.has(rawName)) {
        return this.terminalRawNameMap.get(rawName)!;
      }
      if (this.terminalSessionTitleMap.has(rawName)) {
        return this.terminalSessionTitleMap.get(rawName)!;
      }
    }
    if (this.activeTerminalSessionId) {
      return this.activeTerminalSessionId;
    }
    if (this.lastTerminalSessionId) {
      return this.lastTerminalSessionId;
    }
    if (this.terminalSessions.size) {
      return Array.from(this.terminalSessions.keys())[0];
    }
    return null;
  }

  private nextExtractionId() {
    return `extract-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  }

  private createDesktopFolderIcon(name: string) {
    const div = document.createElement('div');
    div.className = 'desktop-icon';
    div.dataset.folderName = name;
    const img = document.createElement('img');
    img.src = this.assets.folderIcon;
    img.alt = name;
    div.appendChild(img);
    const span = document.createElement('span');
    span.textContent = name;
    div.appendChild(span);
    return div;
  }

  private hideAllWindows() {
    [
      this.elements.browserWindow,
      this.elements.extractionWindow,
      this.elements.folderWindow,
      this.elements.editorWindow,
      this.elements.terminalWindow,
      this.elements.readerWindow,
      this.elements.memoryWindow,
      this.elements.todoWindow,
      this.elements.waitWindow
    ].forEach((win) => this.closeWindow(win, { animate: false }));
    this.clearExtractionWindows();
    this.clearTerminalSessions();
    this.windowOrder = [];
    this.manualPositions.clear();
  }

  private setupAnchors() {
    this.windowAnchors.set(this.elements.browserWindow, { x: 0.02, y: 0.02 });
    this.windowAnchors.set(this.elements.extractionWindow, { x: 0.6, y: 0.04 });
    this.windowAnchors.set(this.elements.folderWindow, { x: 0.68, y: 0.26 });
    this.windowAnchors.set(this.elements.editorWindow, { x: 0.62, y: 0.58 });
    this.windowAnchors.set(this.elements.terminalWindow, { x: 0.18, y: 0.42 });
    this.windowAnchors.set(this.elements.commandWindow, { x: 0.2, y: 0.7 });
    this.windowAnchors.set(this.elements.pythonWindow, { x: 0.52, y: 0.16 });
    this.windowAnchors.set(this.elements.readerWindow, { x: 0.42, y: 0.05 });
    this.windowAnchors.set(this.elements.memoryWindow, { x: 0.28, y: 0.32 });
    this.windowAnchors.set(this.elements.todoWindow, { x: 0.5, y: 0.32 });
    this.windowAnchors.set(this.elements.waitWindow, { x: 0.74, y: 0.24 });
  }

  private layoutFloatingWindows() {
    this.windowAnchors.forEach((anchor, el) => this.positionWindow(el, anchor));
    this.hideWaitOverlay();
  }

  private positionWindow(el: HTMLElement, anchor: { x: number; y: number }) {
    if (!el) {
      return;
    }
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    if (!width || !height) {
      return;
    }
    if (this.manualPositions.has(el)) {
      const manual = this.manualPositions.get(el)!;
      const { left, top } = this.clampWindowPosition(el, manual.left, manual.top);
      el.style.left = `${left}px`;
      el.style.top = `${top}px`;
      this.manualPositions.set(el, { left, top });
      return;
    }
    const availableWidth = Math.max(
      0,
      this.elements.screen.clientWidth - WINDOW_PADDING * 2 - width
    );
    const availableHeight = Math.max(
      0,
      this.elements.screen.clientHeight -
        WINDOW_PADDING -
        WINDOW_TOP_OFFSET -
        WINDOW_PADDING -
        height
    );
    const baseLeft = WINDOW_PADDING + availableWidth * anchor.x;
    const baseTop = WINDOW_TOP_OFFSET + availableHeight * anchor.y;
    const maxLeft = this.elements.screen.clientWidth - width - WINDOW_PADDING;
    const maxTop = Math.max(
      WINDOW_TOP_OFFSET,
      this.elements.screen.clientHeight - height - WINDOW_PADDING
    );
    const left = Math.min(Math.max(WINDOW_PADDING, baseLeft), Math.max(WINDOW_PADDING, maxLeft));
    const top = Math.min(Math.max(WINDOW_TOP_OFFSET, baseTop), maxTop);
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  private setupScenes() {
    this.sceneHandlers.browserSearch = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'browserSearch', t('monitor.searchStatus'));
      const browserVisible = this.isWindowVisible(this.elements.browserWindow);
      if (!browserVisible) {
        await this.movePointerToApp('browser');
        await this.click({ count: 2 });
      }
      this.showWindow(this.elements.browserWindow);
      await sleep(400);
      const searchBar = this.elements.browserSearchText.parentElement;
      if (searchBar) {
        await this.movePointerToElement(searchBar, { offsetX: -40 });
        await this.click();
      }
      const query = payload?.arguments?.query || payload?.argumentSnapshot?.query || t('monitor.defaultSearchQuery');
      await this.typeSearchQuery(query);
      this.elements.browserStatus.textContent = t('monitor.browserSearching');
      const completion = await runtime.waitForResult(payload.executionId || payload.id);
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.searchFailed'))) {
        return;
      }
      const results = Array.isArray(completion?.result?.results) ? completion.result.results : [];
      this.renderSearchResults(results);
      this.elements.browserStatus.textContent =
        completion?.status === 'completed' ? t('monitor.searchCompleted') : t('monitor.searchIncomplete');
      await sleep(320);
      await this.simulateResultBrowsing();
      this.pushWindowToStack(this.elements.browserWindow);
    };

    this.sceneHandlers.webExtract = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'webExtract', t('monitor.statusExtracting'));
      const targetUrl =
        payload?.arguments?.url ||
        payload?.arguments?.target_url ||
        payload?.result?.url ||
        payload?.result?.source ||
        '';
      let usedExistingSearch = false;
      if (targetUrl) {
        const resultEl = await this.focusSearchResultByUrl(targetUrl);
        if (resultEl) {
          usedExistingSearch = true;
          this.showWindow(this.elements.browserWindow);
          await this.movePointerToElement(resultEl, { duration: 700 });
          await this.click({ count: 2 });
          setTimeout(() => resultEl.classList.remove('target'), 800);
          await sleep(380);
        }
      }
      if (!usedExistingSearch) {
        const browserVisible = this.isWindowVisible(this.elements.browserWindow);
        if (!browserVisible) {
          await this.movePointerToApp('browser');
          await this.click({ count: 2 });
        }
        this.showWindow(this.elements.browserWindow);
        const searchBar = this.elements.browserSearchText.parentElement;
        if (searchBar) {
          await sleep(240);
          await this.movePointerToElement(searchBar, { offsetX: -40 });
          await this.click();
        }
        const navigateInput =
          targetUrl ||
          payload?.arguments?.query ||
          payload?.arguments?.title ||
          'https://example.com';
        await this.typeSearchQuery(navigateInput);
        this.elements.browserStatus.textContent = targetUrl
          ? t('monitor.browserOpening')
          : t('monitor.browserSearching');
        await sleep(900);
      }
      const extractionId = String(payload?.executionId || payload?.id || this.nextExtractionId());
      const extractionInstance = this.ensureExtractionWindowInstance(extractionId, {
        title: payload?.arguments?.title || payload?.result?.title,
        url: targetUrl
      });
      this.showWindow(extractionInstance.element);
      const displayUrl =
        targetUrl ||
        payload?.arguments?.url ||
        payload?.result?.url ||
        payload?.result?.source ||
        '';
      extractionInstance.urlEl.textContent = displayUrl;
      extractionInstance.stateEl.textContent = t('monitor.extractInProgress');
      extractionInstance.stateEl.classList.remove('complete');
      extractionInstance.summaryEl.innerHTML = '';
      let completion: any = null;
      try {
        completion = await runtime.waitForResult(payload.executionId || payload.id);
      } catch (error) {
        console.warn('[MonitorDirector] webExtract waitForResult error', error);
      }
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.extractFailed'))) {
        return;
      }
      const resolvedResult = completion?.result ?? payload?.result ?? null;
      const { hasContent, hasError } = this.renderExtractionSummary(
        extractionInstance.summaryEl,
        resolvedResult
      );
      const finalStatus = this.resolveExtractionStatus(completion, payload, resolvedResult);
      const computedStatus = finalStatus || (hasError ? 'failed' : hasContent ? 'completed' : null);
      if (computedStatus === 'failed') {
        extractionInstance.stateEl.textContent = t('monitor.extractStateFailed');
        extractionInstance.stateEl.classList.remove('complete');
      } else {
        extractionInstance.stateEl.textContent = t('monitor.extractStateComplete');
        extractionInstance.stateEl.classList.add('complete');
      }
      await sleep(400);
    };

    this.sceneHandlers.createFolder = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'createFolder', t('monitor.statusCreatingFolder'));
      this.lockDesktopRender();
      const rawPath =
        payload?.arguments?.path || payload?.arguments?.target_path || t('monitor.defaultFolderName');
      const segments = this.normalizePathSegments(rawPath);
      const folderName = segments.pop() || t('monitor.defaultFolderName');
      const parentKey = this.composePath(segments);
      const pendingPath = this.composePath([parentKey, folderName].filter(Boolean));
      this.markPendingCreation(pendingPath);
      let completion: any = null;
      try {
        let preExistingEl: HTMLElement | null = null;
        if (!segments.length) {
          preExistingEl = this.folderIcons.get(folderName) || null;
        } else {
          const prePath = this.composePath([parentKey, folderName]);
          preExistingEl = this.findFolderEntryElement(prePath);
        }
        if (preExistingEl) {
          preExistingEl.style.opacity = '0';
          preExistingEl.classList.add('pending-reveal');
        }
        const resultPromise = runtime
          .waitForResult(payload.executionId || payload.id)
          .catch((error) => {
            console.warn('[MonitorDirector] createFolder waitForResult error', error);
            return null;
          });
        if (segments.length) {
          const openedParent = await this.openFolderChain(segments);
          if (openedParent) {
            await this.movePointerToElement(this.elements.folderBody, {
              offsetX: 40,
              offsetY: 40,
              duration: 720
            });
            await this.click({ right: true });
            this.showContextMenu('folder');
            await sleep(200);
            await this.highlightMenu('folder', 'folder');
            await this.click();
            this.hideContextMenus();
          }
        } else {
          await this.movePointerToDesktop();
          await this.click({ right: true });
          this.showContextMenu('desktop');
          await sleep(240);
          await this.highlightMenu('desktop', 'folder');
          await this.click();
          this.hideContextMenus();
        }
        completion = await resultPromise;
        if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.createFolderFailed'))) {
          return;
        }
        const resolvedPath = this.resolveResultPath(completion, rawPath);
        this.releasePendingCreation(resolvedPath);
        const finalSegments = this.normalizePathSegments(resolvedPath);
        const finalName = finalSegments.pop() || folderName;
        if (finalSegments.length) {
          const finalParentKey = this.composePath(finalSegments);
          this.upsertFolderEntry(finalParentKey, { name: finalName, type: 'folder' });
          await this.openFolder(finalParentKey, finalParentKey);
          const entryPath = this.composePath([finalParentKey, finalName]);
          const entryEl = this.findFolderEntryElement(entryPath);
          if (entryEl) {
            entryEl.classList.add('visible');
            entryEl.style.opacity = '1';
          }
        } else {
          this.ensureFolderKey(finalName);
          if (!this.desktopRoots.includes(finalName)) {
            this.desktopRoots.push(finalName);
          }
          this.setDesktopRoots(this.desktopRoots, { immediate: true });
          const icon = await this.revealDesktopFolderIcon(finalName, { fallbackSpawn: true });
          if (icon) {
            icon.style.opacity = '1';
            await this.movePointerToElement(icon, { duration: 420 });
          }
        }
        await sleep(600);
      } finally {
        this.releasePendingCreation(pendingPath);
        this.unlockDesktopRender();
      }
    };

    this.sceneHandlers.createFile = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'createFile', t('monitor.statusCreatingFile'));
      this.lockDesktopRender();
      const rawPath = payload?.arguments?.path || payload?.arguments?.target_path || 'new-file.txt';
      const segments = this.normalizePathSegments(rawPath);
      const filename = segments.pop() || t('monitor.defaultFileName');
      const parentKey = this.composePath(segments);
      const pendingPath = this.composePath([parentKey, filename].filter(Boolean));
      this.markPendingCreation(pendingPath);
      let completion: any = null;
      try {
        let preExistingEl: HTMLElement | null = null;
        if (!segments.length) {
          preExistingEl = this.fileIcons.get(filename) || null;
        } else {
          const prePath = this.composePath([parentKey, filename]);
          preExistingEl = this.findFolderEntryElement(prePath);
        }
        if (preExistingEl) {
          preExistingEl.style.opacity = '0';
          preExistingEl.classList.add('pending-reveal');
        }
        const resultPromise = runtime
          .waitForResult(payload.executionId || payload.id)
          .catch((error) => {
            console.warn('[MonitorDirector] createFile waitForResult error', error);
            return null;
          });
        if (segments.length) {
          const openedParent = await this.openFolderChain(segments);
          if (openedParent) {
            await this.movePointerToElement(this.elements.folderBody, {
              offsetX: 30,
              offsetY: 20,
              duration: 720
            });
            await this.click({ right: true });
            this.showContextMenu('folder');
            await sleep(200);
            await this.highlightMenu('folder', 'file');
            await this.click();
            this.hideContextMenus();
          }
        } else {
          await this.movePointerToDesktop();
          await this.click({ right: true });
          this.showContextMenu('desktop');
          await sleep(200);
          await this.highlightMenu('desktop', 'file');
          await this.click();
          this.hideContextMenus();
        }
        completion = await resultPromise;
        if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.createFileFailed'))) {
          return;
        }
        const resolvedPath = this.resolveResultPath(completion, rawPath);
        this.releasePendingCreation(resolvedPath);
        const finalSegments = this.normalizePathSegments(resolvedPath);
        const finalName = finalSegments.pop() || filename;
        if (finalSegments.length) {
          const finalParentKey = this.composePath(finalSegments);
          this.upsertFolderEntry(finalParentKey, { name: finalName, type: 'file' });
          const entryPath = this.composePath([finalParentKey, finalName]);
          const entryEl = this.findFolderEntryElement(entryPath);
          if (entryEl) {
            entryEl.classList.add('visible');
            entryEl.style.opacity = '1';
          }
        } else {
          let fileIcon = this.fileIcons.get(finalName);
          if (!fileIcon) {
            fileIcon = this.spawnDesktopFile(finalName);
          }
          if (fileIcon) {
            fileIcon.style.opacity = '1';
            await this.movePointerToElement(fileIcon, { duration: 520 });
          }
        }
        await sleep(400);
      } finally {
        this.releasePendingCreation(pendingPath);
        this.unlockDesktopRender();
      }
    };

    this.sceneHandlers.renameFile = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'renameFile', t('monitor.statusRenaming'));
      this.lockDesktopRender();
      const sourcePath =
        payload?.arguments?.path ||
        payload?.arguments?.target_path ||
        payload?.arguments?.old_path ||
        payload?.arguments?.source_path ||
        payload?.arguments?.from_path ||
        payload?.argumentSnapshot?.old_path ||
        payload?.argumentSnapshot?.path ||
        payload?.result?.old_path ||
        payload?.result?.path;
      const targetPath =
        payload?.arguments?.new_path ||
        payload?.arguments?.destination_path ||
        payload?.arguments?.target_path ||
        payload?.argumentSnapshot?.new_path ||
        payload?.argumentSnapshot?.path ||
        payload?.result?.new_path ||
        payload?.result?.destination_path;
      renameDebug('scene:start', {
        sourcePath,
        targetPath,
        status: payload?.status,
        resultStatus: payload?.result?.status
      });
      if (!sourcePath || !targetPath) {
        renameDebug('scene:missing-path', { sourcePath, targetPath });
        await sleep(400);
        this.unlockDesktopRender();
        return;
      }
      let completion: any = null;
      try {
        completion = await runtime.waitForResult(payload.executionId || payload.id);
      } catch (error) {
        console.warn('[MonitorDirector] renameFile waitForResult error', error);
        completion = null;
      }
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.renameFailed'))) {
        this.unlockDesktopRender();
        return;
      }
      const sourceSegments = this.normalizePathSegments(sourcePath);
      const targetSegments = this.normalizePathSegments(targetPath);
      const fromName = sourceSegments.pop() || '';
      const toName = targetSegments.pop() || '';
      renameDebug('scene:resolved-names', {
        fromName,
        toName,
        sourceSegments: [...sourceSegments],
        targetSegments: [...targetSegments]
      });
      const clickRenameInFileMenu = async () => {
        this.showContextMenu('file');
        await this.waitForMenuVisible(this.elements.fileMenu, 240);
        const highlighted = await this.highlightMenu('file', 'rename');
        if (!highlighted) {
          const btn = this.elements.fileMenu.querySelector<HTMLButtonElement>(
            'button[data-action="rename"]'
          );
          if (btn) {
            await this.movePointerToElement(btn, { duration: 300 });
            btn.classList.add('active');
            await sleep(180);
            btn.classList.remove('active');
          }
        }
        await this.click();
        this.hideContextMenus();
      };
      try {
        if (sourceSegments.length) {
          const parentKey = await this.openFolderChain(sourceSegments);
          if (parentKey) {
            const existing = (this.folderEntries.get(parentKey) || []).find(
              (item) => item.name === fromName
            );
            const entryType = existing?.type || 'file';
            this.upsertFolderEntry(
              parentKey,
              { name: fromName, type: entryType },
              { animate: false }
            );
            await this.openFolder(parentKey, parentKey);
            await sleep(40);
            const entryPath = this.composePath([parentKey, fromName]);
            const entryEl = this.findFolderEntryElement(entryPath);
            renameDebug('scene:folder-target', { entryPath, found: !!entryEl });
            if (!entryEl) {
              renameDebug('scene:folder-missing-target', { entryPath });
            } else {
              await this.movePointerToElement(entryEl, { duration: 620 });
              await this.click({ right: true });
              await clickRenameInFileMenu();
              await this.animateRenameLabel(entryEl, toName);
              renameDebug('scene:folder-rename-animated', { entryPath, toName });
            }
            this.renameFolderEntry(parentKey, fromName, toName, { skipRender: true });
          }
        } else {
          const icon = this.fileIcons.get(fromName) || this.folderIcons.get(fromName) || null;
          renameDebug('scene:desktop-target', { fromName, found: !!icon });
          if (!icon) {
            renameDebug('scene:desktop-missing-target', { fromName });
          } else {
            await this.movePointerToElement(icon, { duration: 600 });
            await this.click({ right: true });
            await clickRenameInFileMenu();
            await this.animateRenameLabel(icon, toName);
            this.renameDesktopEntry(icon, toName);
            renameDebug('scene:desktop-rename-animated', { fromName, toName });
            if (this.fileIcons.has(fromName)) {
              this.fileIcons.delete(fromName);
              this.fileIcons.set(toName, icon);
            }
            if (this.folderIcons.has(fromName)) {
              this.renameDesktopRoot(fromName, toName, { skipRender: true });
            }
          }
        }
        await sleep(400);
      } catch (error) {
        console.error('[MonitorRename] scene error', error);
        renameDebug('scene:error', { error });
      } finally {
        this.unlockDesktopRender();
      }
    };

    this.sceneHandlers.deleteFile = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'deleteFile', t('monitor.statusDeletingFile'));
      const rawPath = payload?.arguments?.path || payload?.arguments?.target_path;
      const segments = this.normalizePathSegments(rawPath);
      const name = segments.pop();
      if (!name) {
        await sleep(300);
        return;
      }
      const openDeleteMenu = async (
        targetEl: HTMLElement | null,
        menuType: ContextMenuType = 'file'
      ) => {
        if (!targetEl) {
          return false;
        }
        await this.movePointerToElement(targetEl, { duration: 600 });
        await this.click({ right: true });
        this.showContextMenu(menuType);
        await sleep(160);
        const highlighted = await this.highlightMenu('file', 'delete');
        if (!highlighted) {
          const btn = this.elements.fileMenu.querySelector<HTMLButtonElement>(
            'button[data-action="delete"]'
          );
          if (btn) {
            await this.movePointerToElement(btn, { duration: 320 });
          }
        }
        await this.click();
        this.hideContextMenus();
        return true;
      };
      if (segments.length) {
        const parentKey = await this.openFolderChain(segments);
        if (parentKey) {
          const existing = (this.folderEntries.get(parentKey) || []).find(
            (item) => item.name === name
          );
          const entryType = existing?.type || 'file';
          this.upsertFolderEntry(parentKey, { name, type: entryType }, { animate: false });
          await this.openFolder(parentKey, parentKey);
          await sleep(40);
          const entryPath = this.composePath([parentKey, name]);
          const entryEl = this.findFolderEntryElement(entryPath);
          const menuShown = await openDeleteMenu(entryEl, 'file');
          if (!menuShown) {
            this.removeFolderEntry(parentKey, name);
            return;
          }
          entryEl?.classList.add('removing');
          setTimeout(() => this.removeFolderEntry(parentKey, name), 260);
        }
      } else {
        const icon = this.fileIcons.get(name) || this.folderIcons.get(name);
        if (icon) {
          await openDeleteMenu(icon, 'file');
          icon.classList.add('removing');
          setTimeout(() => icon.remove(), 320);
          this.fileIcons.delete(name);
          if (this.folderIcons.has(name)) {
            this.folderIcons.delete(name);
            this.folderEntries.delete(name);
            const idx = this.desktopRoots.indexOf(name);
            if (idx >= 0) {
              this.desktopRoots.splice(idx, 1);
            }
          }
        }
      }
      await sleep(400);
    };

    this.sceneHandlers.wait = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'wait', t('monitor.statusWaiting'));
      const duration =
        Number(
          payload?.arguments?.duration ||
            payload?.arguments?.seconds ||
            payload?.seconds ||
            payload?.duration
        ) || 5;
      await this.movePointerToDesktop();
      await this.click({ right: true });
      this.showContextMenu('desktop');
      await sleep(200);
      const highlighted = await this.highlightMenu('desktop', 'wait');
      if (!highlighted) {
        const btn = this.elements.desktopMenu.querySelector<HTMLButtonElement>(
          'button[data-action="wait"]'
        );
        if (btn) {
          await this.movePointerToElement(btn, { duration: 320 });
        }
      }
      await this.click();
      this.hideContextMenus();
      await sleep(140); // 等菜单完全收起后再出现等待提示
      const waitPromise = runtime
        .waitForResult(payload.executionId || payload.id)
        .catch(() => null);
      await this.playWaitCountdown(duration, waitPromise);
    };

    this.sceneHandlers.appendFile = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'appendFile', t('monitor.statusEditing'));
      const rawPath = payload?.arguments?.path || payload?.argumentSnapshot?.path || 'file.txt';
      const segments = this.normalizePathSegments(rawPath);
      const filename = segments.pop() || 'file.txt';
      const snapshotPath =
        (typeof payload?.monitor_snapshot?.path === 'string' && payload.monitor_snapshot.path) ||
        (typeof payload?.monitor_snapshot_after?.path === 'string' &&
          payload.monitor_snapshot_after.path) ||
        null;
      const canonicalPath = snapshotPath || this.composePath([...segments, filename]);
      const fetchTargetPath = canonicalPath || rawPath;
      editorDebug('scene:appendFile:start', {
        execution: payload?.executionId || payload?.id,
        path: rawPath
      });
      const editorAlreadyVisible = this.isWindowVisible(this.elements.editorWindow);
      if (!editorAlreadyVisible) {
        const targetEntry = await this.revealFileTarget(rawPath, { spawnDesktopFile: true });
        if (targetEntry?.element) {
          await this.openFileMenuAction(targetEntry.element, 'edit');
        } else {
          await this.movePointerToDesktop();
        }
      }
      this.openEditorWindow(filename);
      this.renderEditorPlaceholder(t('monitor.readingContent'));
      const payloadBeforeLines = this.resolveEditorBeforeLines(payload);
      const snapshotBeforeSource =
        typeof payload?.monitor_snapshot?.content === 'string'
          ? payload.monitor_snapshot.content
          : payload?.monitor_snapshot?.before;
      const snapshotBeforeLines = this.normalizeLines(snapshotBeforeSource);
      let remoteBeforeLines: string[] | null = null;
      const executionKey = payload?.executionId || payload?.execution_id || payload?.id;
      if (!snapshotBeforeLines.length && fetchTargetPath) {
        remoteBeforeLines = await this.fetchEditorFileLines(fetchTargetPath);
      }
      let beforeLines = payloadBeforeLines.length
        ? payloadBeforeLines
        : snapshotBeforeLines.length
          ? snapshotBeforeLines
          : remoteBeforeLines || [];
      if (!beforeLines.length && executionKey) {
        const cachedBefore = await this.fetchMonitorSnapshotById(executionKey, 'before');
        if (cachedBefore?.length) {
          beforeLines = cachedBefore;
          editorDebug('scene:appendFile:before-cache-hit', { executionId: executionKey });
        }
      }
      if (!beforeLines.length && canonicalPath && this.editorSnapshots.has(canonicalPath)) {
        beforeLines = this.editorSnapshots.get(canonicalPath)!.slice();
      }
      editorDebug('scene:appendFile:beforeLines', {
        count: beforeLines.length,
        remote: !!remoteBeforeLines?.length
      });
      this.prepareEditorScene(beforeLines);
      const completion = await runtime
        .waitForResult(payload.executionId || payload.id)
        .catch((error) => {
          console.warn('[MonitorDirector] appendFile waitForResult error', error);
          return null;
        });
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.editFailed'))) {
        return;
      }
      const payloadAfterLines = this.resolveEditorAfterLines(payload, completion);
      const snapshotAfterSource =
        typeof completion?.monitor_snapshot_after?.content === 'string'
          ? completion.monitor_snapshot_after.content
          : typeof payload?.monitor_snapshot_after?.content === 'string'
            ? payload.monitor_snapshot_after.content
            : payload?.monitor_snapshot_after?.after;
      const snapshotAfterLines = this.normalizeLines(snapshotAfterSource);
      let remoteAfterLines: string[] | null = null;
      if (!snapshotAfterLines.length && fetchTargetPath) {
        remoteAfterLines = await this.fetchEditorFileLines(fetchTargetPath);
      }
      let afterLines = payloadAfterLines.length
        ? payloadAfterLines
        : snapshotAfterLines.length
          ? snapshotAfterLines
          : remoteAfterLines || [];
      if (!afterLines.length && executionKey) {
        const cachedAfter = await this.fetchMonitorSnapshotById(executionKey, 'after');
        if (cachedAfter?.length) {
          afterLines = cachedAfter;
          editorDebug('scene:appendFile:after-cache-hit', { executionId: executionKey });
        }
      }
      if (!afterLines.length && canonicalPath && this.editorSnapshots.has(canonicalPath)) {
        afterLines = this.editorSnapshots.get(canonicalPath)!.slice();
      }
      const fallbackNew = this.normalizeLines(
        payload?.arguments?.content || payload?.result?.content || ''
      );
      const nextLines = afterLines.length ? afterLines : fallbackNew;
      editorDebug('scene:appendFile:nextLines', {
        after: afterLines.length,
        fallback: fallbackNew.length,
        remote: !!remoteAfterLines?.length
      });
      await sleep(260);
      await this.animateEditorTransition(nextLines);
      if (canonicalPath && nextLines.length) {
        this.editorSnapshots.set(canonicalPath, nextLines.slice(0, EDITOR_MAX_RENDER_LINES));
      }
      editorDebug('scene:appendFile:finished');
      await sleep(400);
    };

    this.sceneHandlers.modifyFile = this.sceneHandlers.appendFile;

    this.sceneHandlers.runCommand = async (payload, runtime) => {
      const toolLabel = payload?.name || payload?.tool || 'run_command';
      this.applySceneStatus(runtime, 'runCommand', t('monitor.statusCallingTool', { tool: toolLabel }));
      const command = payload?.arguments?.command || payload?.result?.command || 'echo "Hello"';
      const reuse = this.isWindowVisible(this.elements.commandWindow);
      if (reuse) {
        this.showWindow(this.elements.commandWindow);
        await this.focusCommandInput();
        if (this.elements.commandOutput) {
          this.elements.commandOutput.innerHTML = '';
        }
      } else {
        await this.revealCommandWindow(t('monitor.commandTitle'), { reset: true, focusInput: true });
      }
      await this.typeCommandText(command);
      const completion = await runtime.waitForResult(payload.executionId || payload.id);
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.commandFailed'))) {
        return;
      }
      const output =
        completion?.result?.output || completion?.result?.stdout || t('monitor.commandDone');
      const lines = this.sanitizeTerminalOutput(
        typeof output === 'string'
          ? output.split('\n')
          : Array.isArray(output)
            ? output.map(String)
            : [String(output || '')]
      );
      this.appendCommandOutput(lines.length ? lines : [t('monitor.commandDone')]);
      await sleep(500);
    };

    this.sceneHandlers.reader = async (payload, runtime) => {
      const targetPath = payload?.arguments?.path || payload?.result?.path || t('monitor.defaultDocPath');
      const readMode = String(
        payload?.arguments?.type || payload?.result?.type || 'read'
      ).toLowerCase();
      const statusMap: Record<string, string> = {
        search: t('monitor.readerModeSearch'),
        extract: t('monitor.readerModeExtract')
      };
      runtime.setStatus(statusMap[readMode] || t('monitor.statusReading'));
      readerDebug('readerScene:start', {
        executionId: payload?.executionId || payload?.id,
        targetPath,
        readMode
      });
      const targetEntry = await this.revealFileTarget(targetPath, {
        doubleClick: true,
        spawnDesktopFile: true
      });
      if (!targetEntry) {
        await this.movePointerToDesktop();
      }
      this.showWindow(this.elements.readerWindow);
      this.elements.readerTitle.textContent = targetPath;
      this.renderReaderMessage(t('monitor.readingContent'));
      let completion: any = null;
      try {
        completion = await runtime.waitForResult(payload.executionId || payload.id);
        readerDebug('readerScene:waitForResult resolved', {
          executionId: payload?.executionId || payload?.id,
          hasCompletion: !!completion,
          status: completion?.status,
          keys: completion ? Object.keys(completion) : []
        });
      } catch (error) {
        console.warn('[MonitorDirector] reader waitForResult error', error);
        readerDebug('readerScene:waitForResult error', error);
      }
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.readFailed'))) {
        return;
      }
      const { source: resultPayload, label: payloadSource } = this.resolveReaderPayload(
        payload,
        completion
      );
      readerDebug('readerScene:payload resolved', {
        executionId: payload?.executionId || payload?.id,
        payloadSource,
        hasPayload: !!resultPayload
      });
      const extractedLines = resultPayload ? this.extractReaderLines(resultPayload) : [];
      readerDebug('readerScene:extracted lines', {
        executionId: payload?.executionId || payload?.id,
        count: extractedLines.length
      });
      if (extractedLines.length) {
        this.renderReaderLines(resultPayload);
        readerDebug('readerScene:render lines', {
          executionId: payload?.executionId || payload?.id,
          count: extractedLines.length
        });
      } else {
        const fallbackMessage =
          completion?.message || payload?.result?.message || t('monitor.noDisplayContent');
        this.renderReaderMessage(fallbackMessage);
        readerDebug('readerScene:fallback message', {
          executionId: payload?.executionId || payload?.id,
          fallbackMessage
        });
      }
      await sleep(500);
    };

    this.sceneHandlers.focus = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'focus', t('monitor.statusFocusingFile'));
      const targetPath = payload?.arguments?.path || payload?.result?.path || t('monitor.defaultFilePath');
      const entry = await this.revealFileTarget(targetPath, { spawnDesktopFile: true });
      if (entry?.element) {
        await this.click({ right: true });
        this.showContextMenu('focus');
        await sleep(160);
        await this.highlightMenu('focus', 'focus');
        await this.click();
        this.hideContextMenus();
      } else {
        await this.movePointerToDesktop();
      }
      const completion = await runtime
        .waitForResult(payload.executionId || payload.id)
        .catch((error) => {
          console.warn('[MonitorDirector] focus waitForResult error', error);
          return null;
        });
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.focusFailed'))) {
        return;
      }
      this.showWindow(this.elements.readerWindow);
      this.elements.readerTitle.textContent = targetPath;
      this.renderReaderMessage(t('monitor.loadingContent'));
      let rendered = false;
      if (completion?.result) {
        const lines = this.extractReaderLines(completion.result);
        if (lines.length) {
          this.renderReaderLines(completion.result);
          rendered = true;
        }
      }
      if (!rendered && targetPath) {
        const fallbackLines = await this.fetchEditorFileLines(targetPath);
        if (fallbackLines?.length) {
          this.renderReaderLines(fallbackLines);
          rendered = true;
        }
      }
      if (!rendered) {
        this.renderReaderMessage(t('monitor.focusedReady'));
      }
      this.elements.readerWindow.classList.add('focused');
      await sleep(400);
    };

    this.sceneHandlers.unfocus = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'unfocus', t('monitor.statusProcessing'));
      const targetPath = payload?.arguments?.path || payload?.result?.path || t('monitor.defaultFilePath');
      const entry = await this.revealFileTarget(targetPath, { spawnDesktopFile: true });
      if (entry?.element) {
        await this.click({ right: true });
        this.showContextMenu('focus');
        await sleep(160);
        await this.highlightMenu('focus', 'unfocus');
        await this.click();
        this.hideContextMenus();
      } else {
        await this.movePointerToDesktop();
      }
      await runtime.waitForResult(payload.executionId || payload.id).catch((error) => {
        console.warn('[MonitorDirector] unfocus waitForResult error', error);
        return null;
      });
      if (!this.ensureSuccessOrErrorBubble(null, payload, t('monitor.unfocusFailed'))) {
        return;
      }
      this.showWindow(this.elements.readerWindow);
      this.elements.readerTitle.textContent = targetPath;
      this.elements.readerWindow.classList.remove('focused');
      this.renderReaderMessage(t('monitor.unfocused'));
      await sleep(400);
    };

    this.sceneHandlers.ocr = async (payload, runtime) => {
      await this.sceneHandlers.reader(payload, runtime);
      this.applySceneStatus(runtime, 'ocr', t('monitor.statusExtracting'));
      const completion = await runtime.waitForResult(payload.executionId || payload.id);
      if (!this.ensureSuccessOrErrorBubble(completion, payload, t('monitor.ocrFailed'))) {
        return;
      }
      const lines = completion?.result?.text || completion?.result?.lines || [];
      this.renderReaderOcr(lines);
      await sleep(400);
    };

    this.sceneHandlers.memoryUpdate = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'memoryUpdate', t('monitor.statusSyncingMemory'));
      const { entries: initialEntries, provided: snapshotProvided } =
        this.extractMemorySnapshotEntries(payload, 'before');
      const memoryType = (payload?.arguments?.memory_type || payload?.result?.memory_type || 'main')
        .toString()
        .toLowerCase();
      await this.ensureMemoryWindowVisible({ initialEntries, snapshotProvided, memoryType });

      const op = (
        payload?.arguments?.operation ||
        payload?.result?.operation ||
        'append'
      ).toLowerCase();
      const content = payload?.arguments?.content || payload?.result?.content || '';
      const index = Number(payload?.arguments?.index || payload?.result?.index || 0) || 0;

      if (op === 'replace') {
        await this.animateMemoryReplace(index, content || '');
      } else if (op === 'delete') {
        await this.animateMemoryDelete(index);
      } else {
        await this.animateMemoryAppend(content || t('monitor.defaultMemory'));
      }

      this.updateMemoryMeta();
      await sleep(360);
    };

    this.sceneHandlers.todoCreate = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'todoCreate', t('monitor.statusUpdatingTodo'));
      const summary =
        payload?.arguments?.summary ||
        payload?.arguments?.overview ||
        payload?.arguments?.title ||
        payload?.arguments?.task ||
        t('monitor.defaultTodoSummary');
      const tasks = this.normalizeTodoTasks(payload?.arguments?.tasks || summary);
      await this.ensureTodoWindowVisible();
      this.resetTodoBoard({ summary: true, list: true });
      await this.typeTodoSummary(summary);
      for (const task of tasks) {
        await this.animateTodoAppend(task, { scrollIntoView: false });
      }
      if (this.elements.todoList) {
        this.elements.todoList.scrollTop = 0;
      }
      await sleep(320);
    };

    this.sceneHandlers.todoUpdate = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'todoUpdate', t('monitor.statusAdjustingTodo'));
      await this.ensureTodoWindowVisible();
      const targetText = payload?.arguments?.title || payload?.arguments?.task || null;
      const targetIndex =
        Number(payload?.arguments?.task_index || payload?.arguments?.index || 0) || null;
      const completed =
        payload?.arguments?.completed ??
        payload?.arguments?.done ??
        payload?.arguments?.checked ??
        true;
      await this.toggleTodoItem(targetText, !!completed, targetIndex);
      await sleep(260);
    };

    this.sceneHandlers.todoFinish = async (_payload, runtime) => {
      this.applySceneStatus(runtime, 'todoFinish', t('monitor.statusFinishingTask'));
      const redDot = this.elements.todoWindow.querySelector(
        '.traffic-dot.red'
      ) as HTMLElement | null;
      if (redDot) {
        await this.movePointerToElement(redDot, { duration: 420 });
        await this.click();
      }
      this.closeWindow(this.elements.todoWindow);
      this.resetTodoBoard({ summary: true, list: true });
      await sleep(320);
    };

    this.sceneHandlers.todoFinishConfirm = this.sceneHandlers.todoFinish;

    this.sceneHandlers.todoDelete = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'todoDelete', t('monitor.statusRemovingTodo'));
      const targetText = payload?.arguments?.title || payload?.arguments?.task || null;
      const card = this.findTodoItemByText(targetText);
      if (card) {
        await this.scrollTodoItemIntoView(card);
        card.classList.add('removing');
        await this.movePointerToElement(card, { duration: 420 });
        await this.click({ right: true });
        setTimeout(() => card.remove(), 260);
      }
      await sleep(280);
    };

    this.sceneHandlers.terminalSession = async (payload, runtime) => {
      const action = (payload?.arguments?.action || payload?.action || '').toLowerCase();
      if (action === 'reset') {
        this.applySceneStatus(runtime, 'terminalSession', t('monitor.statusResettingTerminal'));
      } else {
        this.applySceneStatus(runtime, 'terminalSession', t('monitor.statusOpeningTerminal'));
      }
      // 特殊处理：如果是关闭/重置终端，不要无意中新建会话
      if (action === 'close' || action === 'reset') {
        terminalMenuDebug('terminalSession:action', { action, payload });
        const targetSession = this.resolveExistingSessionId(payload);
        if (!targetSession) {
          terminalMenuDebug('terminalSession:action:no-session', { action });
          return;
        }
        // 确保窗口可见，但不强制激活
        const shell = this.getTerminalInstance();
        if (!this.isWindowVisible(shell.element)) {
          await this.revealTerminalWindow(shell);
        } else {
          this.showWindow(shell.element);
        }
        if (action === 'reset') {
          await this.openTerminalContextMenu(targetSession);
          await this.chooseTerminalMenuAction('reset');
          this.terminalHistories.set(targetSession, [{ text: '➜ ', role: 'prompt' }]);
          this.renderTerminalHistory(targetSession);
          this.appendTerminalNote(targetSession, t('monitor.terminalReset'));
          this.ensurePromptLine(targetSession);
          await sleep(300);
          return;
        }
        // action === 'close'
        await this.openTerminalContextMenu(targetSession);
        await this.chooseTerminalMenuAction('close');
        this.closeTerminalSession(targetSession);
        if (!this.terminalSessions.size) {
          this.closeWindow(this.elements.terminalWindow, { animate: true });
        }
        await sleep(320);
        return;
      }

      const explicitSessionProvided =
        payload?.arguments?.session_id ||
        payload?.arguments?.connection_id ||
        payload?.arguments?.session ||
        payload?.arguments?.session_name;
      const nameHint =
        payload?.arguments?.session_name ||
        payload?.arguments?.session ||
        payload?.arguments?.name ||
        payload?.arguments?.title ||
        payload?.result?.session ||
        payload?.result?.name ||
        payload?.title ||
        null;
      const mappedExisting =
        nameHint &&
        (this.terminalRawNameMap.has(nameHint) || this.terminalSessionTitleMap.has(nameHint));
      const shouldForceNew =
        payload?.arguments?.create_new === true ||
        payload?.arguments?.new_session === true ||
        payload?.arguments?.force_new === true ||
        (!explicitSessionProvided && this.terminalSessions.size > 0 && !mappedExisting);
      await this.ensureTerminalSessionReady(payload, {
        focusPrompt: false,
        forceNew: shouldForceNew,
        createIfMissing: true,
        activate: true
      });
      await sleep(200);
    };

    this.sceneHandlers.terminalInput = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'terminalInput', t('monitor.statusCallingTerminalInput'));
      const { sessionId } = await this.ensureTerminalSessionReady(payload, {
        focusPrompt: true,
        activate: true
      });
      const command =
        payload?.arguments?.command ||
        payload?.arguments?.input ||
        payload?.result?.command ||
        payload?.result?.input ||
        '';
      await this.typeSessionCommand(sessionId, command);
      const completion = await runtime
        .waitForResult(payload.executionId || payload.id)
        .catch((error) => {
          console.warn('[MonitorDirector] terminalInput waitForResult error', error);
          return null;
        });
      const output =
        completion?.result?.output ||
        completion?.result?.stdout ||
        completion?.result?.content ||
        completion?.result ||
        '';
      const lines = this.sanitizeTerminalOutput(
        typeof output === 'string'
          ? output.split('\n')
          : Array.isArray(output)
            ? output.map(String)
            : [String(output || '')]
      );
      this.appendTerminalOutputs(sessionId, command, lines.length ? lines : [t('monitor.commandSent')]);
      await sleep(400);
    };

    this.sceneHandlers.terminalSnapshot = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'terminalSnapshot', t('monitor.statusGettingTerminal'));
      const { sessionId } = await this.ensureTerminalSessionReady(payload, {
        focusPrompt: false,
        activate: false
      });
      terminalMenuDebug('scene:terminalSnapshot:start', { sessionId });
      await this.openTerminalContextMenu(sessionId);
      terminalMenuDebug('scene:terminalSnapshot:menu-opened', { sessionId });
      await this.chooseTerminalMenuAction('snapshot');
      this.appendTerminalNote(sessionId, '[Snapshot Captured]');
      this.ensurePromptLine(sessionId);
      await sleep(300);
    };

    this.sceneHandlers.terminalSleep = async (payload, runtime) => {
      this.applySceneStatus(runtime, 'terminalSleep', t('monitor.statusWaiting'));
      const duration =
        Number(
          payload?.arguments?.duration ||
            payload?.arguments?.seconds ||
            payload?.seconds ||
            payload?.duration
        ) || 5;
      await this.movePointerToDesktop();
      await this.click({ right: true });
      this.showContextMenu('desktop');
      await sleep(200);
      const highlighted = await this.highlightMenu('desktop', 'wait');
      if (!highlighted) {
        const btn = this.elements.desktopMenu.querySelector<HTMLButtonElement>(
          'button[data-action="wait"]'
        );
        if (btn) {
          await this.movePointerToElement(btn, { duration: 320 });
        }
      }
      await this.click();
      this.hideContextMenus();
      await sleep(140); // 等菜单完全收起后再出现等待提示
      const waitPromise = runtime
        .waitForResult(payload.executionId || payload.id)
        .catch(() => null);
      await this.playWaitCountdown(duration, waitPromise);
    };

    this.sceneHandlers.sleep = this.sceneHandlers.wait;

    this.sceneHandlers.webSave = async (_payload, runtime) => {
      this.applySceneStatus(runtime, 'webSave', t('monitor.statusSavingWeb'));
      const targetUrl =
        _payload?.arguments?.url ||
        _payload?.arguments?.target_url ||
        _payload?.result?.url ||
        _payload?.result?.source ||
        '';
      let targetEl: HTMLLIElement | null = null;
      if (targetUrl) {
        targetEl = await this.focusSearchResultByUrl(targetUrl);
      }
      if (!targetEl) {
        targetEl = this.elements.browserResults.querySelector('li') as HTMLLIElement | null;
      }
      if (targetEl) {
        this.showWindow(this.elements.browserWindow);
        await this.movePointerToElement(targetEl, { duration: 620 });
        await this.click({ right: true });
        this.showContextMenu('browser');
      } else {
        await this.movePointerToElement(this.elements.browserWindow, {
          offsetX: 40,
          offsetY: -120
        });
        await this.click({ right: true });
        this.showContextMenu('browser');
      }
      await sleep(200);
      await this.highlightMenu('browser', 'save');
      await this.click();
      this.hideContextMenus();
      const target = _payload?.arguments?.path || 'saved-page.html';
      this.spawnDesktopFile(target.split('/').pop() || 'saved-page.html');
      await sleep(600);
    };

    this.sceneHandlers.genericTool = async (payload, runtime) => {
      const toolLabel = payload?.name || payload?.tool || 'tool';
      this.applySceneStatus(runtime, 'genericTool', t('monitor.statusCallingTool', { tool: toolLabel }));
      await sleep(600);
    };
  }

  /**
   * 终端标签区的手动交互：左键切换、点击 + 创建、右键打开菜单
   */
  private bindTerminalInteractions() {
    const addBtn = this.elements.terminalAddButton;
    const tabList = this.elements.terminalTabList;
    const menu = this.elements.terminalMenu;
    if (addBtn) {
      addBtn.addEventListener('click', this.handleTerminalAddClick);
    }
    if (tabList) {
      tabList.addEventListener('click', this.handleTerminalTabClick);
      tabList.addEventListener('contextmenu', this.handleTerminalTabContext);
    }
    if (menu) {
      menu.addEventListener('click', this.handleTerminalMenuClick);
    }
    this.destroyFns.push(() => {
      addBtn?.removeEventListener('click', this.handleTerminalAddClick);
      tabList?.removeEventListener('click', this.handleTerminalTabClick);
      tabList?.removeEventListener('contextmenu', this.handleTerminalTabContext);
      menu?.removeEventListener('click', this.handleTerminalMenuClick);
    });
  }

  private handleTerminalAddClick = (event: MouseEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    event.preventDefault();
    const sessionId = this.createSession();
    this.activateSession(sessionId);
  };

  private handleTerminalTabClick = (event: MouseEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    const target = (event.target as HTMLElement | null)?.closest('.terminal-tab');
    if (!target || target.classList.contains('add-tab')) {
      return;
    }
    const sessionId = target.getAttribute('data-session-id');
    if (sessionId) {
      event.preventDefault();
      this.activateSession(sessionId);
    }
  };

  private handleTerminalTabContext = (event: MouseEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    const target = (event.target as HTMLElement | null)?.closest('.terminal-tab');
    if (!target || target.classList.contains('add-tab')) {
      return;
    }
    const sessionId = target.getAttribute('data-session-id');
    if (!sessionId) {
      return;
    }
    event.preventDefault();
    this.terminalContextSessionId = sessionId;
    const screenRect = this.elements.screen.getBoundingClientRect();
    const x = event.clientX - screenRect.left;
    const y = event.clientY - screenRect.top;
    this.showContextMenu('terminal', { x, y });
  };

  private handleTerminalMenuClick = (event: MouseEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    const target = event.target as HTMLElement | null;
    if (!target || target.tagName.toLowerCase() !== 'button') {
      return;
    }
    const action = target.getAttribute('data-action');
    const sessionId = this.terminalContextSessionId || this.activeTerminalSessionId;
    if (!action || !sessionId) {
      this.hideContextMenus();
      return;
    }
    event.preventDefault();
    if (action === 'snapshot') {
      this.appendTerminalNote(sessionId, '[Snapshot Captured]');
      this.ensurePromptLine(sessionId);
    } else if (action === 'reset') {
      this.terminalHistories.set(sessionId, [{ text: '➜ ', role: 'prompt' }]);
      this.renderTerminalHistory(sessionId);
      this.appendTerminalNote(sessionId, t('monitor.terminalReset'));
      this.ensurePromptLine(sessionId);
    } else if (action === 'close') {
      this.closeTerminalSession(sessionId);
    }
    this.hideContextMenus();
  };

  private bindManualInteractionListeners() {
    if (this.manualListenersAttached) {
      return;
    }
    this.manualListenersAttached = true;
    this.elements.screen.addEventListener('pointerdown', this.handleManualPointerDown);
    window.addEventListener('pointermove', this.handleManualPointerMove, { passive: false });
    window.addEventListener('pointerup', this.handleManualPointerUp);
    window.addEventListener('pointercancel', this.handleManualPointerUp);
    this.elements.screen.addEventListener('click', this.handleManualCloseClick, true);
  }

  private unbindManualInteractionListeners() {
    if (!this.manualListenersAttached) {
      return;
    }
    this.manualListenersAttached = false;
    this.elements.screen.removeEventListener('pointerdown', this.handleManualPointerDown);
    window.removeEventListener('pointermove', this.handleManualPointerMove);
    window.removeEventListener('pointerup', this.handleManualPointerUp);
    window.removeEventListener('pointercancel', this.handleManualPointerUp);
    this.elements.screen.removeEventListener('click', this.handleManualCloseClick, true);
  }

  private resetManualPositions() {
    if (!this.manualPositions.size) {
      return;
    }
    this.manualPositions.forEach((_pos, el) => {
      const anchor = this.windowAnchors.get(el);
      if (anchor) {
        this.positionWindow(el, anchor);
      }
    });
    this.manualPositions.clear();
  }

  private beginManualDrag(windowEl: HTMLElement, event: PointerEvent) {
    if (!windowEl) {
      return;
    }
    const rect = windowEl.getBoundingClientRect();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    this.manualDragState = {
      pointerId: event.pointerId,
      target: windowEl,
      offsetX,
      offsetY
    };
    try {
      windowEl.setPointerCapture(event.pointerId);
    } catch (error) {
      console.warn('[MonitorDirector] setPointerCapture failed', error);
    }
    windowEl.classList.add('manual-dragging');
    this.updateManualDragPosition(event);
  }

  private updateManualDragPosition(event: PointerEvent) {
    if (!this.manualDragState) {
      return;
    }
    const { target, offsetX, offsetY } = this.manualDragState;
    if (!target || !target.isConnected) {
      return;
    }
    const screenRect = this.elements.screen.getBoundingClientRect();
    const desiredLeft = event.clientX - screenRect.left - offsetX;
    const desiredTop = event.clientY - screenRect.top - offsetY;
    const { left, top } = this.clampWindowPosition(target, desiredLeft, desiredTop);
    target.style.left = `${left}px`;
    target.style.top = `${top}px`;
    this.manualPositions.set(target, { left, top });
  }

  private cancelManualDrag() {
    if (!this.manualDragState) {
      return;
    }
    const { target, pointerId } = this.manualDragState;
    if (target && target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
    target?.classList.remove('manual-dragging');
    this.manualDragState = null;
  }

  private clampWindowPosition(el: HTMLElement, left: number, top: number) {
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    const maxLeft = this.elements.screen.clientWidth - width - WINDOW_PADDING;
    const maxTop = Math.max(
      WINDOW_TOP_OFFSET,
      this.elements.screen.clientHeight - height - WINDOW_PADDING
    );
    const clampedLeft = Math.min(Math.max(WINDOW_PADDING, left), Math.max(WINDOW_PADDING, maxLeft));
    const clampedTop = Math.min(Math.max(WINDOW_TOP_OFFSET, top), maxTop);
    return { left: clampedLeft, top: clampedTop };
  }

  private handleManualPointerDown = (event: PointerEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    const target = (event.target as HTMLElement) || null;
    if (!target) {
      return;
    }
    if (target.closest('.traffic-dot')) {
      return;
    }
    const header = target.closest('.window-header');
    if (!header) {
      return;
    }
    const windowEl = header.closest('.window') as HTMLElement | null;
    if (!windowEl || !windowEl.classList.contains('visible')) {
      return;
    }
    event.preventDefault();
    this.beginManualDrag(windowEl, event);
  };

  private handleManualPointerMove = (event: PointerEvent) => {
    if (!this.manualInteractionEnabled || !this.manualDragState) {
      return;
    }
    if (event.pointerId !== this.manualDragState.pointerId) {
      return;
    }
    event.preventDefault();
    this.updateManualDragPosition(event);
  };

  private handleManualPointerUp = (event: PointerEvent) => {
    if (!this.manualDragState || event.pointerId !== this.manualDragState.pointerId) {
      return;
    }
    const { target, pointerId } = this.manualDragState;
    if (target && target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
    target?.classList.remove('manual-dragging');
    this.manualDragState = null;
  };

  private handleManualCloseClick = (event: MouseEvent) => {
    if (!this.manualInteractionEnabled) {
      return;
    }
    const target = (event.target as HTMLElement) || null;
    if (!target) {
      return;
    }
    const closeDot = target.closest('.traffic-dot.red');
    if (!closeDot) {
      return;
    }
    const windowEl = closeDot.closest('.window') as HTMLElement | null;
    if (!windowEl) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    this.manualPositions.delete(windowEl);
    this.cancelManualDrag();
    this.closeWindow(windowEl, { animate: true });
    this.windowOrder = this.windowOrder.filter(
      (win) => win && win !== windowEl && win.classList.contains('visible')
    );
    if (windowEl === this.elements.terminalWindow) {
      this.activeTerminalSessionId = null;
      return;
    }
    if (windowEl.dataset.extractId) {
      this.purgeExtractionWindowByElement(windowEl);
    }
  };

  private async movePointerToApp(appId: string) {
    this.refreshScreenRect();
    const icon = this.appIcons.get(appId);
    if (icon) {
      await this.movePointerToElement(icon);
    } else {
      await this.movePointerToDesktop();
    }
  }

  private async movePointerToDesktop() {
    this.refreshScreenRect();
    return this.movePointerToElement(this.elements.desktopGrid, {
      offsetX: 160,
      offsetY: 120,
      duration: 700
    });
  }

  private async movePointerToElement(
    target: Element | null,
    options: { offsetX?: number; offsetY?: number; duration?: number } = {}
  ) {
    if (!target) {
      return;
    }
    this.refreshScreenRect();
    if (this.elements.screen.clientWidth < 1 || this.elements.screen.clientHeight < 1) {
      return;
    }
    this.raiseWindowForTarget(target);
    if (!this.progressBubbleBase) {
      this.dismissBubble(true);
    } else {
      progressDebug('movePointer:preserve-progress', {
        target:
          target instanceof HTMLElement ? target.className || target.tagName : target?.toString()
      });
    }
    const { offsetX = 0, offsetY = 0, duration = 900 } = options;
    const rect = target.getBoundingClientRect();
    if (rect.width < 1 && rect.height < 1) {
      return;
    }
    const desiredX = rect.left - this.screenRect.left + rect.width / 2 + offsetX;
    const desiredY = rect.top - this.screenRect.top + rect.height / 2 + offsetY;
    const pointerX = desiredX - POINTER_TIP_OFFSET.x;
    const pointerY = desiredY - POINTER_TIP_OFFSET.y;
    this.updatePointerTransform(pointerX, pointerY, duration);
    await sleep(duration + 60);
  }

  private isWindowVisible(win: HTMLElement | null) {
    return !!win && win.classList.contains('visible');
  }

  private async simulateResultBrowsing(cycles = 2) {
    const list = this.elements.browserResults;
    if (!list) {
      return;
    }
    const maxScroll = list.scrollHeight - list.clientHeight;
    if (maxScroll <= 0) {
      return;
    }
    const offset = Math.max(12, list.clientHeight / 2 - 14);
    const duration = 520;
    const rect = list.getBoundingClientRect();
    const centerX = rect.left - this.screenRect.left + rect.width / 2 - POINTER_TIP_OFFSET.x;
    const startY = rect.top - this.screenRect.top + rect.height / 2 + offset - POINTER_TIP_OFFSET.y;
    const endY = rect.top - this.screenRect.top + rect.height / 2 - offset - POINTER_TIP_OFFSET.y;
    for (let i = 0; i < cycles; i += 1) {
      list.scrollTop = maxScroll;
      this.updatePointerTransform(centerX, startY, 260);
      await sleep(280);
      const steps = 8;
      for (let s = 1; s <= steps; s += 1) {
        const t = s / steps;
        const y = startY + (endY - startY) * t;
        list.scrollTop = maxScroll * (1 - t);
        this.updatePointerTransform(centerX, y, 0);
        await sleep(duration / steps);
      }
      await sleep(140);
    }
    this.updatePointerTransform(centerX, endY, 220);
    await sleep(240);
  }

  private async click(options: { count?: number; interval?: number; right?: boolean } = {}) {
    const { count = 1, interval = 130, right = false } = options;
    if (!this.progressBubbleBase) {
      this.dismissBubble(true);
    } else {
      progressDebug('click:preserve-progress', { count, right });
    }
    for (let i = 0; i < count; i += 1) {
      this.triggerClickEffect(right);
      await sleep(interval);
    }
  }

  private triggerClickEffect(right = false) {
    const tip = this.getPointerTip();
    const circle = document.createElement('span');
    circle.className = `click-effect${right ? ' right' : ''}`;
    circle.style.left = `${tip.x - 9}px`;
    circle.style.top = `${tip.y - 9}px`;
    this.elements.screen.appendChild(circle);
    setTimeout(() => circle.remove(), 450);
  }

  private getPointerTip() {
    return {
      x: this.pointerBase.x + POINTER_TIP_OFFSET.x,
      y: this.pointerBase.y + POINTER_TIP_OFFSET.y
    };
  }

  private flushPendingPointerTransform() {
    if (!this.pendingPointerTransform) {
      return;
    }
    const pending = this.pendingPointerTransform;
    this.pendingPointerTransform = null;
    this.updatePointerTransform(pending.x, pending.y, pending.duration);
  }

  private updatePointerTransform(x: number, y: number, duration = 0) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return;
    }
    const screenWidth = this.elements.screen.clientWidth || this.screenRect?.width || 0;
    const screenHeight = this.elements.screen.clientHeight || this.screenRect?.height || 0;
    if (screenWidth < 1 || screenHeight < 1) {
      return;
    }
    const clampedX = Math.min(Math.max(0, x), Math.max(0, screenWidth - 10));
    const clampedY = Math.min(Math.max(0, y), Math.max(0, screenHeight - 10));
    this.elements.mousePointer.style.setProperty('--mouse-duration', `${Math.max(duration, 0)}ms`);
    this.elements.mousePointer.style.transform = `translate3d(${clampedX}px, ${clampedY}px, 0)`;
    this.pointerBase = { x: clampedX, y: clampedY };
  }

  private clampToScreen(x: number, y: number, width: number, height: number, padding = 12) {
    const maxX = this.elements.screen.clientWidth - width - padding;
    const maxY = this.elements.screen.clientHeight - height - padding;
    return {
      x: Math.min(Math.max(padding, x), Math.max(padding, maxX)),
      y: Math.min(Math.max(padding, y), Math.max(padding, maxY))
    };
  }

  private setupScreenObserver() {
    if (typeof ResizeObserver === 'undefined') {
      return;
    }
    this.screenObserver = new ResizeObserver((entries) => {
      const entry = Array.isArray(entries) ? entries[0] : null;
      const width = entry?.contentRect?.width || this.elements.screen.clientWidth;
      const height = entry?.contentRect?.height || this.elements.screen.clientHeight;
      if (width < 1 || height < 1) {
        return;
      }
      this.refreshScreenRect();
      this.flushPendingPointerTransform();
    });
    this.screenObserver.observe(this.elements.screen);
    this.destroyFns.push(() => this.screenObserver?.disconnect());
  }

  private showWindow(el: HTMLElement) {
    el.classList.remove('closing');
    el.classList.add('visible');
    const anchor = this.windowAnchors.get(el);
    if (anchor) {
      this.positionWindow(el, anchor);
    }
    this.pushWindowToStack(el);
    if (el === this.elements.folderWindow) {
      this.refreshFolderIconStates();
    }
  }

  private pushWindowToStack(el: HTMLElement) {
    if (!el) {
      return;
    }
    this.windowOrder = this.windowOrder.filter(
      (win) => win && win !== el && win.classList.contains('visible')
    );
    this.windowOrder.push(el);
    this.enforceWindowLimit();
    this.updateWindowZIndices();
  }

  private enforceWindowLimit() {
    if (!this.windowOrder.length) {
      return;
    }
    this.windowOrder = this.windowOrder.filter((win) => win && win.classList.contains('visible'));
    while (this.windowOrder.length > this.maxVisibleWindows) {
      const stale = this.windowOrder.shift();
      if (stale) {
        this.closeWindow(stale, { animate: true });
        this.purgeExtractionWindowByElement(stale);
      }
    }
    this.updateWindowZIndices();
  }

  private updateWindowZIndices() {
    const baseZ = 30;
    this.windowOrder.forEach((win, index) => {
      if (win && win.classList.contains('visible')) {
        win.style.zIndex = String(baseZ + index);
      }
    });
  }

  private closeWindow(el: HTMLElement | null, options: { animate?: boolean } = {}) {
    if (!el) {
      return;
    }
    const isFolderWindow = el === this.elements.folderWindow;
    if (isFolderWindow) {
      this.activeFolder = null;
    }
    this.windowOrder = this.windowOrder.filter((win) => win && win !== el);
    const animate = options.animate ?? false;
    if (!animate) {
      el.classList.remove('visible', 'closing');
      this.updateWindowZIndices();
      if (isFolderWindow) {
        this.refreshFolderIconStates();
      }
      return;
    }
    if (el.classList.contains('closing')) {
      return;
    }
    el.classList.add('closing');
    setTimeout(() => {
      el.classList.remove('visible', 'closing');
      this.updateWindowZIndices();
      if (isFolderWindow) {
        this.refreshFolderIconStates();
      }
    }, 320);
  }

  private raiseWindowForTarget(target: Element | null) {
    const ancestor = this.findWindowAncestor(target);
    if (ancestor) {
      this.windowOrder = this.windowOrder.filter(
        (win) => win && win !== ancestor && win.classList.contains('visible')
      );
      this.windowOrder.push(ancestor);
      this.enforceWindowLimit();
      this.updateWindowZIndices();
    }
  }

  private findWindowAncestor(target: Element | null): HTMLElement | null {
    let node: Element | null = target;
    while (node && node !== this.elements.screen) {
      if (node instanceof HTMLElement && node.classList.contains('window')) {
        return node;
      }
      node = node.parentElement;
    }
    return null;
  }

  private showContextMenu(type: ContextMenuType, coords?: { x: number; y: number }) {
    const map: Record<ContextMenuType, HTMLElement> = {
      desktop: this.elements.desktopMenu,
      folder: this.elements.folderMenu,
      file: this.elements.fileMenu,
      browser: this.elements.browserMenu,
      terminal: this.elements.terminalMenu,
      focus: this.elements.focusMenu
    };
    const menu = map[type];
    if (!menu) {
      return;
    }
    menu.classList.add('visible');
    const tip = this.getPointerTip();
    const desiredX = typeof coords?.x === 'number' ? coords.x : tip.x + 16;
    const desiredY = typeof coords?.y === 'number' ? coords.y : tip.y + 16;
    const { x, y } = this.clampToScreen(desiredX, desiredY, menu.offsetWidth, menu.offsetHeight);
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    monitorLifecycleDebug('contextMenu:show', { type, x, y, desiredX, desiredY });
  }

  private hideContextMenus() {
    [
      this.elements.desktopMenu,
      this.elements.folderMenu,
      this.elements.fileMenu,
      this.elements.focusMenu,
      this.elements.browserMenu,
      this.elements.terminalMenu
    ].forEach((menu) => {
      menu.classList.remove('visible');
      menu.querySelectorAll('button').forEach((btn) => btn.classList.remove('active'));
    });
    this.hideSecondaryMenu();
    this.hideWaitOverlay();
    this.terminalContextSessionId = null;
  }

  private hideSecondaryMenu() {
    if (this.secondaryMenu && this.secondaryMenu.parentElement) {
      this.secondaryMenu.parentElement.removeChild(this.secondaryMenu);
    }
    this.secondaryMenu = null;
  }

  private showSecondaryMenu(
    anchorMenu: HTMLElement | null,
    items: Array<{ label: string; action: string }>
  ) {
    this.hideSecondaryMenu();
    if (!anchorMenu || !items.length) {
      return null;
    }
    const menu = document.createElement('div');
    menu.className = 'context-menu secondary-menu visible';
    items.forEach((item) => {
      const btn = document.createElement('button');
      btn.textContent = item.label;
      btn.dataset.action = item.action;
      menu.appendChild(btn);
    });
    this.elements.screen.appendChild(menu);
    const rect = anchorMenu.getBoundingClientRect();
    const left = rect.right - this.screenRect.left + 10;
    const top = rect.top - this.screenRect.top + 4;
    const { x, y } = this.clampToScreen(
      left,
      top,
      menu.offsetWidth || 180,
      menu.offsetHeight || 60
    );
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    this.secondaryMenu = menu;
    monitorLifecycleDebug('secondaryMenu:show', {
      items: items.map((item) => item.action),
      anchorRect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      pos: { x, y }
    });
    return menu;
  }

  private async chooseSecondaryMenuAction(action: string) {
    const btn =
      this.secondaryMenu?.querySelector<HTMLButtonElement>(`button[data-action="${action}"]`) ||
      null;
    if (!btn) {
      monitorLifecycleDebug('secondaryMenu:missing-btn', { action, hasMenu: !!this.secondaryMenu });
      return false;
    }
    monitorLifecycleDebug('secondaryMenu:choose', {
      action,
      rect: btn.getBoundingClientRect().toJSON
        ? btn.getBoundingClientRect().toJSON()
        : btn.getBoundingClientRect()
    });
    await this.movePointerToElement(btn, { duration: 320 });
    btn.classList.add('active');
    await sleep(200);
    btn.classList.remove('active');
    await this.click();
    await sleep(140);
    this.hideSecondaryMenu();
    return true;
  }

  private async highlightMenu(type: ContextMenuType, action: string): Promise<boolean> {
    const map: Record<ContextMenuType, HTMLElement> = {
      desktop: this.elements.desktopMenu,
      folder: this.elements.folderMenu,
      file: this.elements.fileMenu,
      browser: this.elements.browserMenu,
      terminal: this.elements.terminalMenu,
      focus: this.elements.focusMenu
    };
    const menu = map[type];
    const btn = menu?.querySelector<HTMLButtonElement>(`button[data-action="${action}"]`);
    if (!btn) {
      return false;
    }
    await this.movePointerToElement(btn, { duration: 360 });
    btn.classList.add('active');
    await sleep(240);
    btn.classList.remove('active');
    return true;
  }

  /**
   * 打开终端上下文菜单，确保出现右键动作和弹出二级菜单
   */
  private async openTerminalContextMenu(sessionId: string) {
    const tabEl = this.getTabElement(sessionId);
    let coords: { x: number; y: number } | undefined;
    if (tabEl) {
      terminalMenuDebug('openContextMenu:move-to-tab', { sessionId, tabText: tabEl.textContent });
      await this.movePointerToElement(tabEl, { duration: 420 });
      await this.click({ right: true });
      const rect = tabEl.getBoundingClientRect();
      coords = {
        x: rect.left - this.screenRect.left + rect.width / 2,
        y: rect.top - this.screenRect.top + rect.height / 2
      };
      terminalMenuDebug('openContextMenu:tab-rect', { sessionId, rect, coords });
    } else {
      terminalMenuDebug('openContextMenu:no-tab', { sessionId });
      await this.movePointerToElement(this.elements.terminalWindow, {
        offsetX: 18,
        offsetY: 0,
        duration: 420
      });
      await this.click({ right: true });
    }
    this.terminalContextSessionId = sessionId;
    this.showContextMenu('terminal', coords);
    const visible = await this.waitForMenuVisible(this.elements.terminalMenu);
    terminalMenuDebug('openContextMenu:visible', { sessionId, visible });
    // 右键后立即把指针轻推到菜单中心，减少后续对准偏差
    if (visible && this.elements.terminalMenu) {
      this.snapPointerToElement(this.elements.terminalMenu);
    }
  }

  /**
   * 选中终端菜单项，若高亮失败则回退到菜单中心点击，保证动画流程完整
   */
  private async chooseTerminalMenuAction(action: TerminalMenuAction) {
    terminalMenuDebug('chooseMenuAction:start', { action });
    const menu = this.elements.terminalMenu;
    const btn = menu?.querySelector<HTMLButtonElement>(`button[data-action="${action}"]`) || null;
    if (btn) {
      await this.waitForMenuVisible(menu);
      await this.movePointerToElement(btn, { duration: 320 });
      this.snapPointerToElement(btn);
      btn.classList.add('active');
      await sleep(200);
      btn.classList.remove('active');
      terminalMenuDebug('chooseMenuAction:target', {
        action,
        rect: btn.getBoundingClientRect().toJSON
          ? btn.getBoundingClientRect().toJSON()
          : btn.getBoundingClientRect()
      });
    } else {
      const highlighted = await this.highlightMenu('terminal', action);
      terminalMenuDebug('chooseMenuAction:highlight', { action, highlighted });
      if (!highlighted && menu) {
        await this.movePointerToElement(menu, { duration: 260 });
        this.snapPointerToElement(menu);
        terminalMenuDebug('chooseMenuAction:fallback-move', { action });
      }
    }
    await this.click();
    await sleep(160);
    this.hideContextMenus();
    terminalMenuDebug('chooseMenuAction:done', { action });
  }

  private async waitForMenuVisible(menu: HTMLElement | null, timeout = 240) {
    if (!menu) {
      return false;
    }
    const start = performance.now();
    while (!menu.classList.contains('visible') && performance.now() - start < timeout) {
      await sleep(16);
    }
    monitorLifecycleDebug('menu:visible-check', {
      hasMenu: !!menu,
      visible: menu.classList.contains('visible')
    });
    return menu.classList.contains('visible');
  }

  private hideWaitOverlay() {
    if (!this.elements.waitOverlay) return;
    this.elements.waitOverlay.classList.remove('active');
    if (this.waitOverlayTimer) {
      clearInterval(this.waitOverlayTimer);
      this.waitOverlayTimer = null;
    }
  }

  private formatWaitText(seconds: number) {
    if (seconds >= 60) {
      const m = Math.floor(seconds / 60)
        .toString()
        .padStart(2, '0');
      const s = (seconds % 60).toString().padStart(2, '0');
      return `${m}:${s}`;
    }
    return `${seconds}s`;
  }

  private async playWaitCountdown(durationSeconds = 5, until?: Promise<any>) {
    const total = Math.max(1, Math.round(durationSeconds));
    if (!this.elements.waitOverlay || !this.elements.waitCountdown) {
      return;
    }
    const tip = this.getPointerTip();
    this.elements.waitOverlay.style.left = `${tip.x + 16}px`;
    this.elements.waitOverlay.style.top = `${tip.y - 28}px`;
    const start = performance.now();
    this.elements.waitCountdown.textContent = this.formatWaitText(total);
    this.elements.waitOverlay.classList.add('active');
    if (this.waitOverlayTimer) {
      clearInterval(this.waitOverlayTimer);
      this.waitOverlayTimer = null;
    }
    let phase = 0;
    const update = () => {
      const elapsed = (performance.now() - start) / 1000;
      const remain = total - elapsed;
      if (!this.elements.waitCountdown) {
        return;
      }
      if (remain > 0) {
        this.elements.waitCountdown.textContent = this.formatWaitText(Math.ceil(remain));
      } else {
        const over = Math.ceil(-remain);
        const dots = '.'.repeat(phase);
        phase = (phase + 1) % 4;
        this.elements.waitCountdown.textContent = t('monitor.waitOverrun', { n: over, dots });
      }
    };
    this.waitOverlayTimer = window.setInterval(update, 260);
    const done = until || Promise.resolve();
    const minDuration = new Promise((resolve) => setTimeout(resolve, total * 1000));
    try {
      await Promise.all([done.catch(() => null), minDuration]);
    } finally {
      update();
      this.hideWaitOverlay();
    }
  }

  /**
   * 将指针瞬时对准目标元素的中心，防止动画收尾时产生偏移
   */
  private snapPointerToElement(target: Element | null, offsetX = 0, offsetY = 0) {
    if (!target) {
      return;
    }
    const rect = target.getBoundingClientRect();
    const desiredX = rect.left - this.screenRect.left + rect.width / 2 + offsetX;
    const desiredY = rect.top - this.screenRect.top + rect.height / 2 + offsetY;
    const pointerX = desiredX - POINTER_TIP_OFFSET.x;
    const pointerY = desiredY - POINTER_TIP_OFFSET.y;
    this.updatePointerTransform(pointerX, pointerY, 0);
  }

  private spawnDesktopFolder(name: string) {
    const icon = this.createDesktopFolderIcon(name);
    icon.classList.add('temporary-shortcut');
    this.elements.desktopGrid.appendChild(icon);
    requestAnimationFrame(() => icon.classList.add('visible'));
    this.folderIcons.set(name, icon);
    this.pendingDesktopFolders.delete(name);
    if (!this.folderEntries.has(name)) {
      this.folderEntries.set(name, []);
    }
    return icon;
  }

  private async waitForDesktopFolderIcon(name: string, timeout = 1600) {
    const start = Date.now();
    let icon = this.folderIcons.get(name) || null;
    while (!icon && Date.now() - start < timeout) {
      await sleep(80);
      icon = this.folderIcons.get(name) || null;
    }
    return icon;
  }

  private async revealDesktopFolderIcon(name: string, options: { fallbackSpawn?: boolean } = {}) {
    let icon = this.folderIcons.get(name) || null;
    if (!icon) {
      icon = await this.waitForDesktopFolderIcon(name);
    }
    if (!icon && options.fallbackSpawn) {
      icon = this.spawnDesktopFolder(name);
    }
    if (!icon) {
      return null;
    }
    this.pendingDesktopFolders.delete(name);
    icon.classList.remove('pending-reveal');
    this.renderDesktopFolders();
    if (!icon.classList.contains('visible')) {
      requestAnimationFrame(() => icon.classList.add('visible'));
    }
    return icon;
  }

  private spawnDesktopFile(name: string) {
    const div = document.createElement('div');
    div.className = 'desktop-icon app';
    const img = document.createElement('img');
    img.src = this.assets.fileIcon;
    img.alt = name;
    div.appendChild(img);
    const span = document.createElement('span');
    span.textContent = name;
    div.appendChild(span);
    div.classList.add('temporary-shortcut');
    div.style.opacity = '0';
    this.elements.desktopGrid.appendChild(div);
    requestAnimationFrame(() => {
      div.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      div.style.opacity = '1';
      div.style.transform = 'translateY(-6px)';
    });
    this.fileIcons.set(name, div);
    return div;
  }

  private async openFolder(folderKey: string, label?: string) {
    if (!folderKey) {
      return;
    }
    await this.loadFolderEntries(folderKey);
    this.activeFolder = folderKey;
    this.showWindow(this.elements.folderWindow);
    this.elements.folderHeaderText.textContent = label || folderKey || 'workspace';
    this.renderFolderEntries(folderKey);
    this.refreshFolderIconStates();
  }

  private setFolderIconState(folderName: string) {
    if (!folderName) {
      return;
    }
    const icon = this.folderIcons.get(folderName);
    if (!icon) {
      return;
    }
    const img = icon.querySelector('img');
    if (!img) {
      return;
    }
    // 简化：统一使用单一图标
    img.src = this.assets.folderIcon;
  }

  private removeFolderEntry(folderName: string, entryName: string) {
    const list = this.folderEntries.get(folderName);
    if (!list) {
      return;
    }
    const entry = list.find((item) => item.name === entryName) || null;
    const next = list.filter((item) => item.name !== entryName);
    this.folderEntries.set(folderName, next);
    if (entry?.type === 'folder') {
      const key = this.composePath([folderName, entryName].filter(Boolean));
      if (key && this.folderEntries.has(key)) {
        this.folderEntries.delete(key);
      }
    }
    if (this.activeFolder === folderName) {
      this.renderFolderEntries(folderName, false);
    }
  }

  private closeFolder() {
    this.activeFolder = null;
    this.elements.folderWindow.classList.remove('visible');
    this.refreshFolderIconStates();
  }

  private renameFolderEntry(
    folderName: string,
    fromName: string,
    toName: string,
    options: { skipRender?: boolean } = {}
  ) {
    const entries = this.folderEntries.get(folderName);
    if (!entries) {
      return;
    }
    const entry = entries.find((item) => item.name === fromName);
    if (entry) {
      entry.name = toName;
      entry.path = this.composePath([folderName, toName].filter(Boolean));
      if (entry.type === 'folder') {
        const oldKey = this.composePath([folderName, fromName].filter(Boolean));
        const newKey = this.composePath([folderName, toName].filter(Boolean));
        if (oldKey && newKey && this.folderEntries.has(oldKey)) {
          const childEntries = this.folderEntries.get(oldKey) || [];
          this.folderEntries.delete(oldKey);
          this.folderEntries.set(newKey, childEntries);
        }
      }
    }
    this.folderEntries.set(folderName, entries);
    if (!options.skipRender && this.activeFolder === folderName) {
      this.renderFolderEntries(folderName, false);
    }
  }

  private renameDesktopEntry(icon: HTMLElement, newName: string) {
    const span = icon.querySelector('span');
    if (span) {
      span.textContent = newName;
    }
  }

  private async animateRenameLabel(targetEl: HTMLElement | null, newName: string) {
    if (!targetEl) {
      return;
    }
    const span = targetEl.querySelector('span');
    if (!span) {
      return;
    }
    const current = span.textContent || '';
    if (current === newName) {
      return;
    }
    renameDebug('animate:start', { current, newName });
    for (let i = current.length; i > 0; i -= 1) {
      span.textContent = current.slice(0, i - 1);
      await sleep(RENAME_ERASE_INTERVAL);
    }
    for (let i = 0; i < newName.length; i += 1) {
      span.textContent = newName.slice(0, i + 1);
      await sleep(RENAME_TYPE_INTERVAL);
    }
    span.textContent = newName;
    renameDebug('animate:end', { final: span.textContent });
  }

  private renameDesktopRoot(
    fromName: string,
    toName: string,
    options: { skipRender?: boolean } = {}
  ) {
    if (!fromName || !toName || fromName === toName) {
      return;
    }
    const icon = this.folderIcons.get(fromName) || null;
    if (icon) {
      this.folderIcons.delete(fromName);
      this.folderIcons.set(toName, icon);
      icon.dataset.folderName = toName;
      this.renameDesktopEntry(icon, toName);
    }
    const updatedEntries = new Map<string, FolderEntry[]>();
    this.folderEntries.forEach((entries, key) => {
      if (key === fromName || key.startsWith(`${fromName}/`)) {
        const newKey = toName + key.slice(fromName.length);
        const mapped = entries.map((item) => {
          const nextPath = item.path.startsWith(fromName)
            ? toName + item.path.slice(fromName.length)
            : item.path;
          return { ...item, path: nextPath };
        });
        updatedEntries.set(newKey, mapped);
      } else {
        updatedEntries.set(key, entries);
      }
    });
    this.folderEntries = updatedEntries;
    const rootIdx = this.desktopRoots.indexOf(fromName);
    if (rootIdx >= 0) {
      this.desktopRoots[rootIdx] = toName;
    }
    if (this.activeFolder) {
      if (this.activeFolder === fromName) {
        this.activeFolder = toName;
      } else if (this.activeFolder.startsWith(`${fromName}/`)) {
        this.activeFolder = toName + this.activeFolder.slice(fromName.length);
      }
    }
    if (!options.skipRender) {
      this.renderDesktopFolders();
      this.refreshFolderIconStates();
    }
  }

  private openEditorWindow(filename: string) {
    this.showWindow(this.elements.editorWindow);
    this.elements.editorHeaderText.textContent = filename;
    this.elements.editorBody.innerHTML = '';
    this.editorScene.lines = [];
    this.editorScene.placeholder = false;
    this.elements.editorBody.scrollTo({ top: 0 });
  }

  private renderEditorPlaceholder(message: string) {
    this.elements.editorBody.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'code-line placeholder visible';
    div.textContent = message;
    this.elements.editorBody.appendChild(div);
    this.editorScene.lines = [];
    this.editorScene.placeholder = true;
    this.elements.editorBody.scrollTo({ top: 0 });
  }

  private async fetchEditorFileLines(path?: string | null): Promise<string[] | null> {
    const targetPath = (path || '').trim();
    if (!targetPath) {
      return null;
    }
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = controller ? window.setTimeout(() => controller.abort(), 8000) : null;
    try {
      const response = await fetch(`/api/gui/files/text?path=${encodeURIComponent(targetPath)}`, {
        method: 'GET',
        credentials: 'include',
        headers: {
          Accept: 'application/json'
        },
        signal: controller?.signal
      });
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      if (!response.ok) {
        editorDebug('fetchFileLines:http-error', { path: targetPath, status: response.status });
        return null;
      }
      const data = await response.json().catch(() => null);
      if (!data) {
        return null;
      }
      const content =
        typeof data?.content === 'string'
          ? data.content
          : typeof data?.data?.content === 'string'
            ? data.data.content
            : null;
      if (typeof content !== 'string') {
        editorDebug('fetchFileLines:empty', { path: targetPath });
        return null;
      }
      const normalized = this.normalizeLines(content);
      editorDebug('fetchFileLines:resolved', { path: targetPath, length: normalized.length });
      return normalized;
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        editorDebug('fetchFileLines:aborted', { path: targetPath });
      } else {
        console.warn('[MonitorDirector] fetchEditorFileLines failed', error);
      }
      return null;
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }
  }

  private async fetchMonitorSnapshotById(
    executionId?: string | number | null,
    stage: 'before' | 'after' = 'before'
  ): Promise<string[] | null> {
    const key = typeof executionId === 'number' ? String(executionId) : (executionId || '').trim();
    if (!key) {
      return null;
    }
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const timeoutId = controller ? window.setTimeout(() => controller.abort(), 6000) : null;
    try {
      const response = await fetch(
        `/api/gui/monitor_snapshot?executionId=${encodeURIComponent(key)}&stage=${stage}`,
        {
          method: 'GET',
          credentials: 'include',
          headers: { Accept: 'application/json' },
          signal: controller?.signal
        }
      );
      if (!response.ok) {
        editorDebug('snapshotFetch:http-error', {
          executionId: key,
          stage,
          status: response.status
        });
        return null;
      }
      const data = await response.json().catch(() => null);
      if (!data?.success || !data?.snapshot) {
        editorDebug('snapshotFetch:empty', { executionId: key, stage });
        return null;
      }
      const rawContent =
        typeof data.snapshot?.content === 'string'
          ? data.snapshot.content
          : typeof data.snapshot?.text === 'string'
            ? data.snapshot.text
            : Array.isArray(data.snapshot?.lines)
              ? data.snapshot.lines
              : null;
      if (rawContent === null) {
        editorDebug('snapshotFetch:no-content', { executionId: key, stage });
        return null;
      }
      return this.normalizeLines(rawContent);
    } catch (error: any) {
      if (error?.name === 'AbortError') {
        editorDebug('snapshotFetch:aborted', { executionId: key, stage });
      } else {
        console.warn('[MonitorDirector] fetchMonitorSnapshot failed', error);
      }
      return null;
    } finally {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
    }
  }

  private prepareEditorScene(lines: any) {
    const normalized = this.sanitizeEditorLines(lines);
    editorDebug('prepareScene', { normalizedLength: normalized.length });
    if (!normalized.length) {
      this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      return;
    }
    this.renderEditorSnapshot(normalized);
  }

  private sanitizeEditorLines(lines: any): string[] {
    const normalized = this.normalizeLines(lines);
    if (!normalized.length) {
      return [];
    }
    return normalized.slice(0, EDITOR_MAX_RENDER_LINES);
  }

  private renderEditorSnapshot(lines: string[]) {
    const normalized = this.sanitizeEditorLines(lines);
    if (!normalized.length) {
      this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      return;
    }
    this.editorScene.lines = normalized.slice();
    this.editorScene.placeholder = false;
    const container = this.elements.editorBody;
    container.innerHTML = '';
    const fragment = document.createDocumentFragment();
    normalized.forEach((text, index) => {
      fragment.appendChild(this.buildEditorLineElement(text, index));
    });
    container.appendChild(fragment);
    container.scrollTo({ top: 0 });
    this.syncEditorIndices();
    requestAnimationFrame(() => {
      const nodes = container.querySelectorAll('.code-line');
      nodes.forEach((node, index) => {
        setTimeout(() => node.classList.add('visible'), Math.min(index, 10) * 30);
      });
    });
  }

  private buildEditorLineElement(
    text: string,
    index: number,
    extraClass?: string,
    options?: { prefill?: boolean }
  ) {
    const row = document.createElement('div');
    const classes = ['code-line'];
    if (extraClass) {
      classes.push(extraClass);
    }
    row.className = classes.join(' ');
    row.dataset.index = String(index);
    const shouldPrefill = options?.prefill !== false;
    row.textContent = shouldPrefill ? text || ' ' : '';
    return row;
  }

  private syncEditorIndices() {
    Array.from(this.elements.editorBody.children).forEach((node, index) => {
      if (node instanceof HTMLElement) {
        node.dataset.index = String(index);
      }
    });
  }

  private async focusEditorLine(index: number) {
    const container = this.elements.editorBody;
    if (!container) {
      return;
    }
    const target = container.children[index] as HTMLElement | undefined;
    if (!target) {
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const delta =
      targetRect.top + targetRect.height / 2 - (containerRect.top + containerRect.height / 2);
    const travel = Math.abs(delta);
    const nextTop = Math.max(0, container.scrollTop + delta);
    const behavior = travel < 24 ? 'auto' : 'smooth';
    try {
      container.scrollTo({ top: nextTop, behavior });
    } catch (error) {
      container.scrollTop = nextTop;
    }
    const waitDuration = Math.min(700, Math.max(220, travel * 0.8));
    await sleep(waitDuration);
  }

  private async animateEditorTransition(nextLines: any) {
    const currentLines = this.editorScene.lines.slice();
    const targetLines = this.sanitizeEditorLines(nextLines);
    const lineCount = targetLines.length;
    editorDebug('animate:start', {
      currentLines: currentLines.length,
      targetLines: lineCount
    });
    if (!currentLines.length && !targetLines.length) {
      editorDebug('animate:both-empty');
      this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      return;
    }
    // 规则：
    // - > 30 行：直接瞬间渲染
    if (lineCount > 30) {
      editorDebug('animate:skip-gt-30', { lineCount });
      this.renderEditorSnapshot(targetLines);
      return;
    }
    // - 9~30 行：逐行动画（不逐字符）
    if (lineCount > 8) {
      editorDebug('animate:line-fill', { lineCount });
      await this.animateEditorLineFill(targetLines);
      return;
    }
    // - ≤8 行：保留原逐字符/行内阈值逻辑
    if (currentLines.length > EDITOR_DIFF_LIMIT || targetLines.length > EDITOR_DIFF_LIMIT) {
      editorDebug('animate:skip-large', {
        currentLines: currentLines.length,
        targetLines: targetLines.length
      });
      this.renderEditorSnapshot(targetLines);
      return;
    }
    const operations = this.buildEditorDiff(currentLines, targetLines);
    const mergedOperations = this.mergeEditorOperations(operations);
    // 根据本次动画的总改动量动态提升速度：改动越大，动画越快
    const totalInsertedChars = mergedOperations.reduce((sum, op) => {
      if (op.type === 'delete') {
        return sum;
      }
      return sum + (op.text?.length || 0);
    }, 0);
    const totalOps = mergedOperations.length;
    this.editorSpeedBoost = this.computeEditorSpeedBoost(totalInsertedChars, totalOps);
    editorDebug('animate:operations', { count: mergedOperations.length });
    if (!mergedOperations.length) {
      if (!targetLines.length) {
        this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      }
      editorDebug('animate:no-change');
      return;
    }
    const limitedOps = mergedOperations.slice(0, EDITOR_MAX_ANIMATION_STEPS);
    for (const op of limitedOps) {
      if (op.type === 'delete') {
        await this.animateEditorDeletion(op.index);
      } else if (op.type === 'replace') {
        await this.animateEditorReplacement(op.index, op.text || '');
      } else {
        await this.animateEditorInsertion(op.index, op.text || '');
      }
    }
    if (mergedOperations.length > EDITOR_MAX_ANIMATION_STEPS) {
      editorDebug('animate:exceed-limit', { total: mergedOperations.length });
      this.renderEditorSnapshot(targetLines);
      return;
    }
    if (!targetLines.length) {
      editorDebug('animate:target-empty');
      this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      return;
    }
    this.editorScene.lines = targetLines.slice();
    this.syncEditorIndices();
    editorDebug('animate:done', { finalLines: this.editorScene.lines.length });
    // 重置全局加速
    this.editorSpeedBoost = 1;
  }

  private async animateEditorLineFill(lines: string[]) {
    const normalized = this.sanitizeEditorLines(lines);
    if (!normalized.length) {
      this.renderEditorPlaceholder(t('monitor.editorEmpty'));
      return;
    }
    const container = this.elements.editorBody;
    container.innerHTML = '';
    // 先放空行占位，再逐行填充文本，避免逐字符动画
    normalized.forEach((_, index) => {
      const row = this.buildEditorLineElement('', index, undefined, { prefill: false });
      container.appendChild(row);
    });
    container.scrollTo({ top: 0 });
    const delay = 28;
    const rows = Array.from(container.children) as HTMLElement[];
    for (let i = 0; i < rows.length; i += 1) {
      const row = rows[i];
      row.textContent = normalized[i] || ' ';
      row.classList.add('visible');
      this.adjustEditorScrollForLine(row);
      await sleep(delay);
    }
    this.editorScene.lines = normalized.slice();
    this.editorScene.placeholder = false;
    this.syncEditorIndices();
    this.editorSpeedBoost = 1;
  }

  private buildEditorDiff(before: string[], after: string[]): EditorOperation[] {
    const m = before.length;
    const n = after.length;
    const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i -= 1) {
      for (let j = n - 1; j >= 0; j -= 1) {
        if (before[i] === after[j]) {
          dp[i][j] = dp[i + 1][j + 1] + 1;
        } else {
          dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
      }
    }
    const rawOps: Array<{ type: 'keep' | 'insert' | 'delete'; text?: string }> = [];
    let i = 0;
    let j = 0;
    while (i < m || j < n) {
      if (i < m && j < n && before[i] === after[j]) {
        rawOps.push({ type: 'keep', text: before[i] });
        i += 1;
        j += 1;
        continue;
      }
      if (j < n && (i === m || dp[i][j + 1] >= dp[i + 1][j])) {
        rawOps.push({ type: 'insert', text: after[j] });
        j += 1;
        continue;
      }
      if (i < m) {
        rawOps.push({ type: 'delete', text: before[i] });
        i += 1;
      }
    }
    const operations: EditorOperation[] = [];
    const working = before.slice();
    let pointer = 0;
    rawOps.forEach((op) => {
      if (op.type === 'keep') {
        pointer += 1;
        return;
      }
      if (op.type === 'insert') {
        const index = Math.min(pointer, working.length);
        operations.push({ type: 'insert', index, text: op.text || '' });
        working.splice(index, 0, op.text || '');
        pointer += 1;
        return;
      }
      if (op.type === 'delete') {
        const maxIndex = Math.max(working.length - 1, 0);
        const index = Math.min(pointer, maxIndex);
        operations.push({ type: 'delete', index });
        working.splice(index, 1);
      }
    });
    editorDebug('diff', {
      before: before.length,
      after: after.length,
      rawOps: rawOps.length,
      operations: operations.length
    });
    return operations;
  }

  private mergeEditorOperations(operations: EditorOperation[]): EditorOperation[] {
    const merged: EditorOperation[] = [];
    for (let i = 0; i < operations.length; i += 1) {
      const current = operations[i];
      const next = operations[i + 1];
      if (
        current.type === 'delete' &&
        next &&
        next.type === 'insert' &&
        next.index === current.index
      ) {
        merged.push({ type: 'replace', index: current.index, text: next.text || '' });
        i += 1;
        continue;
      }
      merged.push(current);
    }
    return merged;
  }

  private async animateEditorInsertion(index: number, text: string) {
    const container = this.elements.editorBody;
    if (this.editorScene.placeholder) {
      container.innerHTML = '';
      this.editorScene.placeholder = false;
    }
    const safeIndex = Math.max(0, Math.min(index, container.children.length));
    const safeText = typeof text === 'string' ? text : '';
    editorDebug('insert', { index: safeIndex, textPreview: safeText.slice(0, 40) });
    const row = this.buildEditorLineElement(safeText, safeIndex, 'diff-line', { prefill: false });
    const reference = container.children[safeIndex] || null;
    if (reference) {
      container.insertBefore(row, reference);
    } else {
      container.appendChild(row);
    }
    this.editorScene.lines.splice(safeIndex, 0, safeText);
    await this.focusEditorLine(safeIndex);
    await sleep(60);
    row.classList.add('visible');
    await this.typeLineText(row, safeText);
    row.classList.remove('diff-line');
    await sleep(80);
    this.syncEditorIndices();
    if (this.editorScene.lines.length > EDITOR_MAX_RENDER_LINES) {
      this.editorScene.lines = this.editorScene.lines.slice(0, EDITOR_MAX_RENDER_LINES);
      while (container.children.length > EDITOR_MAX_RENDER_LINES) {
        const lastEl = container.lastElementChild;
        if (!lastEl) {
          break;
        }
        container.removeChild(lastEl);
      }
      this.syncEditorIndices();
      editorDebug('insert:trim', { length: this.editorScene.lines.length });
    }
  }

  private async animateEditorDeletion(index: number) {
    const container = this.elements.editorBody;
    if (!container.children.length) {
      editorDebug('delete:empty-container');
      return;
    }
    const safeIndex = Math.max(0, Math.min(index, container.children.length - 1));
    editorDebug('delete', { index: safeIndex });
    const target = container.children[safeIndex] as HTMLElement | undefined;
    if (!target) {
      editorDebug('delete:missing-node');
      this.renderEditorSnapshot(this.editorScene.lines);
      return;
    }
    await this.focusEditorLine(safeIndex);
    target.classList.add('diff-delete');
    await this.eraseLineText(target);
    target.classList.add('removing');
    await sleep(120);
    target.remove();
    this.editorScene.lines.splice(safeIndex, 1);
    this.syncEditorIndices();
  }

  private async animateEditorReplacement(index: number, text: string) {
    const container = this.elements.editorBody;
    if (!container.children.length) {
      await this.animateEditorInsertion(index, text);
      return;
    }
    const safeIndex = Math.max(0, Math.min(index, container.children.length - 1));
    const row = container.children[safeIndex] as HTMLElement | undefined;
    if (!row) {
      await this.animateEditorInsertion(index, text);
      return;
    }
    const safeText = typeof text === 'string' ? text : '';
    await this.focusEditorLine(safeIndex);
    row.classList.add('diff-replace');
    await this.eraseLineText(row, { instant: false });
    await this.typeLineText(row, safeText);
    row.classList.remove('diff-replace');
    this.editorScene.lines[safeIndex] = safeText;
    this.syncEditorIndices();
  }

  private adjustEditorScrollForLine(target: HTMLElement) {
    const container = this.elements.editorBody;
    if (!container || !target) {
      return;
    }
    const padding = 18;
    const cRect = container.getBoundingClientRect();
    const tRect = target.getBoundingClientRect();
    let delta = 0;
    if (tRect.bottom > cRect.bottom - padding) {
      delta = tRect.bottom - (cRect.bottom - padding);
    } else if (tRect.top < cRect.top + padding) {
      delta = tRect.top - (cRect.top + padding);
    }
    if (delta !== 0) {
      const nextTop = Math.max(0, container.scrollTop + delta);
      try {
        container.scrollTo({ top: nextTop, behavior: 'auto' });
      } catch (error) {
        container.scrollTop = nextTop;
      }
    }
  }

  private async typeLineText(
    target: HTMLElement,
    text: string,
    options: { instant?: boolean } = {}
  ) {
    const normalized = text && text.length ? text : ' ';
    const shouldInstant =
      typeof options.instant === 'boolean'
        ? options.instant
        : normalized.length > EDITOR_TYPING_THRESHOLD;
    target.textContent = '';
    if (shouldInstant) {
      target.textContent = normalized;
      this.adjustEditorScrollForLine(target);
      await sleep(40);
      return;
    }
    // 行越长、整体改动越大 -> 动画越快
    const lineBoost = 1 + Math.min(normalized.length / 40, 6);
    const speed = Math.max(1, lineBoost * this.editorSpeedBoost);
    const interval = Math.max(4, Math.floor(EDITOR_TYPING_INTERVAL / speed));
    for (const char of normalized.split('')) {
      target.textContent = `${target.textContent || ''}${char}`;
      this.adjustEditorScrollForLine(target);
      await sleep(interval);
    }
  }

  private async eraseLineText(target: HTMLElement | null, options: { instant?: boolean } = {}) {
    if (!target) {
      return;
    }
    const current = target.textContent || '';
    if (!current.length) {
      await sleep(40);
      return;
    }
    const shouldInstant =
      typeof options.instant === 'boolean'
        ? options.instant
        : current.length > EDITOR_TYPING_THRESHOLD;
    if (shouldInstant) {
      target.textContent = '';
      await sleep(60);
      return;
    }
    const lineBoost = 1 + Math.min(current.length / 40, 6);
    const speed = Math.max(1, lineBoost * this.editorSpeedBoost);
    const interval = Math.max(3, Math.floor(EDITOR_ERASE_INTERVAL / speed));
    for (let i = current.length - 1; i >= 0; i -= 1) {
      target.textContent = current.slice(0, i);
      await sleep(interval);
    }
    target.textContent = '';
  }

  private resolveEditorBeforeLines(payload: any) {
    const args = payload?.argumentSnapshot || payload?.arguments || {};
    const result = payload?.result || {};
    return this.pickFirstNonEmptyLines([
      result.before,
      result.original,
      result.previous_content,
      args.existing_content,
      args.original_content,
      args.before
    ]);
  }

  private resolveEditorAfterLines(payload: any, completion?: any) {
    const completionResult = completion?.result || {};
    const result = payload?.result || {};
    const args = payload?.argumentSnapshot || payload?.arguments || {};
    return this.pickFirstNonEmptyLines([
      completionResult.after,
      completionResult.content,
      completionResult.text,
      completionResult.new_content,
      result.after,
      result.content,
      result.text,
      args.final_content,
      args.updated_content,
      args.content
    ]);
  }

  private pickFirstNonEmptyLines(candidates: any[]): string[] {
    for (const candidate of candidates) {
      const normalized = this.normalizeLines(candidate);
      if (normalized.length) {
        return normalized;
      }
    }
    return [];
  }

  private async typeEditorLines(lines: string[], options?: { highlight?: 'diff' }) {
    if (options?.highlight === 'diff') {
      await this.animateEditorTransition(lines);
      return;
    }
    this.prepareEditorScene(lines);
  }

  /**
   * 根据本次补丁的总体字符量与操作数，返回动画加速倍数。
   * - 插入/替换字符越多，加速越明显
   * - 操作数量越多（分块多），也进一步加速
   */
  private computeEditorSpeedBoost(totalChars: number, totalOps: number): number {
    const charBoost = Math.min(totalChars / 400, 4); // 0 ~ 4
    const opBoost = Math.min(totalOps / 120, 2); // 0 ~ 2
    return 1 + charBoost + opBoost;
  }

  private async typeSearchQuery(text: string) {
    this.elements.browserSearchText.textContent = '';
    for (const char of text.split('')) {
      this.elements.browserSearchText.textContent += char;
      await sleep(70);
    }
  }

  private normalizeUrl(raw?: string) {
    if (!raw || typeof raw !== 'string') {
      return '';
    }
    return raw
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, '')
      .replace(/\/$/, '');
  }

  private findSearchResultElement(url: string) {
    const normalized = this.normalizeUrl(url);
    if (!normalized) {
      return null;
    }
    if (this.browserResultMap.has(normalized)) {
      return this.browserResultMap.get(normalized) || null;
    }
    for (const [key, element] of this.browserResultMap.entries()) {
      if (key.includes(normalized) || normalized.includes(key)) {
        return element;
      }
    }
    return null;
  }

  private async focusSearchResultByUrl(url: string) {
    const element = this.findSearchResultElement(url);
    if (!element) {
      return null;
    }
    this.clearResultTargets();
    const container = this.elements.browserResults.parentElement as HTMLElement | null;
    if (container) {
      const containerRect = container.getBoundingClientRect();
      const elementRect = element.getBoundingClientRect();
      const padding = 12;
      let targetTop = container.scrollTop;
      const aboveTop = elementRect.top - (containerRect.top + padding);
      const belowBottom = elementRect.bottom - (containerRect.bottom - padding);
      if (aboveTop < 0) {
        targetTop = Math.max(0, container.scrollTop + aboveTop);
      } else if (belowBottom > 0) {
        targetTop = Math.min(
          container.scrollHeight - container.clientHeight,
          container.scrollTop + belowBottom
        );
      }
      container.scrollTo({
        top: targetTop,
        behavior: 'smooth'
      });
      await sleep(380);
    }
    element.classList.add('target');
    return element;
  }

  private clearResultTargets() {
    this.elements.browserResults
      .querySelectorAll('.target')
      .forEach((node) => node.classList.remove('target'));
  }

  private renderSearchResults(results: any[]) {
    this.clearResultTargets();
    this.elements.browserResults.innerHTML = '';
    this.browserResultMap.clear();
    const container = this.elements.browserResults.parentElement as HTMLElement | null;
    const subset = results.slice(0, 8);
    if (!subset.length) {
      const empty = document.createElement('li');
      empty.textContent = t('monitor.noSearchResults');
      this.elements.browserResults.appendChild(empty);
    } else {
      subset.forEach((result) => {
        const li = document.createElement('li');
        const title = document.createElement('strong');
        const url = typeof result?.url === 'string' ? result.url : '';
        title.textContent = result?.title || url || t('monitor.searchResultFallback');
        const meta = document.createElement('span');
        meta.textContent = url || 'agent.local';
        li.appendChild(title);
        li.appendChild(meta);
        if (result?.highlight) {
          li.classList.add('highlight');
        }
        const normalized = this.normalizeUrl(url);
        if (normalized) {
          li.dataset.url = normalized;
          this.browserResultMap.set(normalized, li);
        }
        this.elements.browserResults.appendChild(li);
      });
    }
    if (container) {
      container.scrollTo({ top: 0, behavior: 'smooth' });
    }
  }

  private renderExtractionSummary(
    target: HTMLElement,
    result: any
  ): { hasContent: boolean; hasError: boolean } {
    if (!target) {
      return { hasContent: false, hasError: false };
    }
    target.innerHTML = '';
    let hasContent = false;
    let hasError = false;
    const appendMessage = (text: string, className?: string) => {
      const row = document.createElement('p');
      if (className) {
        row.className = className;
      }
      row.textContent = text;
      target.appendChild(row);
    };
    if (result && typeof result === 'object' && result.error) {
      appendMessage(`❌ ${result.error}`);
      hasError = true;
    }
    const sections = this.normalizeExtractionSections(result);
    if (sections.length) {
      hasContent = true;
      sections.forEach((section) => {
        const block = document.createElement('div');
        block.className = 'extract-block';
        if (section.title || section.url) {
          const header = document.createElement('div');
          header.className = 'extract-block-header';
          if (section.title) {
            const strong = document.createElement('strong');
            strong.textContent = section.title;
            header.appendChild(strong);
          }
          if (section.url) {
            const urlLine = document.createElement('span');
            urlLine.textContent = section.url;
            header.appendChild(urlLine);
          }
          block.appendChild(header);
        }
        const pre = document.createElement('pre');
        pre.textContent = section.text;
        block.appendChild(pre);
        target.appendChild(block);
      });
    } else if (!hasError) {
      appendMessage(t('monitor.noExtractionSummary'));
    }
    const failedList: Array<Record<string, any>> = Array.isArray(result?.failed_results)
      ? (result.failed_results as Array<Record<string, any>>)
      : [];
    if (failedList.length) {
      failedList.slice(0, 2).forEach((item) => {
        appendMessage(
          t('monitor.extractFailedItem', {
            url: item?.url || 'URL',
            error: item?.error || t('monitor.extractFailedLabel')
          })
        );
      });
    }
    requestAnimationFrame(() => {
      target.scrollTo({
        top: target.scrollHeight,
        behavior: 'smooth'
      });
    });
    return { hasContent, hasError };
  }

  private normalizeExtractionSections(source: any) {
    const sections: Array<{ title?: string; url?: string; text: string }> = [];
    const pushSection = (text: string, meta?: { title?: string; url?: string }) => {
      const normalized = (text || '').trim();
      if (!normalized) {
        return;
      }
      sections.push({
        title: meta?.title || undefined,
        url: meta?.url || undefined,
        text: normalized
      });
    };
    const pickText = (value: any): string => {
      if (value === null || typeof value === 'undefined') {
        return '';
      }
      if (typeof value === 'string') {
        return value.trim();
      }
      if (typeof value === 'number' || typeof value === 'boolean') {
        return String(value);
      }
      if (Array.isArray(value)) {
        return value
          .map((item) => pickText(item))
          .filter(Boolean)
          .join('\n\n');
      }
      if (typeof value === 'object') {
        const priorityKeys = [
          'raw_content',
          'content',
          'summary',
          'paragraphs',
          'paragraph',
          'text',
          'snippets',
          'highlights',
          'lines',
          'chunks',
          'description'
        ];
        for (const key of priorityKeys) {
          if (key in value) {
            const resolved = pickText(value[key]);
            if (resolved) {
              return resolved;
            }
          }
        }
        if (Array.isArray(value.sections)) {
          return value.sections
            .map((section: any) => pickText(section))
            .filter(Boolean)
            .join('\n\n');
        }
      }
      return '';
    };
    if (source && typeof source === 'object' && source.summary) {
      const summaryText = pickText(source.summary);
      if (summaryText) {
        pushSection(summaryText, {
          title: source.title || t('monitor.extractSectionTitle'),
          url: source.url || source.source
        });
      }
    }
    if (Array.isArray(source?.results) && source.results.length) {
      source.results.forEach((item: any, index: number) => {
        const text = pickText(item);
        if (text) {
          pushSection(text, {
            title: item?.title || t('monitor.extractSectionItem', { n: index + 1 }),
            url: item?.url || item?.source
          });
        }
      });
    }
    if (!sections.length) {
      const fallback = pickText(source);
      if (fallback) {
        pushSection(fallback, {
          title: source?.title,
          url: source?.url || source?.source
        });
      }
    }
    return sections;
  }

  private resolveExtractionStatus(
    completion: any,
    payload: any,
    result: any
  ): 'completed' | 'failed' | null {
    const statusCandidate = (completion?.status || payload?.status || '').toString().toLowerCase();
    if (statusCandidate === 'completed') {
      return 'completed';
    }
    if (statusCandidate === 'failed' || statusCandidate === 'error') {
      return 'failed';
    }
    if (result && typeof result === 'object') {
      if (result.success === false || result.error) {
        return 'failed';
      }
      if (
        result.summary ||
        result.paragraphs ||
        result.content ||
        result.raw_content ||
        (Array.isArray(result.results) && result.results.length)
      ) {
        return 'completed';
      }
    }
    if (typeof result === 'string' && result.trim()) {
      return 'completed';
    }
    if (Array.isArray(result) && result.length) {
      return 'completed';
    }
    return null;
  }

  private toolResultSucceeded(payload: any) {
    if (!payload) {
      return true;
    }
    const status = String(payload?.status || payload?.result?.status || '').toLowerCase();
    const successStatuses = ['completed', 'success', 'succeeded', 'ok', 'done'];
    if (status && !successStatuses.includes(status)) {
      return false;
    }
    if (payload?.result?.success === false) return false;
    if (payload?.success === false) return false;
    if (payload?.result?.error) return false;
    if (payload?.error) return false;
    return true;
  }

  private resolveResultPath(payload: any, fallback: string) {
    if (!payload) {
      return fallback;
    }
    const candidates = [
      payload?.result?.path,
      payload?.path,
      payload?.arguments?.path,
      payload?.arguments?.target_path,
      fallback
    ];
    const resolved = candidates.find((path) => typeof path === 'string' && path.trim().length);
    return resolved || fallback;
  }

  /**
   * 工具失败时统一弹出红色气泡并终止后续动画。
   * 返回 true 表示成功可继续，false 表示已提示错误应中断。
   */
  private ensureSuccessOrErrorBubble(
    completion: any,
    payload?: any,
    fallbackMessage = t('monitor.toolError')
  ): boolean {
    const ok = this.toolResultSucceeded(completion ?? payload ?? null);
    if (ok) {
      return true;
    }
    const message =
      completion?.result?.error ||
      completion?.error ||
      payload?.error ||
      payload?.result?.error ||
      fallbackMessage;
    this.showSpeechBubble(message || fallbackMessage, { variant: 'error', duration: 2600 });
    return false;
  }

  private normalizeLines(content: any): string[] {
    if (typeof content === 'string') {
      const parts = content.split(/\r?\n/).map((line) => line.replace(/\t/g, '    '));
      return parts.length ? parts : [''];
    }
    if (typeof content === 'number') {
      return [String(content)];
    }
    if (Array.isArray(content)) {
      const lines: string[] = [];
      content.forEach((item) => {
        if (typeof item === 'string' || typeof item === 'number') {
          lines.push(String(item));
        } else if (item && typeof item === 'object') {
          const text =
            item.content || item.text || (Array.isArray(item.lines) ? item.lines.join('\n') : '');
          if (text) {
            lines.push(...this.normalizeLines(text));
          }
        }
      });
      return lines;
    }
    if (content && typeof content === 'object') {
      if (typeof content.content === 'string') {
        return this.normalizeLines(content.content);
      }
      if (typeof content.text === 'string') {
        return this.normalizeLines(content.text);
      }
      if (Array.isArray(content.lines)) {
        return this.normalizeLines(content.lines);
      }
    }
    return [];
  }

  private renderReaderMessage(message: string) {
    this.elements.readerLines.innerHTML = '';
    const row = this.buildEditorLineElement(message || t('monitor.readerEmptyFallback'), 0);
    row.classList.add('reading-line', 'empty');
    this.elements.readerLines.appendChild(row);
  }

  private renderReaderLines(source: any) {
    const lines = this.extractReaderLines(source).slice(0, EDITOR_MAX_RENDER_LINES);
    if (!lines.length) {
      this.renderReaderMessage(t('monitor.noVisibleContent'));
      return;
    }
    this.elements.readerLines.innerHTML = '';
    lines.forEach((line, index) => {
      const row = this.buildEditorLineElement(line.text || ' ', index);
      row.classList.add('reading-line');
      if (line.highlight) {
        row.classList.add('highlight');
      }
      this.elements.readerLines.appendChild(row);
      requestAnimationFrame(() => {
        setTimeout(() => row.classList.add('visible'), Math.min(index, 30) * 60);
      });
    });
  }

  private resolveReaderPayload(
    payload: any,
    completion: any
  ): { source: any; label: string | null } {
    const candidates = [
      { label: 'completion.result', value: completion?.result },
      { label: 'completion.result.tool_payload', value: completion?.result?.tool_payload },
      { label: 'completion.payload', value: completion?.payload },
      { label: 'payload.result', value: payload?.result },
      { label: 'payload.result.tool_payload', value: payload?.result?.tool_payload },
      { label: 'payload.payload', value: payload?.payload },
      { label: 'payload.arguments', value: payload?.arguments }
    ];
    for (const candidate of candidates) {
      const normalized = this.normalizeReaderPayloadCandidate(candidate.value);
      if (normalized !== null) {
        readerDebug('resolveReaderPayload hit', candidate.label);
        return { source: normalized, label: candidate.label };
      }
    }
    readerDebug('resolveReaderPayload miss');
    return { source: null, label: null };
  }

  private normalizeReaderPayloadCandidate(value: any): any {
    if (value === null || value === undefined) {
      return null;
    }
    if (this.hasRenderableReaderContent(value)) {
      return value;
    }
    if (typeof value === 'string') {
      const parsed = this.tryParseReaderJson(value);
      if (parsed && this.hasRenderableReaderContent(parsed)) {
        return parsed;
      }
      return value.trim().length ? value : null;
    }
    if (typeof value === 'object') {
      if (typeof value.output === 'string') {
        const parsed = this.tryParseReaderJson(value.output);
        if (parsed && this.hasRenderableReaderContent(parsed)) {
          return parsed;
        }
        if (value.output.trim().length) {
          return value.output;
        }
      }
      if (typeof value.tool_content === 'string') {
        const parsed = this.tryParseReaderJson(value.tool_content);
        if (parsed && this.hasRenderableReaderContent(parsed)) {
          return parsed;
        }
        if (value.tool_content.trim().length) {
          return value.tool_content;
        }
      }
    }
    return null;
  }

  private hasRenderableReaderContent(source: any): boolean {
    if (source === null || source === undefined) {
      return false;
    }
    if (typeof source === 'string') {
      return source.trim().length > 0;
    }
    if (Array.isArray(source)) {
      return source.length > 0;
    }
    if (typeof source === 'object') {
      if (typeof source.content === 'string' && source.content.trim().length) {
        return true;
      }
      if (typeof source.text === 'string' && source.text.trim().length) {
        return true;
      }
      if (Array.isArray(source.lines) && source.lines.length) {
        return true;
      }
      if (Array.isArray(source.matches) && source.matches.length) {
        return true;
      }
      if (Array.isArray(source.segments) && source.segments.length) {
        return true;
      }
    }
    return false;
  }

  private tryParseReaderJson(raw: string): any {
    const trimmed = raw.trim();
    if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
      return null;
    }
    try {
      return JSON.parse(trimmed);
    } catch (error) {
      readerDebug('resolveReaderPayload parse failed', error);
      return null;
    }
  }

  private renderReaderOcr(lines: any) {
    this.elements.readerOcr.innerHTML = '';
    const normalized = this.normalizeLines(lines);
    const segments = (normalized.length ? normalized : [t('monitor.ocrReady')]).slice(0, 6);
    segments.forEach((line) => {
      const p = document.createElement('p');
      p.textContent = line;
      this.elements.readerOcr.appendChild(p);
    });
  }

  private extractReaderLines(source: any): ReaderLine[] {
    let preview = '';
    try {
      preview = JSON.stringify(source, null, 2);
    } catch (error) {
      preview = '[unserializable source]';
      readerDebug('extractReaderLines stringify failed', error);
    }
    readerDebug('extractReaderLines:start', {
      type: typeof source,
      isArray: Array.isArray(source),
      preview
    });

    if (!source) {
      readerDebug('extractReaderLines:empty source');
      return [];
    }
    readerDebug('extractReaderLines:source meta', {
      type: typeof source,
      isArray: Array.isArray(source)
    });

    if (typeof source === 'string' || Array.isArray(source)) {
      const lines = this.normalizeLines(source).map((text, index) => ({
        text,
        lineNumber: index + 1
      }));
      readerDebug('extractReaderLines:string/array', { count: lines.length });
      return lines;
    }
    if (typeof source !== 'object') {
      readerDebug('extractReaderLines:non object', { type: typeof source });
      return [];
    }

    // 首先尝试直接从 content 字段读取
    if (source.content !== undefined && source.content !== null) {
      readerDebug('extractReaderLines:try content', {
        type: typeof source.content
      });
      const lines = this.normalizeLines(source.content).map((text, index) => ({
        text,
        lineNumber: index + 1
      }));
      if (lines.length) {
        readerDebug('extractReaderLines:content success', { count: lines.length });
        return lines;
      }
    }

    // 尝试从 text 字段读取
    if (source.text !== undefined && source.text !== null) {
      readerDebug('extractReaderLines:try text', { type: typeof source.text });
      const lines = this.normalizeLines(source.text).map((text, index) => ({
        text,
        lineNumber: index + 1
      }));
      if (lines.length) {
        readerDebug('extractReaderLines:text success', { count: lines.length });
        return lines;
      }
    }

    // 尝试从 lines 字段读取
    if (Array.isArray(source.lines)) {
      readerDebug('extractReaderLines:try lines array', { length: source.lines.length });
      const lines = this.normalizeLines(source.lines).map((text, index) => ({
        text,
        lineNumber: index + 1
      }));
      if (lines.length) {
        readerDebug('extractReaderLines:lines success', { count: lines.length });
        return lines;
      }
    }

    const type = typeof source.type === 'string' ? source.type.toLowerCase() : '';
    if (type === 'search') {
      const matches = Array.isArray(source.matches) ? source.matches : [];
      const result = matches.flatMap((match: any) => {
        const snippet = typeof match?.snippet === 'string' ? match.snippet : match?.content;
        const baseLine = Number(match?.line_start || match?.start_line || 0);
        return this.normalizeLines(snippet).map((text, index) => ({
          text,
          lineNumber: baseLine ? baseLine + index : undefined,
          highlight: true
        }));
      });
      readerDebug('extractReaderLines:search matches', { count: result.length });
      return result;
    }
    if (type === 'extract') {
      const segments = Array.isArray(source.segments) ? source.segments : [];
      const result = segments.flatMap((segment: any) => {
        const start = Number(segment?.line_start || segment?.start_line || 0);
        const textSource = segment?.content || segment?.text;
        return this.normalizeLines(textSource).map((text, index) => ({
          text,
          lineNumber: start ? start + index : undefined
        }));
      });
      readerDebug('extractReaderLines:extract segments', { count: result.length });
      return result;
    }
    if (Array.isArray(source.segments)) {
      const result = source.segments.flatMap((segment: any) => {
        const start = Number(segment?.line_start || segment?.start_line || 0);
        const textSource = segment?.content || segment?.text;
        return this.normalizeLines(textSource).map((text, index) => ({
          text,
          lineNumber: start ? start + index : undefined
        }));
      });
      readerDebug('extractReaderLines:segments fallback', { count: result.length });
      return result;
    }
    if (Array.isArray(source.matches)) {
      const result = this.extractReaderLines({ type: 'search', matches: source.matches });
      readerDebug('extractReaderLines:matches recursive', { count: result.length });
      return result;
    }
    if (source.content || source.text) {
      const start = Number(source.line_start || source.start_line || 0);
      const result = this.normalizeLines(source.content || source.text).map((text, index) => ({
        text,
        lineNumber: start ? start + index : undefined
      }));
      readerDebug('extractReaderLines:content/text fallback', { count: result.length });
      return result;
    }
    readerDebug('extractReaderLines:failed', {
      keys: Object.keys(source)
    });
    return [];
  }

  private addMemoryCard(text: string) {
    const card = this.createMemoryCard(text);
    this.elements.memoryList.appendChild(card);
    requestAnimationFrame(() => card.classList.add('visible'));
    this.updateMemoryMeta();
  }

  private getTodoItems(): HTMLElement[] {
    if (!this.elements.todoList) return [];
    return Array.from(this.elements.todoList.children) as HTMLElement[];
  }

  private findTodoItemByText(text?: string | null): HTMLElement | null {
    if (!text) return null;
    const target = (text || '').trim();
    return (
      this.getTodoItems().find(
        (item) => item.querySelector('.todo-text')?.textContent?.trim() === target
      ) || null
    );
  }

  private findTodoItemByIndex(index?: number | null): HTMLElement | null {
    if (!index || index < 1) return null;
    const items = this.getTodoItems();
    if (index > items.length) return null;
    return items[index - 1];
  }

  private normalizeTodoTasks(raw: any): Array<{ text: string; done?: boolean }> {
    if (Array.isArray(raw)) {
      return raw
        .map((item) => {
          if (typeof item === 'string') return { text: item, done: false };
          if (item && typeof item === 'object') {
            const text = String(item.title || item.task || item.text || '').trim();
            const done =
              typeof item.completed === 'boolean'
                ? item.completed
                : typeof item.done === 'boolean'
                  ? item.done
                  : typeof item.checked === 'boolean'
                    ? item.checked
                    : false;
            if (!text) return null;
            return { text, done };
          }
          return null;
        })
        .filter(Boolean) as Array<{ text: string; done?: boolean }>;
    }
    if (typeof raw === 'string') {
      return [{ text: raw, done: false }];
    }
    return [];
  }

  private createTodoItem(text: string, done = false) {
    const item = document.createElement('div');
    item.className = 'todo-item fly-in';
    const body = document.createElement('div');
    body.className = 'todo-text';
    body.textContent = text;
    const check = document.createElement('div');
    check.className = 'todo-check';
    if (done) {
      check.classList.add('checked');
      item.classList.add('done');
    }
    item.appendChild(body);
    item.appendChild(check);
    return item;
  }

  private renderTodoItems(items: Array<{ text: string; done?: boolean }>) {
    if (!this.elements.todoList) return;
    this.elements.todoList.innerHTML = '';
    items.forEach((task) => {
      const card = this.createTodoItem(task.text, !!task.done);
      card.classList.add('visible');
      this.elements.todoList.appendChild(card);
    });
  }

  private resetTodoBoard(options: { summary?: boolean; list?: boolean } = {}) {
    const { summary = true, list = true } = options;
    if (summary && this.elements.todoSummary) {
      this.elements.todoSummary.textContent = '';
    }
    if (list && this.elements.todoList) {
      this.elements.todoList.innerHTML = '';
      this.elements.todoList.scrollTop = 0;
    }
  }

  private async ensureTodoWindowVisible() {
    if (this.isWindowVisible(this.elements.todoWindow)) {
      this.showWindow(this.elements.todoWindow);
      return;
    }
    await this.movePointerToApp('todo');
    await this.click({ count: 2 });
    this.showWindow(this.elements.todoWindow);
    this.resetTodoBoard({ summary: true, list: true });
  }

  private async typeTodoSummary(text: string) {
    if (!this.elements.todoSummary) return;
    await this.movePointerToElement(this.elements.todoSummary, { duration: 420 });
    await this.click();
    this.elements.todoSummary.textContent = '';
    const chars = Array.from(text || t('monitor.todoEmptySummary'));
    for (const ch of chars) {
      this.elements.todoSummary.textContent = (this.elements.todoSummary.textContent || '') + ch;
      await sleep(20);
    }
  }

  private async animateTodoAppend(
    task: { text: string; done?: boolean },
    options: { scrollIntoView?: boolean } = {}
  ) {
    if (!this.elements.todoList) return;
    const { scrollIntoView = false } = options;
    const card = this.createTodoItem(task.text, !!task.done);
    this.elements.todoList.appendChild(card);
    requestAnimationFrame(() => card.classList.add('visible'));
    if (scrollIntoView) {
      await this.scrollTodoItemIntoView(card);
    }
    await sleep(180);
  }

  private async scrollTodoItemIntoView(
    card: HTMLElement | null,
    options: { waitMs?: number } = {}
  ) {
    if (!card || !this.elements.todoList) return;
    const body = this.elements.todoList;
    const margin = 8;
    const wait = typeof options.waitMs === 'number' ? options.waitMs : 120;

    const measure = () => {
      const bodyRect = body.getBoundingClientRect();
      const cardRect = card.getBoundingClientRect();
      const relTop = cardRect.top - bodyRect.top + body.scrollTop;
      const relBottom = relTop + cardRect.height;
      const viewTop = body.scrollTop;
      const viewBottom = viewTop + body.clientHeight;
      return { relTop, relBottom, viewTop, viewBottom };
    };

    let { relTop, relBottom, viewTop, viewBottom } = measure();

    // 已完整可见（含少量缓冲），直接返回
    if (relTop >= viewTop + margin && relBottom <= viewBottom - margin) {
      return;
    }

    // 计算需要滚动的位置（基于相对坐标，避免 offsetParent 不一致的问题）
    const needsScrollDown = relBottom > viewBottom - margin;
    const targetTop = needsScrollDown
      ? relBottom - body.clientHeight + margin
      : Math.max(0, relTop - margin);
    const clampedTop = Math.max(0, targetTop);

    body.scrollTo({ top: clampedTop, behavior: 'smooth' });
    await this.waitForScrollSettled(body, clampedTop);
    if (wait > 0) {
      await sleep(wait);
    }

    // 二次校验，确保整块已进入可视区域
    ({ relTop, relBottom, viewTop, viewBottom } = measure());
    if (relTop < viewTop + margin) {
      body.scrollTop = Math.max(0, relTop - margin);
      await sleep(30);
    } else if (relBottom > viewBottom - margin) {
      body.scrollTop = relBottom - body.clientHeight + margin;
      await sleep(30);
    }
  }

  private async toggleTodoItem(text?: string | null, done?: boolean, index?: number | null) {
    const card =
      this.findTodoItemByIndex(index) ||
      this.findTodoItemByText(text) ||
      (!text && !index ? this.getTodoItems()[0] : null);
    if (!card) return;
    await this.scrollTodoItemIntoView(card);
    const check = card.querySelector('.todo-check') as HTMLElement | null;
    if (!check) return;
    await this.movePointerToElement(check, { duration: 420 });
    await this.click();
    const targetState = typeof done === 'boolean' ? done : !check.classList.contains('checked');
    check.classList.toggle('checked', targetState);
    card.classList.toggle('done', targetState);
    await sleep(200);
  }
}
