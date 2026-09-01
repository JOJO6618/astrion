<template>
  <section
    v-if="renderVisible"
    class="qd-detail"
    :class="{ 'panel-enter': entering, 'panel-leave': leaving }"
  >
    <header class="qd-detail__header">
      <span class="qd-detail__dot" :class="`is-${stateClass}`"></span>
      <span class="qd-detail__title" :title="title">{{ title }}</span>
      <span class="qd-detail__badge" :class="`is-${stateClass}`">{{ statusText }}</span>
      <span v-if="tokensText" class="qd-detail__tokens" :title="tokensTitle">{{ tokensText }}</span>
      <span class="qd-detail__close">
        <CloseButton :label="$t('common.close')" :title="$t('common.close')" @click="close" />
      </span>
    </header>
    <div ref="bodyRef" class="qd-detail__body" :class="{ 'body-fade': bodyFading }">
      <!-- 子智能体：工具调用 + 文本输出时间线 -->
      <template v-if="effectiveDetail?.kind === 'agent'">
        <div v-if="!timelineItems.length" class="qd-detail__empty">
          {{ activityLoading ? $t('common.loading') : $t('quickdock.noProgress') }}
        </div>
        <div
          v-for="item in timelineItems"
          :key="item.key"
          class="qd-feed-row"
          :class="[item.kind === 'tool' ? 'feed-tool' : item.kind === 'compression' ? 'feed-compressed' : 'feed-text', { 'is-new': animatedKeys.has(item.key) }]"
        >
          <template v-if="item.kind === 'tool'">
            <span class="tool-name">{{ item.toolName }}</span>
            <span class="tool-param" :title="item.text">{{ item.text }}</span>
            <span class="tool-status" :class="{ 'is-error': item.state === 'failed' }">
              <span v-if="item.state === 'running' || item.state === 'calling'" class="qd-tool-spinner"></span>
              <span v-else class="qd-tool-done">{{ item.state === 'failed' ? '✕' : '✓' }}</span>
            </span>
          </template>
          <template v-else-if="item.kind === 'compression'">
            <span class="feed-compressed__label">
              <svg class="feed-compressed__icon" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="M8 1.5 13.5 4v4.5c0 3-2.2 5-5.5 6-3.3-1-5.5-3-5.5-6V4L8 1.5Z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
                <path d="M5.5 8.2 7.2 9.9 11 5.8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              {{ t('quickdock.contextCompressed', { round: item.round }) }}
            </span>
          </template>
          <template v-else>{{ item.content }}</template>
        </div>
      </template>

      <!-- 后台指令：终端输出行 -->
      <template v-else>
        <div v-if="!outputLines.length" class="qd-detail__empty">
          {{ detailLoading ? $t('common.loading') : $t('quickdock.noOutput') }}
        </div>
        <div
          v-for="(line, i) in outputLines"
          :key="i"
          class="qd-feed-row feed-term"
          :class="{ 'is-new': animatedKeys.has(`term-${i}`) }"
        >
          {{ line }}
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { t, currentLocale } from '@/locales';
import { useQuickDockStore } from '@/stores/quickDock';
import { useSubAgentStore } from '@/stores/subAgent';
import { useBackgroundCommandStore } from '@/stores/backgroundCommand';

/**
 * 详情面板（fixed 浮在快捷窗口列左侧）
 * - 子智能体：activity entries 时间线（工具行 + 文本行）
 * - 后台指令：终端输出行
 * - 新行自动播 feed-in 动画；距底 <80px 时新内容平滑滚到底（用户上翻不打断）
 */

const AGENT_TERMINAL = new Set(['completed', 'failed', 'timeout', 'terminated']);
const CMD_TERMINAL = new Set(['completed', 'failed', 'timeout', 'cancelled']);

const quickDock = useQuickDockStore();
const subAgentStore = useSubAgentStore();
const bgStore = useBackgroundCommandStore();

const { detail } = storeToRefs(quickDock);
const { activityEntries, activityLoading } = storeToRefs(subAgentStore);
const { activeDetail, detailLoading } = storeToRefs(bgStore);

/** 最后一个非 null 的 detail：关闭时 detail 立即变 null，但离开动画仍播放 190ms，
 *  期间所有状态显示改用此快照，避免角标/标题闪变为「已结束」/空。 */
const lastDetail = ref<{ kind: 'agent' | 'cmd'; id: string } | null>(null);
watch(
  detail,
  (val) => {
    if (val) {
      lastDetail.value = { kind: val.kind, id: val.id };
    }
  },
  { immediate: true }
);
const effectiveDetail = computed(() => detail.value || lastDetail.value);

/** 最后一次非空实时状态：切换对话/关闭面板时 store 列表被替换，实时状态取空，
 *  离开动画期间回退到此快照，避免空状态被误判为「运行中」。 */
const lastStatus = ref('');

/** 实时状态（面板打开期间随 store 刷新） */
const liveStatus = computed(() => {
  const target = effectiveDetail.value;
  if (!target) {
    return '';
  }
  if (target.kind === 'agent') {
    const agent = subAgentStore.subAgents.find((a) => a.task_id === target.id);
    const active =
      subAgentStore.activeAgent?.task_id === target.id ? subAgentStore.activeAgent : null;
    return String(active?.status || agent?.status || '');
  }
  const cmd = bgStore.commands.find((c) => c.command_id === target.id);
  const active = bgStore.activeCommand?.command_id === target.id ? bgStore.activeCommand : null;
  return String(active?.status || cmd?.status || '');
});
watch(
  liveStatus,
  (val) => {
    if (val) {
      lastStatus.value = val;
    }
  },
  { immediate: true }
);

const renderVisible = ref(false);
const entering = ref(false);
const leaving = ref(false);
const bodyFading = ref(false);
const bodyRef = ref<HTMLElement | null>(null);

/** 当前条目状态与标题（关闭离开动画期间沿用最后快照） */
const currentStatus = computed(() => {
  return liveStatus.value || lastStatus.value || '';
});

const isRunning = computed(() => {
  const status = currentStatus.value.toLowerCase();
  if (!effectiveDetail.value || !status) {
    // 状态未知（快照也没有）时不应显示「运行中」
    return false;
  }
  // idle（空闲）不是运行中：子智能体等待唤醒，不参与运行态展示
  if (status === 'idle') {
    return false;
  }
  return !(effectiveDetail.value.kind === 'agent' ? AGENT_TERMINAL : CMD_TERMINAL).has(status);
});

/** 状态分类：running=运行中 / idle=空闲 / done=完成 / ended=失败·超时·终止 */
const stateClass = computed(() => {
  const status = currentStatus.value.toLowerCase();
  if (status === 'idle') {
    return 'idle';
  }
  if (isRunning.value) {
    return 'running';
  }
  if (status === 'completed') {
    return 'done';
  }
  return 'ended';
});

const title = computed(() => {
  void currentLocale.value;
  if (!effectiveDetail.value) {
    return '';
  }
  if (effectiveDetail.value.kind === 'agent') {
    const agent = subAgentStore.subAgents.find((a) => a.task_id === effectiveDetail.value?.id);
    return (
      agent?.display_name || agent?.summary || subAgentStore.activeAgent?.display_name || t('quickdock.subAgent')
    );
  }
  const cmd = bgStore.commands.find((c) => c.command_id === effectiveDetail.value?.id);
  return cmd?.command || bgStore.activeCommand?.command || t('quickdock.backgroundCommand');
});

const statusText = computed(() => {
  void currentLocale.value;
  const status = currentStatus.value.toLowerCase();
  if (status === 'idle') {
    return t('quickdock.statusIdle');
  }
  if (isRunning.value) {
    return t('common.running');
  }
  if (status === 'completed') {
    return t('quickdock.statusCompleted');
  }
  if (status === 'failed') {
    return t('quickdock.statusFailed');
  }
  if (status === 'timeout') {
    return t('quickdock.statusTimeout');
  }
  if (status === 'terminated' || status === 'cancelled') {
    return t('quickdock.statusTerminated');
  }
  return t('quickdock.statusEnded');
});

/* ---------------- 子智能体上下文 token（数据源：/api/sub_agents 列表轮询） ---------------- */

/** 实时 token：列表 5s 轮询携带 current_context_tokens（与被移除的旧工作区面板同一字段） */
const liveTokens = computed(() => {
  const target = effectiveDetail.value;
  if (!target || target.kind !== 'agent') {
    return 0;
  }
  const agent = subAgentStore.subAgents.find((a) => a.task_id === target.id);
  const tokens = Number(agent?.current_context_tokens);
  return Number.isFinite(tokens) && tokens > 0 ? tokens : 0;
});
/** 关闭离开动画期间列表可能被替换取空，沿用最后快照避免闪烁（同 lastStatus） */
const lastTokens = ref(0);
watch(
  liveTokens,
  (val) => {
    if (val > 0) {
      lastTokens.value = val;
    }
  },
  { immediate: true }
);
const currentTokens = computed(() => liveTokens.value || lastTokens.value);

/** 单位用 Tokens：>=1000 显示 x.xk Tokens，否则 n Tokens */
function formatCtxTokens(tokens: number): string {
  if (!tokens || tokens <= 0) {
    return '';
  }
  if (tokens >= 1000) {
    return `${(tokens / 1000).toFixed(1)}k Tokens`;
  }
  return `${tokens} Tokens`;
}

const tokensText = computed(() => {
  if (effectiveDetail.value?.kind !== 'agent') {
    return '';
  }
  return formatCtxTokens(currentTokens.value);
});

const tokensTitle = computed(() => {
  void currentLocale.value;
  return t('quickdock.contextTokensTitle', { tokens: currentTokens.value.toLocaleString() });
});

/* ---------------- 子智能体时间线（逻辑借鉴 SubAgentActivityDialog） ---------------- */

function normalizeStatus(status?: string) {
  if (status === 'running' || status === 'in_progress') return 'running';
  if (status === 'calling') return 'calling';
  if (status === 'completed' || status === 'done' || status === 'success') return 'completed';
  if (status === 'failed' || status === 'error') return 'failed';
  return status || 'running';
}

function isEntryTerminal(status?: string) {
  const normalized = (status || '').toString().toLowerCase();
  return [
    'completed',
    'failed',
    'timeout',
    'terminated',
    'cancelled',
    'done',
    'success',
    'error'
  ].includes(normalized);
}

function buildText(entry: any): string {
  const tool = entry.tool || '';
  const args = entry.args || {};
  if (tool === 'read_file') return t('quickdock.toolReadFile', { path: args.path || args.file_path || '' });
  if (tool === 'write_file') return t('quickdock.toolWriteFile', { path: args.file_path || args.path || '' });
  if (tool === 'read_skill') return t('quickdock.toolReadSkill', { name: args.skill_name || '' });
  if (tool === 'web_search') return t('quickdock.toolSearch', { query: args.query || args.q || '' });
  if (tool === 'extract_webpage') return t('quickdock.toolExtract', { url: args.url || '' });
  if (tool === 'run_command') return t('quickdock.toolRunCommand', { command: args.command || '' });
  if (tool === 'edit_file') return t('quickdock.toolEditFile', { path: args.path || args.file_path || '' });
  if (tool === 'read_mediafile') return t('quickdock.toolReadMedia', { path: args.path || args.file_path || '' });
  return tool || t('common.tool');
}

interface ToolTimelineItem {
  kind: 'tool';
  key: string;
  state: string;
  toolName: string;
  text: string;
}

interface OutputTimelineItem {
  kind: 'output';
  key: string;
  content: string;
}

interface CompressionTimelineItem {
  kind: 'compression';
  key: string;
  round: number;
}

const timelineItems = computed<(ToolTimelineItem | OutputTimelineItem | CompressionTimelineItem)[]>(() => {
  void currentLocale.value;
  const entries = activityEntries.value || [];
  const rawItems: ({ kind: 'tool'; key: string; entry: any } | OutputTimelineItem | CompressionTimelineItem)[] = [];
  let currentToolGroup: { kind: 'tool'; key: string; entry: any } | null = null;

  const flushToolGroup = () => {
    if (currentToolGroup) {
      rawItems.push(currentToolGroup);
      currentToolGroup = null;
    }
  };

  entries.forEach((entry: any, index: number) => {
    if (
      entry?.type === 'progress' &&
      entry?.subtype === 'output' &&
      typeof entry.content === 'string'
    ) {
      flushToolGroup();
      // 语义顺序修正：工具「正在调用」事件在流式期间先于文本 output 落盘，
      // 但 assistant 消息里文本永远在 tool_calls 之前——把 output 插入到
      // 尾部连续的非终态工具条目（同一条 assistant 消息发起的调用）之前
      const outputItem: OutputTimelineItem = {
        kind: 'output',
        key: `output-${entry.ts || index}`,
        content: entry.content
      };
      let cut = rawItems.length;
      while (cut > 0) {
        const tail = rawItems[cut - 1];
        if (tail.kind === 'tool' && !isEntryTerminal(tail.entry?.status)) {
          cut -= 1;
        } else {
          break;
        }
      }
      rawItems.splice(cut, 0, outputItem);
      return;
    }

    // 上下文压缩提示：在压缩发生的位置插入一条提示条目
    if (entry?.type === 'progress' && entry?.subtype === 'compression') {
      flushToolGroup();
      rawItems.push({
        kind: 'compression',
        key: `compression-${entry.ts || index}`,
        round: Number(entry.round) || 0,
      });
      return;
    }

    if (!entry || entry.type !== 'progress' || !entry.tool) {
      return;
    }

    const baseKey = entry.id || `${entry.tool}-${entry.ts || index}`;
    if (
      currentToolGroup &&
      (currentToolGroup.entry.id === entry.id || currentToolGroup.key === baseKey) &&
      !isEntryTerminal(currentToolGroup.entry.status)
    ) {
      currentToolGroup.entry = { ...currentToolGroup.entry, ...entry };
      return;
    }

    // 同一 tool_call id 的历史条目仍非终态时原地更新（「正在调用」事件可能与其他
    // 工具的调用事件交错到达），避免出现永远转圈的重复条目
    if (entry.id) {
      const prior = rawItems.find(
        (item) =>
          item.kind === 'tool' && item.entry.id === entry.id && !isEntryTerminal(item.entry.status)
      );
      if (prior && prior.kind === 'tool') {
        prior.entry = { ...prior.entry, ...entry };
        return;
      }
    }

    flushToolGroup();
    let key = baseKey;
    let suffix = 0;
    while (rawItems.some((item) => item.kind === 'tool' && item.key === key)) {
      suffix += 1;
      key = `${baseKey}--${suffix}`;
    }
    currentToolGroup = { kind: 'tool', key, entry: { ...entry } };
  });

  flushToolGroup();

  return rawItems.map((item) => {
    if (item.kind === 'output' || item.kind === 'compression') {
      return item;
    }
    const state = normalizeStatus(item.entry.status);
    return {
      kind: 'tool' as const,
      key: item.key,
      state,
      toolName: item.entry.tool || t('common.tool'),
      text: buildText(item.entry)
    };
  });
});

/* ---------------- 后台指令输出 ---------------- */

const outputLines = computed<string[]>(() => {
  if (effectiveDetail.value?.kind !== 'cmd') {
    return [];
  }
  const output = activeDetail.value?.output;
  if (typeof output !== 'string') {
    return [];
  }
  return output.split('\n');
});

/* ---------------- feed 行进入动画：仅面板打开期间新出现的行播放 ---------------- */
/** 打开/切换详情后首次填充的行直接静态显示；之后新出现的行才带 is-new 播动画 */
const animatedKeys = ref<Set<string>>(new Set());
let seenFeedKeys = new Set<string>();
let feedHydrated = false;

function resetFeedAnimState() {
  seenFeedKeys = new Set();
  feedHydrated = false;
  animatedKeys.value = new Set();
}

watch(
  [timelineItems, outputLines],
  () => {
    const keys =
      effectiveDetail.value?.kind === 'agent'
        ? timelineItems.value.map((t) => t.key)
        : outputLines.value.map((_, i) => `term-${i}`);
    if (!feedHydrated) {
      // 空列表不算首次填充：打开详情时 entries 会先被清空再拉取，
      // 若清空即消费首次机会，真实内容到达时会被滚动 watch 误判为
      // 「后续批次 + 距底<80」而发起平滑滚动（先上后下的滚动动画）。
      if (!keys.length) {
        return;
      }
      // 真实内容的首次填充：全部标记为已见，静态显示，
      // 并瞬间定位到底部（无滚动动画）
      feedHydrated = true;
      keys.forEach((k) => seenFeedKeys.add(k));
      nextTick(() => scrollToBottom(false));
      return;
    }
    const newKeys = keys.filter((k) => !seenFeedKeys.has(k));
    if (!newKeys.length) {
      return;
    }
    newKeys.forEach((k) => seenFeedKeys.add(k));
    animatedKeys.value = new Set(newKeys);
    // 动画播完后清理，避免后续 DOM 重建时重播
    setTimeout(() => {
      animatedKeys.value = new Set();
    }, 400);
  },
  { flush: 'post' }
);

/* ---------------- 数据联动：打开/切换/关闭详情 ---------------- */

watch(
  detail,
  (target, prev) => {
    if (!target) {
      // 关闭：播离开动画后隐藏；数据清理延迟到动画结束，
      // 避免离开期间内容区闪成「暂无进度」、角标闪变「已结束」
      const cleanup = () => {
        if (prev?.kind === 'agent') {
          subAgentStore.closeSubAgent();
        }
        if (prev?.kind === 'cmd') {
          bgStore.closeCommand();
        }
      };
      if (renderVisible.value) {
        entering.value = false;
        leaving.value = true;
        // setTimeout 而非 animationend：feed 行动画的 animationend 会冒泡干扰
        setTimeout(() => {
          leaving.value = false;
          renderVisible.value = false;
          cleanup();
        }, 190);
      } else {
        cleanup();
      }
      return;
    }

    // 切换条目：立即清理上一个条目的数据（避免旧内容留给新条目）
    if (prev?.kind === 'agent') {
      subAgentStore.closeSubAgent();
    }
    if (prev?.kind === 'cmd') {
      bgStore.closeCommand();
    }
    // 新条目的 feed 动画状态重置：首次填充静态显示
    resetFeedAnimState();

    // 加载详情数据（store 自带轮询，终态自动停止）。
    // silent：不设 activeAgent/activeCommand，避免触发旧版详情弹窗
    //（SubAgentActivityDialog / BackgroundCommandDialog 以 activeXxx 为显示条件）
    if (target.kind === 'agent') {
      const agent = subAgentStore.subAgents.find((a) => a.task_id === target.id);
      if (agent) {
        subAgentStore.openSubAgent(agent, { silent: true });
      }
    } else {
      const cmd = bgStore.commands.find((c) => c.command_id === target.id);
      if (cmd) {
        bgStore.openCommand(cmd, { silent: true });
      }
    }

    if (!renderVisible.value) {
      renderVisible.value = true;
      entering.value = true;
      setTimeout(() => {
        entering.value = false;
      }, 260);
      nextTick(() => {
        scrollToBottom(false);
      });
    } else {
      // 切换条目：内容区快速淡入
      bodyFading.value = false;
      nextTick(() => {
        bodyFading.value = true;
        scrollToBottom(false);
        setTimeout(() => {
          bodyFading.value = false;
        }, 200);
      });
    }
  },
  { immediate: true }
);

/* ---------------- 自动滚动：距底 <80px 时新内容滚到底 ---------------- */

function scrollToBottom(smooth: boolean) {
  const el = bodyRef.value;
  if (!el) {
    return;
  }
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
}

function maybeAutoScroll() {
  const el = bodyRef.value;
  if (!el) {
    return;
  }
  // 首次填充由 feed watch 瞬间定位到底，不走平滑滚动；
  // 平滑滚动只用于「已在底部时新步骤出现」的场景
  if (!feedHydrated) {
    return;
  }
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  if (nearBottom) {
    nextTick(() => scrollToBottom(true));
  }
}

watch(
  () => [timelineItems.value.length, outputLines.value.length],
  () => {
    maybeAutoScroll();
  }
);

function close() {
  quickDock.closeDetail();
}
</script>
