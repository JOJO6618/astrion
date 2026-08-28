<template>
  <div class="virtual-monitor-surface">
    <div class="monitor-stage">
      <div class="monitor">
        <div class="monitor-shell">
          <div class="monitor-top">
            <div class="monitor-brand">Agent Display Surface</div>
            <div class="status-pill-group">
              <div class="status-pill">{{ statusLabel }}</div>
              <div class="progress-pill" v-if="currentProgressLabel">
                <span class="pulse-dot"></span>
                <span class="progress-text">{{ currentProgressLabel }}</span>
              </div>
            </div>
          </div>
          <div class="monitor-screen" ref="screenEl">
            <div class="desktop-layer">
              <div class="desktop-section apps">
                <div class="apps-grid" ref="appsGrid"></div>
              </div>
              <div class="desktop-section files">
                <div class="desktop-grid" ref="desktopGrid"></div>
              </div>
            </div>

            <div class="window browser-window" ref="browserWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span>{{ $t('chat.vmBrowserTitle') }}</span>
              </div>
              <div class="browser-body">
                <div class="search-bar"><span ref="browserSearchText"></span></div>
                <div class="browser-status" ref="browserStatus">{{ $t('chat.vmBrowserReady') }}</div>
                <div class="results-list results-scroll">
                  <ul ref="browserResults"></ul>
                </div>
              </div>
            </div>

            <div class="window extraction-window" ref="extractionWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span class="window-title">{{ $t('chat.vmWebExtract') }}</span>
              </div>
              <div class="extraction-body">
                <div class="extract-url" ref="extractionUrl"></div>
                <div class="extract-status" ref="extractionState">{{ $t('chat.vmExtractWait') }}</div>
                <div class="extract-summary" ref="extractionSummary"></div>
              </div>
            </div>

            <div class="window folder-window" ref="folderWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="folderHeaderText">workspace</span>
              </div>
              <div class="folder-body" ref="folderBody"></div>
            </div>

            <div class="window editor-window" ref="editorWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="editorHeaderText">{{ $t('chat.vmFile') }}</span>
              </div>
              <div class="editor-body" ref="editorBody"></div>
            </div>

            <div class="window terminal-window" ref="terminalWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="terminalHeaderText">{{ $t('chat.vmTerminal') }}</span>
              </div>
              <div class="terminal-body">
                <div class="terminal-tabs" ref="terminalTabs">
                  <div class="terminal-tab-list" ref="terminalTabList"></div>
                  <button class="terminal-tab add-tab" ref="terminalAddButton">+</button>
                </div>
                <div class="terminal-output" ref="terminalBody"></div>
              </div>
            </div>

            <div class="window command-window" ref="commandWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="commandTitle">{{ $t('chat.vmCommandLine') }}</span>
              </div>
              <div class="command-body">
                <div class="command-input" ref="commandInput"></div>
                <div class="command-output" ref="commandOutput"></div>
              </div>
            </div>

            <div class="window python-window" ref="pythonWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="pythonTitle">Python</span>
              </div>
              <div class="python-body" ref="pythonBody">
                <div class="python-input" ref="pythonInput"></div>
                <div class="python-output" ref="pythonOutput"></div>
              </div>
            </div>

            <div class="window reader-window" ref="readerWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span ref="readerTitle">{{ $t('chat.vmReaderTitle') }}</span>
              </div>
              <div class="reader-body">
                <div class="reading-lines" ref="readerLines"></div>
                <div class="ocr-panel" ref="readerOcr"></div>
              </div>
            </div>

            <div class="window memory-window" ref="memoryWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span class="window-title">{{ $t('chat.vmMemory') }}</span>
              </div>
              <div class="memory-body">
                <div class="memory-list" ref="memoryList"></div>
              </div>
              <div class="memory-footer hidden" aria-hidden="true">
                <span><strong ref="memoryCount"></strong></span>
                <span><strong ref="memoryTime"></strong></span>
                <span ref="memoryStatus"></span>
              </div>
            </div>

            <div class="window todo-window" ref="todoWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span class="window-title">{{ $t('chat.vmTodo') }}</span>
              </div>
              <div class="todo-body">
                <div class="todo-summary" ref="todoSummary"></div>
                <div class="todo-progress" ref="todoList"></div>
              </div>
            </div>

            <div class="window wait-window" ref="waitWindow">
              <div class="window-header">
                <span class="traffic-dot red"></span>
                <span class="traffic-dot yellow"></span>
                <span class="traffic-dot green"></span>
                <span class="window-title">{{ $t('chat.vmWait') }}</span>
              </div>
              <div class="wait-body">
                <div class="flip-clock" ref="waitDisplay">
                  <div class="flip-digit" data-value="0">
                    <span class="top">0</span><span class="bottom">0</span>
                  </div>
                  <div class="flip-digit" data-value="0">
                    <span class="top">0</span><span class="bottom">0</span>
                  </div>
                  <div class="flip-separator">:</div>
                  <div class="flip-digit" data-value="0">
                    <span class="top">0</span><span class="bottom">0</span>
                  </div>
                  <div class="flip-digit" data-value="0">
                    <span class="top">0</span><span class="bottom">0</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="context-menu" ref="desktopMenu">
              <button data-action="file">{{ $t('chat.vmNewFile') }}</button>
              <button data-action="folder">{{ $t('chat.vmNewFolder') }}</button>
              <button data-action="wait">{{ $t('chat.vmWait') }}</button>
            </div>
            <div class="context-menu" ref="folderMenu">
              <button data-action="file">{{ $t('chat.vmNewFile') }}</button>
              <button data-action="folder">{{ $t('chat.vmNewFolder') }}</button>
            </div>
            <div class="context-menu" ref="fileMenu">
              <button data-action="read">{{ $t('chat.vmReadFile') }}</button>
              <button data-action="edit">{{ $t('chat.vmEditFile') }}</button>
              <button data-action="rename">{{ $t('chat.vmRename') }}</button>
              <button data-action="delete">{{ $t('chat.vmDeleteFile') }}</button>
            </div>
            <div class="context-menu" ref="focusMenu">
              <button data-action="focus">{{ $t('chat.vmFocusFile') }}</button>
              <button data-action="unfocus">{{ $t('chat.vmUnfocus') }}</button>
            </div>
            <div class="context-menu" ref="browserMenu">
              <button data-action="save">{{ $t('chat.vmSaveWebpage') }}</button>
            </div>
            <div class="context-menu" ref="terminalMenu">
              <button data-action="snapshot">{{ $t('chat.vmSaveSnapshot') }}</button>
              <button data-action="reset">{{ $t('chat.vmResetTerminal') }}</button>
              <button data-action="close">{{ $t('chat.vmCloseTerminal') }}</button>
            </div>

            <div class="speech-bubble" ref="bubbleEl">
              <div class="bubble-icon-slot" ref="bubbleIconSlot"></div>
              <div class="bubble-text-slot" ref="bubbleTextSlot"></div>
            </div>

            <div class="mouse-pointer" ref="mousePointer">
              <img :src="mouseIcon" alt="pointer" />
            </div>

            <div class="wait-overlay" ref="waitOverlay">
              <div class="z z-1">Z</div>
              <div class="z z-2">Z</div>
              <div class="z z-3">Z</div>
              <div class="z z-4">Z</div>
              <div class="wait-countdown" ref="waitCountdown">5s</div>
            </div>
          </div>
        </div>
        <div class="monitor-stand"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useMonitorStore } from '@/stores/monitor';
import { MonitorDirector, type MonitorElements } from '@/components/chat/monitor/MonitorDirector';

const monitorStore = useMonitorStore();
const { statusLabel, progressIndicator, lastTreeSnapshot } = storeToRefs(monitorStore);
const currentProgressLabel = computed(() => progressIndicator.value?.label || '');

const screenEl = ref<HTMLElement | null>(null);
const appsGrid = ref<HTMLElement | null>(null);
const desktopGrid = ref<HTMLElement | null>(null);
const browserWindow = ref<HTMLElement | null>(null);
const browserSearchText = ref<HTMLElement | null>(null);
const browserStatus = ref<HTMLElement | null>(null);
const browserResults = ref<HTMLElement | null>(null);
const extractionWindow = ref<HTMLElement | null>(null);
const extractionUrl = ref<HTMLElement | null>(null);
const extractionState = ref<HTMLElement | null>(null);
const extractionSummary = ref<HTMLElement | null>(null);
const folderWindow = ref<HTMLElement | null>(null);
const folderHeaderText = ref<HTMLElement | null>(null);
const folderBody = ref<HTMLElement | null>(null);
const editorWindow = ref<HTMLElement | null>(null);
const editorHeaderText = ref<HTMLElement | null>(null);
const editorBody = ref<HTMLElement | null>(null);
const terminalWindow = ref<HTMLElement | null>(null);
const terminalHeaderText = ref<HTMLElement | null>(null);
const terminalTabs = ref<HTMLElement | null>(null);
const terminalTabList = ref<HTMLElement | null>(null);
const terminalAddButton = ref<HTMLElement | null>(null);
const terminalBody = ref<HTMLElement | null>(null);
const commandWindow = ref<HTMLElement | null>(null);
const commandTitle = ref<HTMLElement | null>(null);
const commandInput = ref<HTMLElement | null>(null);
const commandOutput = ref<HTMLElement | null>(null);
const pythonWindow = ref<HTMLElement | null>(null);
const pythonTitle = ref<HTMLElement | null>(null);
const pythonBody = ref<HTMLElement | null>(null);
const pythonInput = ref<HTMLElement | null>(null);
const pythonOutput = ref<HTMLElement | null>(null);
const readerWindow = ref<HTMLElement | null>(null);
const readerTitle = ref<HTMLElement | null>(null);
const readerLines = ref<HTMLElement | null>(null);
const readerOcr = ref<HTMLElement | null>(null);
const memoryWindow = ref<HTMLElement | null>(null);
const memoryList = ref<HTMLElement | null>(null);
const memoryStatus = ref<HTMLElement | null>(null);
const memoryCount = ref<HTMLElement | null>(null);
const memoryTime = ref<HTMLElement | null>(null);
const todoWindow = ref<HTMLElement | null>(null);
const todoSummary = ref<HTMLElement | null>(null);
const todoList = ref<HTMLElement | null>(null);
const waitWindow = ref<HTMLElement | null>(null);
const waitDisplay = ref<HTMLElement | null>(null);
const waitOverlay = ref<HTMLElement | null>(null);
const waitCountdown = ref<HTMLElement | null>(null);
const desktopMenu = ref<HTMLElement | null>(null);
const folderMenu = ref<HTMLElement | null>(null);
const fileMenu = ref<HTMLElement | null>(null);
const browserMenu = ref<HTMLElement | null>(null);
const terminalMenu = ref<HTMLElement | null>(null);
const focusMenu = ref<HTMLElement | null>(null);
const bubbleEl = ref<HTMLElement | null>(null);
const bubbleIconSlot = ref<HTMLElement | null>(null);
const bubbleTextSlot = ref<HTMLElement | null>(null);
const mousePointer = ref<HTMLElement | null>(null);

const mouseIcon = new URL('../../../icons/mouse-pointer-2.svg', import.meta.url).href;

const assets = {
  brainIcon: new URL('../../../icons/brain.svg', import.meta.url).href,
  folderIcon: new URL('../../../icons/folder.svg', import.meta.url).href,
  folderOpenIcon: new URL('../../../icons/folder-open.svg', import.meta.url).href,
  fileIcon: new URL('../../../icons/file.svg', import.meta.url).href,
  apps: {
    browser: new URL('../../../icons/globe.svg', import.meta.url).href,
    terminal: new URL('../../../icons/laptop.svg', import.meta.url).href,
    command: new URL('../../../icons/terminal.svg', import.meta.url).href,
    python: new URL('../../../icons/python.svg', import.meta.url).href,
    memory: new URL('../../../icons/sticky-note.svg', import.meta.url).href,
    todo: new URL('../../../icons/clipboard.svg', import.meta.url).href,
    subagent: new URL('../../../icons/bot.svg', import.meta.url).href
  }
};

const director = ref<MonitorDirector | null>(null);

const mountDirector = async () => {
  await nextTick();
  if (!screenEl.value) {
    return;
  }
  const elements: MonitorElements = {
    screen: screenEl.value,
    appsGrid: appsGrid.value!,
    desktopGrid: desktopGrid.value!,
    browserWindow: browserWindow.value!,
    browserSearchText: browserSearchText.value!,
    browserStatus: browserStatus.value!,
    browserResults: browserResults.value!,
    extractionWindow: extractionWindow.value!,
    extractionUrl: extractionUrl.value!,
    extractionState: extractionState.value!,
    extractionSummary: extractionSummary.value!,
    folderWindow: folderWindow.value!,
    folderHeaderText: folderHeaderText.value!,
    folderBody: folderBody.value!,
    editorWindow: editorWindow.value!,
    editorHeaderText: editorHeaderText.value!,
    editorBody: editorBody.value!,
    terminalWindow: terminalWindow.value!,
    terminalHeaderText: terminalHeaderText.value!,
    terminalTabs: terminalTabs.value!,
    terminalTabList: terminalTabList.value!,
    terminalAddButton: terminalAddButton.value!,
    terminalBody: terminalBody.value!,
    commandWindow: commandWindow.value!,
    commandTitle: commandTitle.value!,
    commandInput: commandInput.value!,
    commandOutput: commandOutput.value!,
    pythonWindow: pythonWindow.value!,
    pythonTitle: pythonTitle.value!,
    pythonBody: pythonBody.value!,
    pythonInput: pythonInput.value!,
    pythonOutput: pythonOutput.value!,
    readerWindow: readerWindow.value!,
    readerTitle: readerTitle.value!,
    readerLines: readerLines.value!,
    readerOcr: readerOcr.value!,
    memoryWindow: memoryWindow.value!,
    memoryList: memoryList.value!,
    memoryStatus: memoryStatus.value!,
    memoryCount: memoryCount.value!,
    memoryTime: memoryTime.value!,
    todoWindow: todoWindow.value!,
    todoSummary: todoSummary.value!,
    todoList: todoList.value!,
    waitWindow: waitWindow.value!,
    waitDisplay: waitDisplay.value!,
    waitOverlay: waitOverlay.value!,
    waitCountdown: waitCountdown.value!,
    desktopMenu: desktopMenu.value!,
    folderMenu: folderMenu.value!,
    fileMenu: fileMenu.value!,
    focusMenu: focusMenu.value!,
    browserMenu: browserMenu.value!,
    terminalMenu: terminalMenu.value!,
    speechBubble: bubbleEl.value!,
    bubbleIconSlot: bubbleIconSlot.value!,
    bubbleTextSlot: bubbleTextSlot.value!,
    mousePointer: mousePointer.value!
  };
  const instance = new MonitorDirector(elements, assets);
  director.value = instance;
  monitorStore.registerDriver(instance);
  // 确保初次挂载或刷新后切换到监视器时桌面根目录立即渲染
  if (Array.isArray(monitorStore.lastTreeSnapshot) && monitorStore.lastTreeSnapshot.length) {
    instance.setDesktopRoots(monitorStore.lastTreeSnapshot, { immediate: true });
  }
};

onMounted(() => {
  mountDirector();
});

// 监听桌面根目录变化，确保文件/文件夹实时渲染（包括刷新后首帧）
watch(
  lastTreeSnapshot,
  (roots) => {
    if (director.value && Array.isArray(roots)) {
      director.value.setDesktopRoots(roots, { immediate: true });
    }
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  monitorStore.unregisterDriver();
  director.value?.destroy();
  director.value = null;
});
</script>
