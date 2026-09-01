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
import { io as createSocketClient } from 'socket.io-client';
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

// ---- 本地状态（自建 socket，不依赖父组件） ----
const sessions = ref<Record<string, { working_dir?: string; shell?: string }>>({});
const activeSession = ref('');
const sessionLogs = ref<Record<string, string>>({});
const sessionHydrated = ref<Record<string, boolean>>({});

let socket: ReturnType<typeof createSocketClient> | null = null;

const terminalContainer = ref<HTMLElement | null>(null);
let term: Terminal | null = null;
let themeObserver: MutationObserver | null = null;
let resizeObserver: ResizeObserver | null = null;
const _historyReady = ref<Record<string, boolean>>({});
// 历史加载完成后记录最后一条事件的时间戳，用于去重刷新后回放的旧 terminal_output 事件
const _historyLastEventTime = ref<Record<string, number>>({});

const sessionKeys = computed(() => Object.keys(sessions.value));

// 对话级隔离：广播类终端事件（started/list_update/output/input/closed/reset/switched）
// 由后端注入 conversation_id，仅处理当前对话的事件；
// 响应类事件（terminal_subscribed / *_history）是请求的直接回应，无此字段，不过滤。
function acceptTerminalBroadcast(data: any): boolean {
  const cid = data?.conversation_id;
  if (!cid || !props.conversationId) return false;
  return cid === props.conversationId;
}

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
    const updatedTimes = { ..._historyLastEventTime.value };
    delete updatedTimes[name];
    _historyLastEventTime.value = updatedTimes;
    if (socket?.connected) {
      socket.emit('get_terminal_output', { session: name, lines: 0, conversation_id: props.conversationId });
    }
  }
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

// 工作区切换时重连
watch(() => props.workspaceId, (newId, oldId) => {
  if (newId && oldId && newId !== oldId) {
    // 断开旧连接，清空状态，重新连接
    if (socket) {
      socket.disconnect();
      socket = null;
    }
    sessions.value = {};
    activeSession.value = '';
    sessionLogs.value = {};
    sessionHydrated.value = {};
    _historyReady.value = {};
    _historyLastEventTime.value = {};
    if (term) term.clear();
    initSocket();
  }
});

// 对话切换时重置并重新订阅（对话级 terminal：各对话 shell 互相独立）
watch(() => props.conversationId, (newId, oldId) => {
  if (newId !== oldId) {
    sessions.value = {};
    activeSession.value = '';
    sessionLogs.value = {};
    sessionHydrated.value = {};
    _historyReady.value = {};
    _historyLastEventTime.value = {};
    if (term) term.clear();
    if (socket?.connected) {
      socket.emit('terminal_subscribe', { all: true, conversation_id: newId });
    }
  }
});

// ---- socket 初始化 ----
async function initSocket() {

  socket = createSocketClient('/', {
    transports: ['websocket', 'polling'],
    autoConnect: false
  });

  const assignToken = async (): Promise<boolean> => {
    const w = window as any;
    if (typeof w.requestSocketToken !== 'function') {
      console.warn('[TerminalPanel] requestSocketToken 不可用');
      return false;
    }
    try {
      const token = await w.requestSocketToken();
      (socket as any).auth = { socket_token: token };
      return true;
    } catch (e) {
      console.error('[TerminalPanel] 获取 token 失败:', e);
      return false;
    }
  };

  socket.io.on('reconnect_attempt', () => assignToken());

  const ready = await assignToken();
  if (!ready) {
    console.error('[TerminalPanel] token 获取失败，放弃 socket');
    socket = null;
    return;
  }

  socket.on('connect', () => {
    socket!.emit('terminal_subscribe', { all: true, conversation_id: props.conversationId });
  });

  socket.on('disconnect', () => {
  });

  socket.on('terminal_subscribed', (data: any) => {
    if (data?.terminals) {
      const map: Record<string, { working_dir?: string; shell?: string }> = {};
      for (const t of data.terminals) {
        map[t.name] = { working_dir: t.working_dir, shell: 'bash' };
      }
      sessions.value = map;
      if (data.terminals.length > 0 && !activeSession.value) {
        switchToSession(data.terminals[0].name);
      }
    }
  });

  socket.on('terminal_started', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    sessions.value = {
      ...sessions.value,
      [data.session]: { working_dir: data.working_dir, shell: 'bash' }
    };
    if (!activeSession.value) switchToSession(data.session);
  });

  socket.on('terminal_list_update', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    if (data?.terminals) {
      const map: Record<string, { working_dir?: string; shell?: string }> = {};
      for (const t of data.terminals) {
        map[t.name] = { working_dir: t.working_dir, shell: 'bash' };
      }
      sessions.value = map;
      if (data.active && !activeSession.value) switchToSession(data.active);
    }
  });

  socket.on('terminal_output', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    const s = data.session;
    if (!s) return;
    const delta = (data.data || '') as string;

    // 去重：刷新页面后后端会回放历史快照（terminal_output_history）
    // 同时可能回放最近的 terminal_output 事件，导致已有内容被重复写入。
    // 用历史快照的 last_event_time 作为截止线，跳过更早的事件。
    const historyTime = _historyLastEventTime.value[s] || 0;
    const eventTime = typeof data.timestamp === 'number' ? data.timestamp : 0;
    if (historyTime > 0 && eventTime > 0 && eventTime <= historyTime) {
      return;
    }

    // 先把输出暂存到 sessionLogs；历史加载完成后再写入终端，
    // 避免新终端历史为空时 _historyReady 始终为 false 导致永远写不进去。
    sessionLogs.value[s] = (sessionLogs.value[s] || '') + delta;
    if (s === activeSession.value && term && _historyReady.value[s]) {
      term.write(delta);
    } else {
    }
    if (!activeSession.value) switchToSession(s);
  });

  socket.on('terminal_input', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    // 原样显示后端输出，此处不重复写入
    // 仅用于新终端自动切换
    if (!activeSession.value && data.session) switchToSession(data.session);
  });

  socket.on('terminal_closed', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    const updated = { ...sessions.value };
    delete updated[data.session];
    sessions.value = updated;
    const remaining = Object.keys(updated);
    if (activeSession.value === data.session) {
      activeSession.value = remaining.length > 0 ? remaining[0] : '';
    }
  });

  socket.on('terminal_reset', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    const target = data.session || activeSession.value;
    if (target) {
      sessionLogs.value = { ...sessionLogs.value, [target]: '' };
      sessionHydrated.value = { ...sessionHydrated.value, [target]: true };
      const updatedTimes = { ..._historyLastEventTime.value };
      delete updatedTimes[target];
      _historyLastEventTime.value = updatedTimes;
      if (target === activeSession.value) renderSessionLog();
    }
  });

  socket.on('terminal_switched', (data: any) => {
    if (!acceptTerminalBroadcast(data)) return;
    if (data.current) switchToSession(data.current);
  });

  const handleHistory = (data: any) => {
    const s = data.session || data.active;
    if (!s) return;
    const raw = data.output || data.data || '';
    let payload = '';
    if (typeof raw === 'string') {
      payload = raw;
    } else if (Array.isArray(raw)) {
      payload = raw.join('\n');
    }
    if (payload) {
      sessionLogs.value[s] = payload;
    }
    // 无论历史是否为空，都要标记加载完成；否则新建终端在收到第一条输出前
    // _historyReady 一直为 false，所有实时输出都会被跳过，终端 seemingly 卡住。
    sessionHydrated.value = { ...sessionHydrated.value, [s]: true };
    _historyReady.value = { ..._historyReady.value, [s]: true };
    // 记录历史快照最后一条事件的时间戳，用于后续 terminal_output 去重
    const lastEventTime = typeof data.last_event_time === 'number' ? data.last_event_time : 0;
    _historyLastEventTime.value = { ..._historyLastEventTime.value, [s]: lastEventTime };
    if (s === activeSession.value) {
      renderSessionLog();
    }
  };

  socket.on('terminal_output_history', handleHistory);
  socket.on('terminal_history', handleHistory);

  socket.connect();
}

// ---- 生命周期 ----
onMounted(() => {
  nextTick(() => {
    initTerminal();
    initSocket();
  });
});

onBeforeUnmount(() => {
  disposeTerminal();
  if (socket) {
    socket.disconnect();
    socket = null;
  }
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
