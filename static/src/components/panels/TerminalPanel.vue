<template>
  <aside class="terminal-panel" :style="{ width: width + 'px' }">
    <!-- 顶部栏 -->
    <header class="terminal-panel__header">
      <div class="terminal-panel__tabs">
        <div v-if="!sessionKeys.length" class="terminal-panel__empty-tab">
          <span>{{ $t('shell.waitingTerminalSession') }}</span>
        </div>
        <button
          v-for="name in sessionKeys"
          :key="name"
          type="button"
          class="terminal-panel__tab"
          :class="{ active: name === activeSession }"
          :title="sessions[name]?.working_dir || name"
          @click="switchToSession(name)"
        >
          <span class="terminal-panel__tab-name">{{ name }}</span>
        </button>
      </div>
      <CloseButton :label="$t('shell.closeTerminalPanel')" @click="$emit('close')" />
    </header>

    <!-- 终端容器 -->
    <div class="terminal-panel__body" ref="terminalContainer">
      <div v-if="!sessionKeys.length" class="terminal-panel__idle">
        <svg class="terminal-panel__idle-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <rect x="2" y="3" width="20" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
          <path d="M6 7h12M6 10h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span>{{ $t('shell.noOpenTerminals') }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';
import CloseButton from '@/components/common/CloseButton.vue';

defineOptions({ name: 'TerminalPanel' });

const props = defineProps<{
  width: number;
  workspaceId?: string;
  conversationId?: string;
}>();

const emit = defineEmits<{
  (event: 'close'): void;
}>();

// ---- 本地状态（REST 轮询驱动，不依赖父组件） ----
const sessions = ref<Record<string, { working_dir?: string; shell?: string }>>({});
const activeSession = ref('');
const sessionLogs = ref<Record<string, string>>({});
const sessionHydrated = ref<Record<string, boolean>>({});

const terminalContainer = ref<HTMLElement | null>(null);
let term: Terminal | null = null;
let themeObserver: MutationObserver | null = null;
let resizeObserver: ResizeObserver | null = null;
const _historyReady = ref<Record<string, boolean>>({});
let listPollTimer: number | null = null;
let outputPollTimer: number | null = null;

const sessionKeys = computed(() => Object.keys(sessions.value));

// ---- 主题适配 ----
function getCurrentTheme(): 'light' | 'dark' {
  const theme = document.documentElement.getAttribute('data-theme');
  return theme === 'dark' ? 'dark' : 'light';
}

function lightTerminalTheme() {
  return {
    background: '#ffffff',
    foreground: '#1f1f1f',
    cursor: '#3d3929',
    black: '#1f1f1f',
    red: '#c04a2f',
    green: '#4b8f60',
    yellow: '#b48a2c',
    blue: '#4a6ea9',
    magenta: '#9b4d88',
    cyan: '#2a8c8c',
    white: '#f4f0ea',
    brightBlack: '#6b6b6b',
    brightRed: '#d94a3a',
    brightGreen: '#5c9e6f',
    brightYellow: '#c49a3c',
    brightBlue: '#5a7eb9',
    brightMagenta: '#ab5d98',
    brightCyan: '#3a9c9c',
    brightWhite: '#faf9f5'
  };
}

function darkTerminalTheme() {
  return {
    background: '#181818',
    foreground: '#d4d4d8',
    cursor: '#d4d4d8',
    black: '#181818',
    red: '#f87171',
    green: '#4ade80',
    yellow: '#fbbf24',
    blue: '#60a5fa',
    magenta: '#c084fc',
    cyan: '#22d3ee',
    white: '#e4e4e7',
    brightBlack: '#3f3f46',
    brightRed: '#fb7185',
    brightGreen: '#86efac',
    brightYellow: '#fcd34d',
    brightBlue: '#93c5fd',
    brightMagenta: '#d8b4fe',
    brightCyan: '#67e8f9',
    brightWhite: '#fafafa'
  };
}

function applyTerminalTheme() {
  if (!term) return;
  const isDark = getCurrentTheme() === 'dark';
  term.options.theme = isDark ? darkTerminalTheme() : lightTerminalTheme();
}

// ---- 初始化 xterm ----
function initTerminal() {
  if (!terminalContainer.value) return;

  const isDark = getCurrentTheme() === 'dark';
  const theme = isDark ? darkTerminalTheme() : lightTerminalTheme();

  term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: '"JetBrains Mono", "SF Mono", "Fira Code", "Consolas", monospace',
    theme,
    scrollback: 10000,
    convertEol: true,
    allowTransparency: false,
    disableStdin: true
  });

  term.open(terminalContainer.value);

  // 持续自适应：手动 resize 而不是用 FitAddon（FitAddon 会操作 CSS 导致闪）
  resizeObserver = new ResizeObserver(() => {
    if (!term || !terminalContainer.value) return;
    const rect = terminalContainer.value.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) return;
    // 估算字符宽高，计算行列数
    const charW = term._core._renderService.dimensions.css.cell.width || 7.8;
    const charH = term._core._renderService.dimensions.css.cell.height || 17;
    const cols = Math.max(2, Math.floor((rect.width - 16) / charW));
    const rows = Math.max(1, Math.floor((rect.height - 16) / charH));
    if (cols !== term.cols || rows !== term.rows) {
      term.resize(cols, rows);
    }
  });
  resizeObserver.observe(terminalContainer.value);

  themeObserver = new MutationObserver(() => applyTerminalTheme());
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme']
  });
}

function disposeTerminal() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  themeObserver?.disconnect();
  themeObserver = null;
  term?.dispose();
  term = null;
}

// ---- 终端渲染 ----
// 追加写入终端（不 clear，避免闪烁）
function appendToTerm(data: string) {
  if (term && activeSession.value) {
    term.write(data);
  }
}

// 清屏 + 写入完整日志（仅切换会话 / 历史加载时用）
function renderSessionLog() {
  if (!term || !activeSession.value) return;
  const log = sessionLogs.value[activeSession.value] || '';
  term.clear();
  if (log) {
    term.write(log, () => {
      term?.scrollToBottom();
    });
  }
}

function switchToSession(name: string) {
  if (!name) return;
  activeSession.value = name;
  if (!sessionHydrated.value[name]) {
    _historyReady.value = { ..._historyReady.value, [name]: false };
  }
  // 切到新会话立即拉一次输出（不等下一轮轮询）
  void fetchActiveOutput();
  // renderSessionLog 由 watch(activeSession) 统一触发，此处不重复调用
}

// ---- watchers ----
watch(activeSession, (val) => {
  if (term) renderSessionLog();
});

watch(sessionKeys, (keys) => {
  if (keys.length > 0 && term && !activeSession.value) {
    switchToSession(keys[0]);
  }
});

// 工作区/对话切换时重置状态并重新拉取（对话级 terminal：各对话 shell 互相独立）
function resetPanelState() {
  sessions.value = {};
  activeSession.value = '';
  sessionLogs.value = {};
  sessionHydrated.value = {};
  _historyReady.value = {};
  if (term) term.clear();
}

watch(() => props.workspaceId, (newId, oldId) => {
  if (newId && oldId && newId !== oldId) {
    resetPanelState();
    void fetchTerminalList();
  }
});

watch(() => props.conversationId, (newId, oldId) => {
  if (newId !== oldId) {
    resetPanelState();
    void fetchTerminalList();
  }
});

// ---- REST 轮询（替代原 WebSocket 订阅与事件推送） ----
async function fetchTerminalList() {
  try {
    const cid = encodeURIComponent(props.conversationId || '');
    const res = await fetch(`/api/terminals?conversation_id=${cid}`, { cache: 'no-store' });
    if (!res.ok) return;
    const data = await res.json();
    const list = Array.isArray(data?.sessions) ? data.sessions : [];
    const map: Record<string, { working_dir?: string; shell?: string }> = {};
    for (const t of list) {
      const name = t.session_name || t.name || t.session || t.id;
      if (name) map[name] = { working_dir: t.working_dir, shell: t.shell || 'bash' };
    }
    sessions.value = map;
    const keys = Object.keys(map);
    if (keys.length === 0) {
      activeSession.value = '';
    } else if (!activeSession.value || !map[activeSession.value]) {
      // 新终端出现 / 当前终端被关闭：自动切换
      switchToSession(keys[0]);
    }
  } catch {
    // 断线判定由全局连接心跳负责，轮询静默失败
  }
}

async function fetchActiveOutput() {
  const s = activeSession.value;
  if (!s) return;
  try {
    const cid = encodeURIComponent(props.conversationId || '');
    const res = await fetch(
      `/api/terminals/${encodeURIComponent(s)}/output?lines=1000&conversation_id=${cid}`,
      { cache: 'no-store' }
    );
    if (!res.ok) return;
    const data = await res.json();
    if (!data?.success) return;
    const output = typeof data.output === 'string' ? data.output : '';
    applyOutputSnapshot(s, output);
  } catch {
    // 静默失败，下一轮重试
  }
}

// 快照驱动渲染：输出是纯追加时只写增量（避免闪烁）；
// reset/截断/窗口滚动导致前缀不匹配时全量重绘。
function applyOutputSnapshot(session: string, output: string) {
  const prev = sessionLogs.value[session] || '';
  sessionHydrated.value = { ...sessionHydrated.value, [session]: true };
  _historyReady.value = { ..._historyReady.value, [session]: true };
  if (output === prev) return;
  if (prev && output.startsWith(prev)) {
    const delta = output.slice(prev.length);
    sessionLogs.value[session] = output;
    if (session === activeSession.value) appendToTerm(delta);
  } else {
    sessionLogs.value[session] = output;
    if (session === activeSession.value) renderSessionLog();
  }
}

function startPolling() {
  stopPolling();
  void fetchTerminalList();
  listPollTimer = window.setInterval(() => void fetchTerminalList(), 5000);
  outputPollTimer = window.setInterval(() => void fetchActiveOutput(), 1500);
}

function stopPolling() {
  if (listPollTimer !== null) {
    window.clearInterval(listPollTimer);
    listPollTimer = null;
  }
  if (outputPollTimer !== null) {
    window.clearInterval(outputPollTimer);
    outputPollTimer = null;
  }
}

// ---- 生命周期 ----
onMounted(() => {
  nextTick(() => {
    initTerminal();
    startPolling();
  });
});

onBeforeUnmount(() => {
  stopPolling();
  disposeTerminal();
});
</script>

<style scoped>
.terminal-panel {
  display: flex;
  flex-direction: column;
  background: var(--surface-rail);
  border-left: 1px solid var(--border-default);
  color: var(--text-primary);
  height: 100%;
  overflow: hidden;
}

.terminal-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 38px;
  padding: 0 8px;
  border-bottom: 1px solid var(--border-default);
  background: var(--surface-rail);
  flex-shrink: 0;
}

.terminal-panel__tabs {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  flex: 1;
  min-width: 0;
  scrollbar-width: none;
}

.terminal-panel__tabs::-webkit-scrollbar {
  display: none;
}

.terminal-panel__empty-tab {
  display: flex;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.terminal-panel__tab {
  display: flex;
  align-items: center;
  height: 28px;
  padding: 0 12px;
  background: transparent;
  border: none;
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.12s, color 0.12s;
  flex-shrink: 0;
}

.terminal-panel__tab:hover {
  background: var(--theme-tab-active);
  color: var(--text-primary);
}

.terminal-panel__tab.active {
  background: var(--theme-tab-active);
  color: var(--text-primary);
}

[data-theme='dark'] .terminal-panel__tab:hover,
[data-theme='dark'] .terminal-panel__tab.active {
  background: var(--hover-bg);
}

.terminal-panel__tab-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.terminal-panel__body {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}

.terminal-panel__idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 8px;
  color: var(--text-muted);
  font-size: 13px;
}

.terminal-panel__idle-svg {
  width: 36px;
  height: 36px;
  color: var(--text-tertiary);
  opacity: 0.55;
}

.terminal-panel__body :deep(.xterm) {
  width: 100% !important;
  height: 100% !important;
  padding: 8px;
}

.terminal-panel__body :deep(.xterm-screen) {
  width: 100% !important;
  height: 100% !important;
}

.terminal-panel__body :deep(.xterm-viewport) {
  scrollbar-width: none;
}

.terminal-panel__body :deep(.xterm-viewport::-webkit-scrollbar) {
  display: none;
}
</style>
