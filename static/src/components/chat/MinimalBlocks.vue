<template>
  <div class="minimal-blocks-container">
    <!-- 按真实顺序显示分组 -->
    <template v-for="group in blockGroups" :key="group.id">
      <!-- 摘要组（thinking/tool） -->
      <div v-if="group.type === 'summary' && group.actions" class="summary-group">
        <!-- 摘要行（只有文字，可点击展开） -->
        <div
          class="summary-line-text"
          :class="{ running: isSummaryRunning(group.actions, group.id) }"
          :data-group-id="group.id"
          @click="toggleExpand(group.id)"
        >
          <div class="summary-content-wrapper">
            <span class="summary-preview">
              <template v-if="shouldAnimateSummary(group.actions, group.id)">
                <span
                  v-if="shouldShowToolReel(group.actions, group.id)"
                  class="summary-tool-reel-window"
                >
                  <span
                    class="summary-tool-reel-track"
                    :class="getToolReelPhase(group.id)"
                    :style="getToolReelTrackStyle(group.id)"
                  >
                    <span
                      v-for="(item, idx) in getToolReelDisplayItems(group.actions, group.id)"
                      :key="`${group.id}-tool-reel-${idx}-${item}`"
                      class="summary-tool-reel-item"
                    >
                      {{ item }}
                    </span>
                  </span>
                </span>
                <template v-else>
                  <span
                    v-for="(char, idx) in getAnimatedSummaryChars(
                      getSummaryLineText(group.actions, group.id)
                    )"
                    :key="`${group.id}-${idx}`"
                    class="summary-char"
                    :style="{ animationDelay: `${(idx + 1) * 0.12}s` }"
                  >
                    {{ char === ' ' ? '\u00A0' : char }}
                  </span>
                </template>
              </template>
              <template v-else>
                {{ getSummaryLineText(group.actions, group.id) }}
              </template>
            </span>
          </div>
          <!-- 加载动画或完成图标 -->
          <div class="summary-status-icon">
            <div v-if="isSummaryRunning(group.actions, group.id)" class="summary-icon-inner">
              <component :is="getSummaryLoader(group.id)" />
            </div>
            <svg
              v-else
              class="check-icon"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="7.5 12 10.5 15 16.5 9" />
            </svg>
          </div>
        </div>

        <!-- 步骤容器（展开时显示所有步骤） -->
        <div class="steps-container" :class="{ show: expandedGroups.has(group.id) }">
          <div
            class="steps-wrapper"
            :class="{ 'height-limited': heightLimited }"
            :ref="(el) => registerStepsWrapper(group.id, el)"
          >
            <div v-for="step in getSummarySteps(group.actions)" :key="step.id" class="step-item">
              <div class="step-timeline">
                <span
                  class="step-icon icon icon-sm"
                  :style="getStepIconStyle(step)"
                  aria-hidden="true"
                ></span>
                <div class="step-line"></div>
              </div>
              <div class="step-content">
                <div v-if="step.type === 'thinking'" class="step-body">
                  <div
                    class="thinking-content"
                    :ref="(el) => registerThinking(step.id, el)"
                    @scroll="handleScroll(step.id, $event)"
                    v-html="renderContent(step.content)"
                  ></div>
                </div>
                <div v-else-if="step.type === 'tool'" class="step-body">
                  <div class="step-header">{{ getToolName(step.action) }}</div>
                  <div class="tool-intent" v-if="getToolIntent(step.action)">
                    {{ getToolIntent(step.action) }}
                  </div>
                  <div class="tool-result" v-if="step.action.tool?.result">
                    <div v-html="renderToolResult(step.action)"></div>
                  </div>
                  <div v-else class="tool-result">
                    <div class="result-item">执行中...</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 文本组 -->
      <div
        v-else-if="group.type === 'text'"
        class="text-output"
        :class="{ 'streaming-text': group.streaming }"
      >
        <div class="text-content" :class="{ 'streaming-text': group.streaming }">
          <MarkdownRenderer :content="group.content || ''" :is-streaming="group.streaming" />
        </div>
      </div>
      <div v-else-if="group.type === 'system'" class="summary-group sub-agent-system-group">
        <div class="summary-line-text sub-agent-system-summary-line">
          <div class="summary-content-wrapper">
            <span class="summary-preview">{{ group.content || '' }}</span>
          </div>
          <div class="summary-status-icon">
            <svg
              class="check-icon"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <circle cx="12" cy="12" r="10" />
              <polyline points="7.5 12 10.5 15 16.5 9" />
            </svg>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, nextTick, onBeforeUnmount, Component } from 'vue';
import { usePersonalizationStore } from '@/stores/personalization';
import { renderEnhancedToolResult } from './actions/toolRenderers';

const personalizationStore = usePersonalizationStore();
const heightLimited = computed(() => personalizationStore.form.minimal_expand_height_limited);
import { getRandomLoader } from './loaders/index';
import MarkdownRenderer from './MarkdownRenderer.vue';

interface Action {
  id?: string;
  type: 'thinking' | 'tool' | 'text' | 'system';
  content: string;
  streaming?: boolean;
  blockId?: string;
  tool?: {
    name?: string;
    intent?: string;
    intent_rendered?: string;
    intent_full?: string;
    status?: string;
    display_name?: string;
    arguments?: any;
    result?: any;
  };
}

interface Step {
  id: string;
  type: 'thinking' | 'tool';
  content: string;
  streaming: boolean;
  action: Action;
}

interface BlockGroup {
  type: 'summary' | 'text' | 'system';
  id: string;
  actions?: Action[];
  content?: string;
  streaming?: boolean;
}

const props = defineProps<{
  actions: Action[];
  conversationRunning?: boolean;
  isLatestMessage?: boolean;
  iconStyle?: (name: string) => any;
  getToolIcon?: (tool: any) => string;
  getToolStatusText?: (tool: any) => string;
  getToolDescription?: (tool: any) => string;
  formatSearchTopic?: (filters: Record<string, any>) => string;
  formatSearchTime?: (filters: Record<string, any>) => string;
  formatSearchDomains?: (filters: Record<string, any>) => string;
  registerThinkingRef?: (key: string, el: Element | null) => void;
  handleThinkingScroll?: (blockId: string, event: Event) => void;
}>();

const emit = defineEmits<{
  (event: 'group-toggle', payload: { groupId: string; expanded: boolean }): void;
}>();

const expandedGroups = ref(new Set<string>());
const thinkingRefs = new Map<string, HTMLElement>();
const stepsWrapperRefs = new Map<string, HTMLElement>();
const stepsWrapperScrollLocks = new Map<string, boolean>();
const summaryLoaders = new Map<string, Component>();
const STEPS_WRAPPER_BOTTOM_THRESHOLD = 20;
const TOOL_REEL_ITEM_HEIGHT = 26;
const TOOL_REEL_INTERVAL_MS = 1450;
const TOOL_REEL_ROLL_MS = 520;
const TOOL_REEL_SETTLE_MS = 170;
const TOOL_REEL_OVERSHOOT_PX = 2;

type ToolReelPhase = 'idle' | 'rolling' | 'settle';

interface ToolReelState {
  index: number;
  offsetPx: number;
  phase: ToolReelPhase;
  signature: string;
  items: string[];
  completing?: boolean;
}

const toolReelStates = reactive<Record<string, ToolReelState>>({});
const toolReelIntervals = new Map<string, number>();
const toolReelTimeouts = new Map<string, number[]>();

const getAnimatedSummaryChars = (text: string) => Array.from(text || '');

const getFirstLine = (raw: string) => {
  const text = typeof raw === 'string' ? raw : '';
  const firstLineEnd = text.indexOf('\n');
  return firstLineEnd > 0 ? text.substring(0, firstLineEnd) : text;
};

const getToolSummaryText = (action: Action) => {
  const tool = action.tool;
  if (!tool) return '';

  const intentEnabled = personalizationStore.form.tool_intent_enabled;
  const intentText = tool.intent_rendered || tool.intent_full || '';

  if (intentEnabled && intentText) {
    return getFirstLine(intentText);
  }

  if (tool.status === 'preparing') {
    return `准备调用 ${tool.name || '工具'}...`;
  }
  if (tool.status === 'running') {
    return `正在调用 ${tool.name || '工具'}...`;
  }
  if (tool.status === 'completed') {
    return tool.display_name || tool.name || '工具执行完成';
  }
  return action.streaming ? '正在执行工具...' : tool.display_name || tool.name || '执行工具';
};

const isActiveToolAction = (action: Action) => {
  if (action.type !== 'tool') return false;
  if (action.streaming) return true;

  const status = String(action.tool?.status || '').toLowerCase();
  return [
    'hinted',
    'preparing',
    'running',
    'pending',
    'pending_approval',
    'awaiting_approval',
    'awaiting_user_answer'
  ].includes(status);
};

const getLatestToolSegment = (actions: Action[]) => {
  let lastToolIndex = -1;
  for (let i = actions.length - 1; i >= 0; i--) {
    if (actions[i].type === 'tool') {
      lastToolIndex = i;
      break;
    }
    if (actions[i].type === 'thinking') {
      break;
    }
  }
  if (lastToolIndex < 0) return [];

  let startIndex = lastToolIndex;
  while (startIndex > 0 && actions[startIndex - 1].type === 'tool') {
    startIndex--;
  }

  return actions.slice(startIndex, lastToolIndex + 1);
};

const getLatestActiveToolSegment = (actions: Action[]) => {
  const segment = getLatestToolSegment(actions);
  return segment.some(isActiveToolAction) ? segment : [];
};

const getToolReelItems = (actions: Action[]) =>
  getLatestActiveToolSegment(actions)
    .map(getToolSummaryText)
    .filter((item) => item.trim().length > 0);

const getToolReelDisplayItems = (actions: Action[], groupId: string) => {
  const completingItems = toolReelStates[groupId]?.completing
    ? toolReelStates[groupId].items
    : null;
  if (completingItems?.length) {
    return completingItems;
  }

  const items = getToolReelItems(actions);
  return items.length > 0 ? [...items, items[0]] : [];
};

const shouldShowToolReel = (actions: Action[], groupId: string) =>
  !!toolReelStates[groupId]?.completing ||
  (isSummaryRunning(actions, groupId) && getToolReelItems(actions).length > 1);

const shouldAnimateSummary = (actions: Action[], groupId: string) =>
  isSummaryRunning(actions, groupId) || !!toolReelStates[groupId]?.completing;

const getToolReelPhase = (groupId: string) => toolReelStates[groupId]?.phase || 'idle';

const getToolReelTrackStyle = (groupId: string) => ({
  transform: `translateY(${toolReelStates[groupId]?.offsetPx || 0}px)`
});

const clearToolReelTimers = (groupId: string) => {
  const interval = toolReelIntervals.get(groupId);
  if (interval) {
    window.clearInterval(interval);
    toolReelIntervals.delete(groupId);
  }

  const timeouts = toolReelTimeouts.get(groupId) || [];
  timeouts.forEach((timeout) => window.clearTimeout(timeout));
  toolReelTimeouts.delete(groupId);
};

const pushToolReelTimeout = (groupId: string, timeout: number) => {
  const timeouts = toolReelTimeouts.get(groupId) || [];
  timeouts.push(timeout);
  toolReelTimeouts.set(groupId, timeouts);
};

const spinToolReel = (groupId: string, itemCount: number) => {
  const state = toolReelStates[groupId];
  if (!state || state.completing || itemCount < 2) return;

  const nextIndex = (state.index + 1) % itemCount;
  const visualIndex = nextIndex === 0 ? itemCount : nextIndex;
  const targetOffset = -visualIndex * TOOL_REEL_ITEM_HEIGHT;

  state.phase = 'rolling';
  state.offsetPx = targetOffset - TOOL_REEL_OVERSHOOT_PX;

  const settleTimeout = window.setTimeout(() => {
    state.phase = 'settle';
    state.offsetPx = targetOffset;
    state.index = nextIndex;

    if (nextIndex === 0) {
      const resetTimeout = window.setTimeout(() => {
        state.phase = 'idle';
        state.offsetPx = 0;
      }, TOOL_REEL_SETTLE_MS);
      pushToolReelTimeout(groupId, resetTimeout);
    }
  }, TOOL_REEL_ROLL_MS);
  pushToolReelTimeout(groupId, settleTimeout);
};

const finishToolReel = (groupId: string, actions: Action[]) => {
  const state = toolReelStates[groupId];
  if (!state || state.completing) return;

  const items =
    state.items.length > 1
      ? state.items
      : getLatestToolSegment(actions)
          .map(getToolSummaryText)
          .filter((item) => item.trim().length > 0);

  if (items.length < 2) {
    clearToolReelTimers(groupId);
    delete toolReelStates[groupId];
    return;
  }

  clearToolReelTimers(groupId);
  state.items = items;
  state.signature = items.join('\u0001');
  state.completing = true;

  const finalIndex = items.length - 1;
  const normalizedOffset = -state.index * TOOL_REEL_ITEM_HEIGHT;
  const targetOffset = -finalIndex * TOOL_REEL_ITEM_HEIGHT;

  state.phase = 'idle';
  state.offsetPx = normalizedOffset;

  window.requestAnimationFrame(() => {
    state.phase = 'rolling';
    state.offsetPx = targetOffset - TOOL_REEL_OVERSHOOT_PX;

    const settleTimeout = window.setTimeout(() => {
      state.phase = 'settle';
      state.offsetPx = targetOffset;
      state.index = finalIndex;
    }, TOOL_REEL_ROLL_MS);
    pushToolReelTimeout(groupId, settleTimeout);

    const cleanupTimeout = window.setTimeout(
      () => {
        clearToolReelTimers(groupId);
        delete toolReelStates[groupId];
      },
      TOOL_REEL_ROLL_MS + TOOL_REEL_SETTLE_MS + 260
    );
    pushToolReelTimeout(groupId, cleanupTimeout);
  });
};

const syncToolReels = () => {
  const activeGroups = new Set<string>();

  blockGroups.value.forEach((group) => {
    if (
      group.type !== 'summary' ||
      !group.actions ||
      !shouldShowToolReel(group.actions, group.id)
    ) {
      return;
    }

    const items = getToolReelItems(group.actions);
    const signature = items.join('\u0001');
    activeGroups.add(group.id);

    if (!toolReelStates[group.id] || toolReelStates[group.id].signature !== signature) {
      clearToolReelTimers(group.id);
      toolReelStates[group.id] = {
        index: 0,
        offsetPx: 0,
        phase: 'idle',
        signature,
        items
      };
    } else {
      toolReelStates[group.id].items = items;
    }

    if (!toolReelIntervals.has(group.id)) {
      const interval = window.setInterval(
        () => spinToolReel(group.id, items.length),
        TOOL_REEL_INTERVAL_MS
      );
      toolReelIntervals.set(group.id, interval);
    }
  });

  Object.keys(toolReelStates).forEach((groupId) => {
    if (!activeGroups.has(groupId)) {
      const group = blockGroups.value.find((item) => item.id === groupId);
      if (group?.type === 'summary' && group.actions && !toolReelStates[groupId].completing) {
        activeGroups.add(groupId);
        finishToolReel(groupId, group.actions);
        return;
      }

      if (!toolReelStates[groupId].completing) {
        clearToolReelTimers(groupId);
        delete toolReelStates[groupId];
      }
    }
  });
};

// 获取摘要组的加载动画组件（每次action类型切换时随机一次）
const getSummaryLoader = (groupId: string) => {
  const group = blockGroups.value.find((g) => g.id === groupId);
  if (!group || !group.actions) return getRandomLoader();

  // 找到当前正在streaming的action，或最后一个action
  const streamingAction = group.actions.find((a) => a.streaming);
  const currentAction = streamingAction || group.actions[group.actions.length - 1];

  if (!currentAction) return getRandomLoader();

  // 找到当前action之前最近的一个不同类型的action
  // 用于判断是否发生了类型切换（thinking <-> tool）
  const currentIndex = group.actions.indexOf(currentAction);
  let lastDifferentTypeIndex = -1;

  for (let i = currentIndex - 1; i >= 0; i--) {
    if (group.actions[i].type !== currentAction.type) {
      lastDifferentTypeIndex = i;
      break;
    }
  }

  // 使用"类型切换点"作为key
  // 如果是第一个action，或者类型发生了切换，就会生成新的key
  // 如果是相同类型的连续action（如多个工具并行），使用相同的key
  const typeSegmentKey =
    lastDifferentTypeIndex >= 0
      ? `${group.actions[lastDifferentTypeIndex].id}-${currentAction.type}`
      : `start-${currentAction.type}`;

  const cacheKey = `${groupId}-${typeSegmentKey}`;

  if (!summaryLoaders.has(cacheKey)) {
    summaryLoaders.set(cacheKey, getRandomLoader());
  }
  return summaryLoaders.get(cacheKey);
};

// 将 actions 分组：连续的 thinking/tool 为一组，text 单独为一组
const blockGroups = computed(() => {
  const groups: BlockGroup[] = [];
  let currentGroup: Action[] = [];
  let groupIndex = 0;

  props.actions.forEach((action, idx) => {
    if (action.type === 'thinking' || action.type === 'tool') {
      currentGroup.push(action);
    } else if (action.type === 'text' || action.type === 'system') {
      // 先保存当前的 thinking/tool 组
      if (currentGroup.length > 0) {
        // 使用第一个 action 的 id 作为 group id，保证稳定性
        const firstActionId =
          currentGroup[0].id || currentGroup[0].blockId || `summary-${groupIndex}`;
        groups.push({
          type: 'summary',
          id: `summary-${firstActionId}`,
          actions: currentGroup
        });
        groupIndex++;
        currentGroup = [];
      }
      if (action.type === 'text') {
        const text = typeof action.content === 'string' ? action.content : '';
        if (!text.trim()) {
          return;
        }
        // 添加 text 组
        groups.push({
          type: 'text',
          id: `text-${idx}`,
          content: text,
          streaming: action.streaming
        });
      } else {
        // 添加 system 组（用于子智能体完成通知，保持顺序）
        groups.push({
          type: 'system',
          id: `system-${idx}`,
          content: action.content
        });
      }
    }
  });

  // 处理最后剩余的 thinking/tool 组
  if (currentGroup.length > 0) {
    // 使用第一个 action 的 id 作为 group id，保证稳定性
    const firstActionId = currentGroup[0].id || currentGroup[0].blockId || `summary-${groupIndex}`;
    groups.push({
      type: 'summary',
      id: `summary-${firstActionId}`,
      actions: currentGroup
    });
  }

  return groups;
});

type ToolCategory =
  | 'read'
  | 'command'
  | 'edit'
  | 'search'
  | 'webpage'
  | 'mcp'
  | 'workflow_activate'
  | 'workflow_advance'
  | 'workflow'
  | 'todo_create'
  | 'todo_update'
  | 'memory_update'
  | 'memory_read'
  | 'conversation'
  | 'sub_agent'
  | 'sub_agent_manage'
  | 'wait'
  | 'ask'
  | 'plan'
  | 'skill'
  | 'settings'
  | 'easter_egg'
  | 'other';

const TOOL_CATEGORY_MAP: Record<string, ToolCategory> = {
  // 读取文件 / 视觉内容
  read_file: 'read',
  read_skill: 'read',
  vlm_analyze: 'read',
  view_image: 'read',
  view_video: 'read',

  // 运行指令
  run_command: 'command',
  terminal_session: 'command',
  terminal_input: 'command',
  terminal_snapshot: 'command',

  // 编辑文件
  create_file: 'edit',
  write_file: 'edit',
  edit_file: 'edit',
  delete_file: 'edit',
  rename_file: 'edit',
  create_folder: 'edit',

  // 搜索
  web_search: 'search',

  // 查看网页
  extract_webpage: 'webpage',
  save_webpage: 'webpage',

  // MCP（list_mcp_servers 归入此类；mcp__ 前缀由 getToolCategory 动态判定）
  list_mcp_servers: 'mcp',

  // 工作流：激活 / 推进 / 其余操作
  activate_workflow: 'workflow_activate',
  report_workflow_stage: 'workflow_advance',
  choose_workflow_branch: 'workflow_advance',
  get_workflow_status: 'workflow',
  deactivate_workflow: 'workflow',
  list_workflows: 'workflow',
  save_workflow: 'workflow',

  // 待办：创建 / 更新
  todo_create: 'todo_create',
  todo_update_task: 'todo_update',

  // 更新记忆
  update_memory: 'memory_update',
  update_project_memory: 'memory_update',

  // 查看记忆
  recall_project_memory: 'memory_read',
  search_project_memory: 'memory_read',

  // 回顾对话
  conversation_search: 'conversation',
  conversation_review: 'conversation',

  // 子智能体：创建 / 管理
  create_sub_agent: 'sub_agent',
  close_sub_agent: 'sub_agent_manage',
  terminate_sub_agent: 'sub_agent_manage',
  get_sub_agent_status: 'sub_agent_manage',

  // 其他原生工具
  sleep: 'wait',
  ask_user: 'ask',
  submit_plan: 'plan',
  create_skill: 'skill',
  manage_personalization: 'settings',
  trigger_easter_egg: 'easter_egg'
};

const CATEGORY_LABELS: Record<ToolCategory, (count: number) => string> = {
  read: (n) => `读取了 ${n} 个文件`,
  command: (n) => `运行了 ${n} 个指令`,
  edit: (n) => `编辑了 ${n} 次文件`,
  search: (n) => `搜索了 ${n} 次`,
  webpage: (n) => `查看了 ${n} 次网页`,
  mcp: (n) => `执行了 ${n} 次 MCP 工具`,
  workflow_activate: (n) => `激活了 ${n} 个工作流`,
  workflow_advance: (n) => `推进了 ${n} 次工作流进度`,
  workflow: (n) => `执行了 ${n} 次工作流操作`,
  todo_create: (n) => `创建了 ${n} 次待办`,
  todo_update: (n) => `更新了 ${n} 次待办`,
  memory_update: (n) => `更新了 ${n} 次记忆`,
  memory_read: (n) => `查看了 ${n} 次记忆`,
  conversation: (n) => `回顾了 ${n} 次对话`,
  sub_agent: (n) => `创建了 ${n} 个子智能体`,
  sub_agent_manage: (n) => `管理了 ${n} 次子智能体`,
  wait: (n) => `等待了 ${n} 次`,
  ask: (n) => `询问了 ${n} 次`,
  plan: (n) => `提交了 ${n} 次计划`,
  skill: (n) => `归档了 ${n} 次技能`,
  settings: (n) => `更新了 ${n} 次个性化设置`,
  easter_egg: (n) => `触发了 ${n} 次彩蛋`,
  other: (n) => `执行了 ${n} 次其他操作`
};

const CATEGORY_ORDER: ToolCategory[] = [
  'read',
  'command',
  'edit',
  'search',
  'webpage',
  'mcp',
  'workflow_activate',
  'workflow_advance',
  'workflow',
  'todo_create',
  'todo_update',
  'memory_update',
  'memory_read',
  'conversation',
  'sub_agent',
  'sub_agent_manage',
  'wait',
  'ask',
  'plan',
  'skill',
  'settings',
  'easter_egg',
  'other'
];

const getToolCategory = (action: Action): ToolCategory => {
  const name = action.tool?.name;
  if (typeof name !== 'string') return 'other';
  // MCP 工具名动态生成（mcp__<服务>__<工具>），无法穷举，按前缀归类
  if (name.startsWith('mcp__')) return 'mcp';
  return TOOL_CATEGORY_MAP[name] || 'other';
};

// 运行中：显示当前最新步骤的意图或状态（单行，由 CSS 截断）
const getRunningSummaryText = (actions: Action[]): string => {
  const streamingActions = actions.filter((a) => a.streaming);

  let currentStep;
  if (streamingActions.length > 0) {
    currentStep = streamingActions[streamingActions.length - 1];
  } else {
    const toolActions = actions.filter((a) => a.type === 'tool');
    if (toolActions.length > 0) {
      currentStep = toolActions[toolActions.length - 1];
    } else {
      currentStep = actions[actions.length - 1];
    }
  }

  if (!currentStep) return '';

  if (currentStep.type === 'thinking') {
    const content = currentStep.content || '';
    return getFirstLine(content);
  }

  if (currentStep.type === 'tool') {
    const tool = currentStep.tool;
    if (!tool) return '';

    const intentEnabled = personalizationStore.form.tool_intent_enabled;
    const intentText = tool.intent_rendered || tool.intent_full || '';

    if (intentEnabled && intentText) {
      return getFirstLine(intentText);
    }

    if (tool.status === 'preparing') {
      return `准备调用 ${tool.name || '工具'}...`;
    }
    if (tool.status === 'running') {
      return `正在调用 ${tool.name || '工具'}...`;
    }
    if (tool.status === 'completed') {
      return tool.display_name || tool.name || '工具执行完成';
    }
    return currentStep.streaming ? '正在执行工具...' : tool.display_name || tool.name || '执行工具';
  }

  return '';
};

// 完成后：汇总本组所有工具的执行次数
const getCompletedSummaryText = (actions: Action[]): string => {
  const toolActions = actions.filter((a) => a.type === 'tool');

  if (toolActions.length === 0) {
    // 没有工具时回退到最后一条思考的单行内容
    const lastThinking = actions.filter((a) => a.type === 'thinking').pop();
    if (lastThinking) {
      return getFirstLine(lastThinking.content || '');
    }
    return '';
  }

  // 只有一个工具时，直接显示该工具的 intent/名称
  if (toolActions.length === 1) {
    return getToolSummaryText(toolActions[0]);
  }

  // counts 必须由 CATEGORY_ORDER 生成：手写初始化清单曾在新增分类时漏同步，
  // 导致新分类计数从 undefined 累加成 NaN、被 count > 0 过滤掉，摘要行整行空白
  const counts = Object.fromEntries(
    CATEGORY_ORDER.map((category) => [category, 0])
  ) as Record<ToolCategory, number>;

  toolActions.forEach((action) => {
    counts[getToolCategory(action)]++;
  });

  const parts: string[] = [];
  CATEGORY_ORDER.forEach((category) => {
    const count = counts[category];
    if (count > 0) {
      parts.push(CATEGORY_LABELS[category](count));
    }
  });

  return parts.join('，');
};

// 摘要行统一入口：运行中显示实时进度，完成后显示统计总结
const getSummaryLineText = (actions: Action[], groupId: string): string => {
  if (isSummaryRunning(actions, groupId)) {
    return getRunningSummaryText(actions);
  }
  return getCompletedSummaryText(actions);
};

// 判断摘要组是否正在运行（显示加载动画还是对勾）
const isSummaryRunning = (actions: Action[], groupId: string) => {
  if (!props.conversationRunning || !props.isLatestMessage) {
    return false;
  }

  // 找到当前 group 在 blockGroups 中的索引
  const currentIndex = blockGroups.value.findIndex((g) => g.id === groupId);
  if (currentIndex === -1) return false;

  // 检查后面是否有文本输出
  const hasTextAfter = blockGroups.value.slice(currentIndex + 1).some((g) => g.type === 'text');

  // 只有当后面有文本输出时，才显示对勾（说明这个摘要组已经完成并开始输出了）
  // 否则一直显示加载动画（即使工具执行完了，也要等下一个步骤或文本输出）
  return !hasTextAfter;
};

// 获取摘要组的所有步骤（用于展开显示）
const getSummarySteps = (actions: Action[]) => {
  return actions.map((action, idx) => ({
    id: action.id || action.blockId || `step-${idx}`,
    type: action.type as 'thinking' | 'tool',
    content: action.content || '',
    streaming: action.streaming || false,
    action
  }));
};

const isStepsWrapperNearBottom = (wrapper: HTMLElement) => {
  const distance = wrapper.scrollHeight - wrapper.scrollTop - wrapper.clientHeight;
  return distance <= STEPS_WRAPPER_BOTTOM_THRESHOLD;
};

const handleStepsWrapperScroll = (groupId: string, event: Event) => {
  const wrapper = event.target as HTMLElement;
  if (!wrapper) return;
  const escaped = !isStepsWrapperNearBottom(wrapper);
  stepsWrapperScrollLocks.set(groupId, escaped);
};

const registerStepsWrapper = (groupId: string, el: Element | null) => {
  const prev = stepsWrapperRefs.get(groupId);
  if (prev) {
    prev.removeEventListener('scroll', handleStepsWrapperScroll as EventListener);
  }
  if (el instanceof HTMLElement) {
    stepsWrapperRefs.set(groupId, el);
    el.addEventListener('scroll', (event) => handleStepsWrapperScroll(groupId, event));
  } else {
    stepsWrapperRefs.delete(groupId);
    stepsWrapperScrollLocks.delete(groupId);
  }
};

const scrollStepsWrapperToBottom = (groupId: string) => {
  const wrapper = stepsWrapperRefs.get(groupId);
  if (wrapper && !stepsWrapperScrollLocks.get(groupId)) {
    wrapper.scrollTop = wrapper.scrollHeight;
  }
};

const toggleExpand = (groupId: string) => {
  if (expandedGroups.value.has(groupId)) {
    expandedGroups.value.delete(groupId);
    emit('group-toggle', { groupId, expanded: false });

    // 折叠后，取消注册该组中的思考内容 ref
    nextTick(() => {
      const group = blockGroups.value.find((g) => g.id === groupId);
      if (group && group.actions) {
        group.actions.forEach((action) => {
          if (action.type === 'thinking') {
            const blockId = action.id || action.blockId;
            if (blockId && typeof props.registerThinkingRef === 'function') {
              props.registerThinkingRef(blockId, null);
            }
          }
        });
      }
    });
  } else {
    expandedGroups.value.add(groupId);
    emit('group-toggle', { groupId, expanded: true });

    // 展开后，注册该组中的思考内容 ref
    nextTick(() => {
      const group = blockGroups.value.find((g) => g.id === groupId);
      if (group && group.actions) {
        // 展开时重置锁定：用户新展开的内容默认跟随到底部
        stepsWrapperScrollLocks.set(groupId, false);
        // 运行中展开时，将展开区滚动到底部
        if (isSummaryRunning(group.actions, groupId)) {
          scrollStepsWrapperToBottom(groupId);
        }
        group.actions.forEach((action) => {
          if (action.type === 'thinking') {
            const blockId = action.id || action.blockId;
            if (blockId) {
              const el = thinkingRefs.get(blockId);
              if (el && typeof props.registerThinkingRef === 'function') {
                props.registerThinkingRef(blockId, el);
                // 如果正在 streaming，滚动到底部
                if (action.streaming) {
                  el.scrollTop = el.scrollHeight;
                }
              }
            }
          }
        });
      }
    });
  }
};

const getStepIconStyle = (step: Step) => {
  if (step.type === 'thinking') {
    return props.iconStyle ? props.iconStyle('brain') : {};
  } else if (step.type === 'tool' && step.action.tool) {
    const iconKey = props.getToolIcon ? props.getToolIcon(step.action.tool) : 'terminal';
    return props.iconStyle ? props.iconStyle(iconKey) : {};
  }
  return props.iconStyle ? props.iconStyle('terminal') : {};
};

const getToolName = (action: Action) => {
  const tool = action.tool;
  if (!tool) return '执行工具';

  const intentEnabled = personalizationStore.form.tool_intent_enabled;
  const intentText = tool.intent_rendered || tool.intent_full || '';

  if (intentEnabled && intentText) {
    // 开启intent模式且有intent时，只显示intent
    return intentText;
  }

  // 没有intent或未开启intent模式时显示状态
  if (tool.status === 'preparing') {
    return `准备调用 ${tool.name || '工具'}...`;
  }
  if (tool.status === 'running') {
    return `正在调用 ${tool.name || '工具'}...`;
  }
  if (tool.status === 'completed') {
    return '工具执行完成';
  }
  return tool.name || '执行工具';
};

const getToolIntent = (action: Action) => {
  return action.tool?.intent || '';
};

const renderToolResult = (action: Action) => {
  const rendered = renderEnhancedToolResult(
    action,
    props.formatSearchTopic || (() => ''),
    props.formatSearchTime || (() => ''),
    props.formatSearchDomains || (() => '')
  );
  if (rendered) return rendered;

  const result = action.tool?.result;
  if (!result) return '<div class="result-item">执行中...</div>';

  if (typeof result === 'string') {
    return `<div class="result-item"><pre>${escapeHtml(result)}</pre></div>`;
  }

  return `<div class="result-item"><pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre></div>`;
};

const renderContent = (content: string) => {
  if (!content) return '';
  return escapeHtml(content).replace(/\n/g, '<br>');
};

const escapeHtml = (text: string) => {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
};

const registerThinking = (key: string, el: Element | null) => {
  // 本地保存一份，用于展开/折叠时管理
  if (el instanceof HTMLElement) {
    thinkingRefs.set(key, el);
  } else {
    thinkingRefs.delete(key);
  }

  // 检查当前思考内容所在的摘要是否展开
  const group = blockGroups.value.find(
    (g) =>
      g.type === 'summary' && g.actions && g.actions.some((a) => a.id === key || a.blockId === key)
  );

  // 只有展开的摘要中的思考内容才注册到父组件
  if (group && expandedGroups.value.has(group.id)) {
    if (typeof props.registerThinkingRef === 'function') {
      props.registerThinkingRef(key, el);
    }
  }
};

const handleScroll = (blockId: string, event: Event) => {
  if (typeof props.handleThinkingScroll === 'function') {
    props.handleThinkingScroll(blockId, event);
  }
};

watch(
  () => props.actions,
  () => {
    syncToolReels();
    // 对展开的 running 摘要组，若用户未主动向上滚动，自动将展开区滚动到底部
    nextTick(() => {
      blockGroups.value.forEach((group) => {
        if (
          group.type === 'summary' &&
          group.actions &&
          expandedGroups.value.has(group.id) &&
          isSummaryRunning(group.actions, group.id)
        ) {
          scrollStepsWrapperToBottom(group.id);
        }
      });
    });
  },
  { deep: true, immediate: true }
);

watch(
  () => [props.conversationRunning, props.isLatestMessage],
  () => {
    syncToolReels();
  }
);

onBeforeUnmount(() => {
  Object.keys(toolReelStates).forEach(clearToolReelTimers);
  stepsWrapperRefs.forEach((el) => {
    el.removeEventListener('scroll', handleStepsWrapperScroll as EventListener);
  });
  stepsWrapperRefs.clear();
  stepsWrapperScrollLocks.clear();
});
</script>

<style scoped>
.minimal-blocks-container {
  --chat-content-x: 0px;
  margin: 16px 0;
  width: 100%;
}

/* 摘要组 */
.summary-group {
  margin: 16px 0;
  width: 100%;
}

.sub-agent-system-group {
  margin: 0;
}

.sub-agent-system-group .sub-agent-system-summary-line {
  cursor: default;
}

.sub-agent-system-group .sub-agent-system-summary-line:hover {
  background-color: transparent;
}

/* 摘要行（只有文字） */
.summary-line-text {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  column-gap: 8px;
  width: 100%;
  padding: 0;
  cursor: pointer;
  transition: background-color 0.2s;
}

.summary-line-text:hover {
  background-color: var(--surface-panel);
}

.summary-content-wrapper {
  min-width: 0;
  font-size: 15px;
  color: var(--text-secondary);
  line-height: 1.7;
  padding: 0;
  position: relative;
  z-index: 0;
}

.summary-preview {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  color: inherit;
  mask-image: linear-gradient(to right, black 85%, transparent 100%);
  -webkit-mask-image: linear-gradient(to right, black 85%, transparent 100%);
}

.summary-tool-reel-window {
  position: relative;
  display: inline-block;
  width: min(100%, 36em);
  height: 26px;
  overflow: hidden;
  vertical-align: top;
}

.summary-tool-reel-window::before,
.summary-tool-reel-window::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  z-index: 1;
  height: 6px;
  pointer-events: none;
}

.summary-tool-reel-window::before {
  top: 0;
  background: linear-gradient(180deg, var(--surface-base), transparent);
}

.summary-tool-reel-window::after {
  bottom: 0;
  background: linear-gradient(0deg, var(--surface-base), transparent);
}

.summary-tool-reel-track {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: block;
  will-change: transform;
}

.summary-tool-reel-track.rolling {
  transition: transform 520ms cubic-bezier(0.22, 0.9, 0.25, 1);
}

.summary-tool-reel-track.settle {
  transition: transform 170ms cubic-bezier(0.2, 0.72, 0.26, 1);
}

.summary-tool-reel-item {
  display: block;
  height: 26px;
  line-height: 26px;
  overflow: hidden;
  color: var(--text-secondary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-status-icon {
  flex-shrink: 0;
  margin-left: 0;
  align-self: start;
  margin-top: calc((1.7em - 18px) / 2);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  /* 严格限制右侧不溢出对话记录边界；上下左允许动画自然延伸，不裁剪 */
  clip-path: inset(-100% 0 -100% -100%);
}

.summary-icon-inner {
  display: flex;
  align-items: center;
  justify-content: center;
  /* 整体向左偏移 10px，不影响容器尺寸和布局 */
  transform: translateX(-10px);
}

.check-icon {
  color: var(--text-tertiary);
  width: 18px;
  height: 18px;
}

/* 运行中文本：按字符依次闪烁 */
.summary-line-text.running .summary-char {
  display: inline-block;
  color: var(--text-secondary);
  animation: summaryPass 2s ease-in-out infinite;
}

@keyframes summaryPass {
  0%,
  100% {
    color: var(--text-secondary);
  }
  50% {
    color: var(--accent);
  }
}

/* 步骤容器 */
.steps-container {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  margin-top: 8px;
  width: 100%;
  padding: 0;
}

/* 桌面端与正式文本输出左右内边距保持一致 */
@media (min-width: 769px) {
  .summary-line-text,
  .steps-container {
    padding: 0 var(--chat-content-x);
  }
}

.steps-wrapper {
  overflow: hidden;
  min-height: 0;
  /* content-visibility 进过渡列表：收起动画（grid 0.3s）播完才跳过子树渲染，
     展开时立即恢复渲染；不支持的引擎退化为收起时立即跳过 */
  transition: content-visibility 0.3s;
  transition-behavior: allow-discrete;
}

/* 折叠态跳过整个子树渲染：0fr 折叠只裁视觉，DOM 仍全量参与布局，
   几千节点的步骤组会在窗口拖拽时逐帧重排导致卡顿。
   隐藏翻转由上方过渡延迟到收起动画结束后生效 */
.steps-container:not(.show) .steps-wrapper {
  content-visibility: hidden;
  contain-intrinsic-size: 0;
}

.steps-wrapper.height-limited {
  max-height: min(450px, 70vh);
  overflow-y: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.steps-wrapper.height-limited::-webkit-scrollbar {
  display: none;
}

.steps-container.show {
  grid-template-rows: 1fr;
}

/* 步骤项 */
.step-item {
  display: flex;
  gap: 12px;
}

.step-item:not(:last-child) {
  margin-bottom: 16px;
}

.step-timeline {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  padding-top: 2px;
  align-self: stretch; /* 填充整个 step-item 的高度 */
}

.step-icon {
  flex-shrink: 0;
  color: var(--text-tertiary); /* 和竖线、字体一样的灰色 */
}

.step-line {
  width: 2px;
  flex: 1; /* 自动填充剩余空间 */
  background: var(--border-default);
  margin-top: 4px;
  margin-bottom: -14px; /* 延伸到下一个 step-item，保持上下距离 SVG 相等 */
  flex-shrink: 0;
}

/* 最后一个步骤隐藏竖线 */
.step-item:last-child .step-line {
  display: none;
}

.step-content {
  flex: 1;
  min-width: 0;
  padding-top: 2px;
}

.step-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 8px;
}

.step-body {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.7;
}

/* 思考内容 */
.thinking-content {
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-wrap: break-word;
  max-height: calc(1.7em * 6); /* 6行 */
  overflow-y: auto;
  margin: 0;
  padding: 0;
  display: block;
  /* 让第一行文字中心和 SVG 中心对齐：向上移动半个行高减去半个字高 */
  margin-top: calc((1em - 1.7em) / 2);
  /* 滚动条样式（参考 git 状态侧边栏文件列表，滚动槽透明贴合灰色底色） */
  scrollbar-width: thin; /* Firefox */
  scrollbar-color: color-mix(in srgb, var(--text-secondary) 55%, transparent) transparent;
}

.thinking-content::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

.thinking-content::-webkit-scrollbar-track {
  background: transparent;
  border-radius: 999px;
  margin: 8px;
}

.thinking-content::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-secondary) 55%, transparent);
  border-radius: 999px;
  border: 2px solid transparent;
  background-clip: padding-box;
}

/* 工具内容 */
.step-header {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 0 0 8px 0;
  padding: 0;
  line-height: 1;
}
.tool-intent {
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 12px;
  font-size: 14px;
  line-height: 1.7;
  /* 让第一行文字紧贴顶部 */
  margin-top: calc((1.7em - 1em) / -2);
}

.tool-result {
  background: var(--surface-panel);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 12px;
  font-size: 13px;
  max-height: 200px; /* 约为原来卡片的三分之一 */
  overflow-y: auto;
  /* 隐藏滚动条 */
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE and Edge */
}

.tool-result::-webkit-scrollbar {
  display: none; /* Chrome, Safari, Opera */
}

.tool-result * {
  /* 隐藏所有子元素的滚动条 */
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.tool-result *::-webkit-scrollbar {
  display: none;
}

.tool-result pre {
  margin: 0;
  white-space: pre-wrap;
  word-wrap: break-word;
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  color: var(--text-secondary);
}

.result-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--border-default);
}

.result-item:last-child {
  border-bottom: none;
}

/* 文本输出 */
.text-output {
  margin: 16px 0;
  padding: 0;
}

@media (max-width: 768px) {
  .text-output {
    margin: 16px 0;
  }
}

.text-content {
  font-size: 15px;
  line-height: 1.7;
  color: var(--text-primary);
  word-wrap: break-word;
}
</style>
