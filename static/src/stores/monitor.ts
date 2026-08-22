import { defineStore } from 'pinia';
import type {
  MonitorBubbleOptions,
  MonitorDriver,
  MonitorSceneRuntime
} from '@/components/chat/monitor/types';
import { getSceneProgressLabel } from '@/components/chat/monitor/progressMap';
import { useConnectionStore } from '@/stores/connection';

const MONITOR_DEBUG_LOGS = false;
const monitorDebug = (...args: any[]) => {
  if (!MONITOR_DEBUG_LOGS) {
    return;
  }
  console.debug('[Monitor]', ...args);
};
const monitorTrace = (label: string) => {
  if (!MONITOR_DEBUG_LOGS) {
    return;
  }
  console.trace(`[Monitor] ${label}`);
};
const MONITOR_PROGRESS_DEBUG = false;
const monitorProgressDebug = (...args: any[]) => {
  if (!MONITOR_PROGRESS_DEBUG) {
    return;
  }
  console.debug('[MonitorProgressStore]', ...args);
};
const MONITOR_LIFECYCLE_DEBUG = false;
const monitorLifecycleLog = (...args: any[]) => {
  if (!MONITOR_LIFECYCLE_DEBUG) {
    return;
  }
  // 使用 console.info 方便在控制台中筛选
  console.info('[MonitorLifecycle]', ...args);
};

type MonitorEvent = {
  id: string;
  script: string;
  payload: Record<string, any>;
};

type PendingResultEntry = {
  promise: Promise<any>;
  resolve: (value: any) => void;
  timeout: ReturnType<typeof setTimeout> | null;
  settled: boolean;
};

interface MonitorState {
  statusLabel: string;
  queue: MonitorEvent[];
  playing: boolean;
  awaitingTools: Record<string, string>;
  lastTreeSnapshot: string[];
  lastSpeechAt: number;
  thinkingActive: boolean;
  pendingResults: Record<string, PendingResultEntry>;
  completedResults: Record<string, any>;
  driver: MonitorDriver | null;
  speechBuffer: string;
  bubbleActive: boolean;
  pendingProgressScene: string | null;
  progressIndicator: {
    id: string | null;
    label: string;
    scene: string | null;
  };
}

const DEFAULT_ROOTS = [
  '__pycache__',
  'config',
  'core',
  'data',
  'doc',
  'docker',
  'logs',
  'modules',
  'node_modules',
  'project',
  'prompts',
  'scratch_test',
  'scripts',
  'static',
  'sub_agent',
  'test',
  'users',
  'utils'
];

const COMPLETED_RESULT_CACHE_LIMIT = 32;

const TOOL_SCENE_MAP: Record<string, string> = {
  web_search: 'browserSearch',
  extract_webpage: 'webExtract',
  save_webpage: 'webSave',
  read_file: 'reader',
  view_video: 'reader',
  vlm_analyze: 'ocr',
  ocr_image: 'ocr',
  create_folder: 'createFolder',
  create_file: 'createFile',
  rename_file: 'renameFile',
  delete_file: 'deleteFile',
  write_file: 'modifyFile',
  edit_file: 'modifyFile',
  run_command: 'runCommand',
  terminal_session: 'terminalSession',
  terminal_input: 'terminalInput',
  terminal_snapshot: 'terminalSnapshot',
  sleep: 'terminalSleep',
  update_memory: 'memoryUpdate',
  recall_project_memory: 'reader',
  search_project_memory: 'reader',
  update_project_memory: 'memoryUpdate',
  todo_create: 'todoCreate',
  todo_update_task: 'todoUpdate',
  todo_delete_task: 'todoDelete',
  terminal_run: 'runCommand'
};

const randomId = (prefix = 'evt') =>
  `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

export const useMonitorStore = defineStore('monitor', {
  state: (): MonitorState => ({
    statusLabel: '待机',
    queue: [],
    playing: false,
    awaitingTools: {},
    lastTreeSnapshot: [...DEFAULT_ROOTS],
    lastSpeechAt: 0,
    thinkingActive: false,
    pendingResults: {},
    completedResults: {},
    driver: null,
    speechBuffer: '',
    bubbleActive: false,
    pendingProgressScene: null,
    progressIndicator: {
      id: null,
      label: '',
      scene: null
    }
  }),
  getters: {
    isLocked: (state) => state.playing || state.queue.length > 0
  },
  actions: {
    registerDriver(driver: MonitorDriver) {
      this.driver = driver;
      this.driver.resetScene({
        desktopRoots: this.lastTreeSnapshot,
        preserveBubble: this.bubbleActive,
        preservePointer: false, // 初次挂载重置指针
        preserveWindows: false
      });
      if (this.speechBuffer) {
        this.driver.showSpeechBubble(this.speechBuffer, { variant: 'info', duration: 0 });
        this.bubbleActive = true;
      }
      this.driver.setManualInteractionEnabled(!this.isLocked);
      if (this.pendingProgressScene) {
        monitorProgressDebug('registerDriver:apply-pending', { scene: this.pendingProgressScene });
        this.driver.previewSceneProgress(this.pendingProgressScene);
        this.pendingProgressScene = null;
      }
      if (this.queue.length) {
        this.processQueue();
      }
    },
    unregisterDriver() {
      if (this.driver) {
        this.driver.setManualInteractionEnabled(false);
        this.driver.hideBubble();
      }
      this.driver = null;
      this.playing = false;
    },
    syncDesktopFromTree(tree: Array<{ type: string; name: string }> | null) {
      if (!Array.isArray(tree) || !tree.length) {
        this.lastTreeSnapshot = [...DEFAULT_ROOTS];
        this.driver?.setDesktopRoots(this.lastTreeSnapshot, { immediate: true });
        return;
      }
      const folders = tree
        .filter((node) => node && node.type === 'folder')
        .map((node) => node.name)
        .filter(Boolean);
      this.lastTreeSnapshot = folders.length ? folders : [...DEFAULT_ROOTS];
      this.driver?.setDesktopRoots(this.lastTreeSnapshot);
    },
    resetVisualState(options?: {
      preserveBubble?: boolean;
      preservePointer?: boolean;
      preserveWindows?: boolean;
      preserveQueue?: boolean;
      preservePendingResults?: boolean;
      preserveAwaitingTools?: boolean;
    }) {
      const preserveBubble = !!options?.preserveBubble;
      const preservePointer = options?.preservePointer !== false;
      const preserveWindows = !!options?.preserveWindows;
      const preserveQueue = !!options?.preserveQueue;
      const preservePendingResults = !!options?.preservePendingResults;
      const preserveAwaitingTools = !!options?.preserveAwaitingTools;
      this.statusLabel = '待机';
      if (!preserveQueue) {
        this.queue = [];
        this.playing = false;
      }
      this.lastSpeechAt = 0;
      if (!preserveAwaitingTools) {
        this.awaitingTools = {};
      }
      if (!preservePendingResults) {
        Object.values(this.pendingResults).forEach((entry) => {
          if (entry?.timeout) {
            clearTimeout(entry.timeout);
          }
        });
        this.pendingResults = {};
        this.completedResults = {};
      }
      if (!preserveBubble) {
        this.speechBuffer = '';
        this.bubbleActive = false;
      }
      this.thinkingActive = false;
      this.pendingProgressScene = null;
      this.progressIndicator = { id: null, label: '', scene: null };
      this.driver?.resetScene({
        desktopRoots: this.lastTreeSnapshot,
        preserveBubble,
        preservePointer,
        preserveWindows
      });
      this.driver?.setManualInteractionEnabled(!this.isLocked);
    },
    setProgressIndicator(payload: { id: string | null; label: string; scene: string }) {
      this.progressIndicator = {
        id: payload.id,
        label: payload.label,
        scene: payload.scene
      };
      monitorProgressDebug('progress-indicator:set', this.progressIndicator);
    },
    /**
     * 仅用于模型刚检测到工具调用阶段的预览，不入队、不等待结果。
     * 使用空 id 以便后续真正的 tool_start/update_action 可以顺利覆盖与清除。
     */
    previewToolIntent(payload: Record<string, any>) {
      if (this.bubbleActive) {
        this.hideBubble(`tool-intent:${payload?.name || 'unknown'}`);
      }
      this.speechBuffer = '';
      const script = TOOL_SCENE_MAP[payload.name] || 'genericTool';
      const progressLabel = getSceneProgressLabel(script);
      if (!progressLabel) {
        return;
      }
      this.setStatus(progressLabel);
      this.progressIndicator = {
        id: null,
        label: progressLabel,
        scene: script
      };
      monitorProgressDebug('preview-tool-intent', {
        tool: payload.name,
        script,
        label: progressLabel
      });
      if (this.driver) {
        this.driver.previewSceneProgress(script);
      } else {
        this.pendingProgressScene = script;
      }
    },
    clearProgressIndicator(id?: string | null) {
      if (id && this.progressIndicator.id && id !== this.progressIndicator.id) {
        return;
      }
      monitorProgressDebug('progress-indicator:clear', {
        id: this.progressIndicator.id,
        label: this.progressIndicator.label
      });
      this.progressIndicator = { id: null, label: '', scene: null };
    },
    setStatus(label: string) {
      this.statusLabel = label;
    },
    showBubble(text: string, options: MonitorBubbleOptions = {}) {
      if (!text) {
        return;
      }
      monitorDebug('showBubble', {
        text,
        variant: options?.variant,
        duration: options?.duration
      });
      this.driver?.showSpeechBubble(text, options);
      this.bubbleActive = true;
    },
    hideBubble(reason?: string) {
      monitorDebug('hideBubble', reason || '');
      monitorTrace('hideBubble');
      this.driver?.hideBubble();
      this.bubbleActive = false;
      this.thinkingActive = false;
    },
    resetSpeechBuffer() {
      monitorDebug('resetSpeechBuffer');
      this.speechBuffer = '';
    },
    showPendingReply() {
      this.setStatus('待机');
      if (this.driver && typeof this.driver.showWaitingBubble === 'function') {
        this.driver.showWaitingBubble('等待回复');
        this.bubbleActive = true;
        return;
      }
      this.driver?.showSpeechBubble('等待回复...', { variant: 'info', duration: 0 });
      this.bubbleActive = true;
    },
    enqueueModelSpeech(text: string) {
      if (!text) {
        return;
      }
      this.setStatus('正在规划');
      this.speechBuffer = `${this.speechBuffer}${text}`;
      this.lastSpeechAt = Date.now();
      monitorDebug('enqueueModelSpeech', {
        incoming: text,
        combined: this.speechBuffer
      });
      if (this.playing || !this.driver) {
        this.bubbleActive = false;
        return;
      }
      this.driver.showSpeechBubble(this.speechBuffer, { variant: 'info', duration: 0 });
      this.bubbleActive = true;
    },
    enqueueModelThinking() {
      const connection = useConnectionStore();
      const skipThinking = connection.runMode === 'fast' || connection.thinkingMode === false;
      if (skipThinking) {
        monitorDebug('enqueueModelThinking skipped');
        return;
      }
      if (this.playing || !this.driver) {
        return;
      }
      if (this.thinkingActive) {
        monitorDebug('enqueueModelThinking already active');
        return;
      }
      this.setStatus('思考中');
      monitorDebug('enqueueModelThinking show bubble');
      this.driver?.showThinkingBubble();
      this.bubbleActive = true;
      this.thinkingActive = true;
    },
    endModelOutput() {
      this.setStatus('待机');
      this.thinkingActive = false;
    },
    enqueueToolEvent(payload: Record<string, any>) {
      const now = Date.now();
      const recentSpeechGap = now - this.lastSpeechAt;
      const MIN_SPEECH_VISIBLE_MS = 480;
      const script = TOOL_SCENE_MAP[payload.name] || 'genericTool';
      const progressLabel = getSceneProgressLabel(script);
      if (progressLabel) {
        monitorProgressDebug('enqueueToolEvent:set-status', { script, progressLabel });
        this.setStatus(progressLabel);
      }
      const id = payload.executionId || payload.id || randomId('tool');
      this.ensurePendingResult(id);
      this.queue.push({ id, script, payload: { ...payload, executionId: id } });
      this.awaitingTools[id] = payload.name;

      // 提前为创建类工具标记“待创建”路径，避免动画开始前图标闪现
      if (this.driver && typeof this.driver.preparePendingCreation === 'function') {
        if (payload.name === 'create_folder' || payload.name === 'create_file') {
          const pendingPath =
            payload?.arguments?.path ||
            payload?.arguments?.target_path ||
            payload?.arguments?.targetPath ||
            payload?.argumentSnapshot?.path ||
            payload?.argumentSnapshot?.target_path;
          this.driver.preparePendingCreation(pendingPath);
        }
      }

      monitorLifecycleLog('enqueue', {
        id,
        tool: payload.name,
        script,
        queueSize: this.queue.length,
        awaiting: Object.keys(this.awaitingTools).length,
        status: this.statusLabel
      });
      if (progressLabel) {
        this.setProgressIndicator({ id, label: progressLabel, scene: script });
      } else {
        this.clearProgressIndicator();
      }
      const doPreview = () => {
        if (this.driver) {
          monitorProgressDebug('enqueueToolEvent:preview-now', { tool: payload.name, script, id });
          this.driver.previewSceneProgress(script);
        } else {
          monitorProgressDebug('enqueueToolEvent:pending-preview', {
            tool: payload.name,
            script,
            id
          });
          this.pendingProgressScene = script;
        }
        if (this.bubbleActive) {
          this.hideBubble(`tool-enqueue:${payload?.name || 'unknown'}`);
        }
      };
      if (this.bubbleActive && recentSpeechGap >= 0 && recentSpeechGap < MIN_SPEECH_VISIBLE_MS) {
        const delay = MIN_SPEECH_VISIBLE_MS - recentSpeechGap;
        monitorLifecycleLog('enqueue:delay-preview-for-speech', {
          delay,
          recentSpeechGap,
          tool: payload.name
        });
        setTimeout(doPreview, delay);
      } else {
        doPreview();
      }
      this.processQueue();
    },
    resolveToolResult(payload: {
      id?: string;
      execution_id?: string;
      executionId?: string;
      status?: string;
      message?: string;
      result?: any;
    }) {
      const key = payload.execution_id || payload.executionId || payload.id;
      if (key && this.awaitingTools[key]) {
        delete this.awaitingTools[key];
      }
      monitorLifecycleLog('tool-result', {
        id: key,
        status: payload.status,
        message: payload.message,
        hasResult: payload.result !== undefined
      });
      if (key) {
        this.clearProgressIndicator(key);
      }
      if (payload.status && ['failed', 'error'].includes(payload.status)) {
        const message = payload.message || '工具执行失败';
        this.driver?.showSpeechBubble(message, { variant: 'error', icon: '✖', duration: 2800 });
        this.setStatus('异常');
        setTimeout(() => this.hideBubble(), 2600);
      }
      if (key) {
        this.fulfillPendingResult(key, payload);
      }
    },
    ensurePendingResult(id: string) {
      if (!id) {
        return;
      }
      const hasCached = Object.prototype.hasOwnProperty.call(this.completedResults, id);
      if (this.pendingResults[id]) {
        if (hasCached) {
          const cached = this.completedResults[id];
          delete this.completedResults[id];
          this.pendingResults[id].resolve(cached);
        }
        return;
      }
      let resolver: (value: any) => void = () => {};
      const promise = new Promise<any>((resolve) => {
        resolver = resolve;
      });
      const entry: PendingResultEntry = {
        promise,
        resolve: (value) => {
          if (!entry.settled) {
            entry.settled = true;
            if (entry.timeout) {
              clearTimeout(entry.timeout);
              entry.timeout = null;
            }
          }
          resolver(value);
        },
        timeout: null,
        settled: false
      };
      entry.timeout = setTimeout(() => {
        if (!entry.settled) {
          entry.resolve(null);
          this.cleanupPendingResult(id, entry);
        }
      }, 12000);
      this.pendingResults[id] = entry;
      if (hasCached) {
        const cached = this.completedResults[id];
        delete this.completedResults[id];
        entry.resolve(cached);
      }
    },
    waitForResult(id?: string | number | null) {
      if (!id) {
        return Promise.resolve(null);
      }
      const key = String(id);
      if (Object.prototype.hasOwnProperty.call(this.completedResults, key)) {
        const cached = this.completedResults[key];
        delete this.completedResults[key];
        return Promise.resolve(cached);
      }
      this.ensurePendingResult(key);
      const entry = this.pendingResults[key];
      if (!entry) {
        return Promise.resolve(null);
      }
      return entry.promise.finally(() => {
        this.cleanupPendingResult(key, entry);
      });
    },
    fulfillPendingResult(id: string, payload: any) {
      if (!id) {
        return;
      }
      const entry = this.pendingResults[id];
      if (entry) {
        entry.resolve(payload);
        return;
      }
      this.completedResults[id] = payload;
      const cacheKeys = Object.keys(this.completedResults);
      if (cacheKeys.length > COMPLETED_RESULT_CACHE_LIMIT) {
        const staleKey = cacheKeys[0];
        delete this.completedResults[staleKey];
      }
    },
    cleanupPendingResult(id: string, entry?: PendingResultEntry) {
      if (!id) {
        return;
      }
      const target = entry || this.pendingResults[id];
      if (!target) {
        return;
      }
      if (target.timeout) {
        clearTimeout(target.timeout);
        target.timeout = null;
      }
      if (this.pendingResults[id] === target) {
        delete this.pendingResults[id];
      }
    },
    async processQueue() {
      if (this.playing || !this.queue.length) {
        return;
      }
      if (!this.driver) {
        return;
      }
      this.playing = true;
      this.driver.setManualInteractionEnabled(false);
      monitorLifecycleLog('queue-start', { size: this.queue.length });
      while (this.queue.length && this.driver) {
        const event = this.queue.shift();
        if (!event) {
          continue;
        }
        monitorDebug('processQueue start', {
          script: event.script,
          bubbleActive: this.bubbleActive,
          speechBufferLength: this.speechBuffer.length
        });
        if (this.bubbleActive) {
          this.hideBubble(`queue-start:${event.script}`);
        }
        this.speechBuffer = '';
        await this.runScript(event);
      }
      this.playing = false;
      if (this.driver) {
        this.driver.setManualInteractionEnabled(true);
      }
      if (this.speechBuffer && this.driver) {
        this.driver.showSpeechBubble(this.speechBuffer, { variant: 'info', duration: 0 });
        this.bubbleActive = true;
      }
      this.setStatus('待机');
      monitorLifecycleLog('queue-end');
    },
    async runScript(event: MonitorEvent) {
      if (!this.driver) {
        this.queue.unshift(event);
        return;
      }
      const waitKey =
        event.payload?.executionId ||
        event.payload?.execution_id ||
        event.id ||
        event.payload?.id ||
        null;
      monitorLifecycleLog('runScript:start', {
        script: event.script,
        waitKey,
        payloadTool: event.payload?.name,
        payloadId: event.payload?.id
      });

      let playbackResult: any = undefined;
      let playbackSettled = false;
      const waitForCompletion = async () => {
        if (!waitKey) {
          playbackSettled = true;
          if (playbackResult === undefined) {
            playbackResult = null;
          }
          return playbackResult;
        }
        if (playbackSettled) {
          return playbackResult;
        }
        try {
          playbackResult = await this.waitForResult(waitKey);
        } catch (error) {
          console.warn('monitor waitForResult error', error);
          playbackResult = null;
        } finally {
          playbackSettled = true;
        }
        return playbackResult;
      };

      const transformStatus = (raw?: string) => {
        const label = typeof raw === 'string' && raw.trim().length ? raw.trim() : '进行中';
        if (label.startsWith('正在')) {
          return `回放${label.slice(2)}`;
        }
        return label;
      };

      const runtime: MonitorSceneRuntime = {
        waitForResult: () => waitForCompletion(),
        setStatus: (label: string) => this.setStatus(transformStatus(label)),
        statusPhase: 'playback'
      };

      this.setStatus('工具操作回放');
      monitorLifecycleLog('runScript:playback', {
        script: event.script,
        waitKey,
        status: this.statusLabel
      });
      try {
        await this.driver.playScene(event.script, event.payload || {}, runtime);
      } catch (error) {
        console.warn('Monitor scene error', error);
      } finally {
        monitorLifecycleLog('runScript:end', {
          script: event.script,
          waitKey
        });
      }
    }
  }
});
