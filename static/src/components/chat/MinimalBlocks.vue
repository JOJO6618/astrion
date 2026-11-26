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
          role="button"
          tabindex="0"
          :aria-expanded="expandedGroups.has(group.id)"
          @click="toggleExpand(group.id)"
          @keydown.enter.prevent="toggleExpand(group.id)"
          @keydown.space.prevent="toggleExpand(group.id)"
        >
          <div class="summary-content-wrapper">
            <span class="summary-preview">
              <SummarySweepText
                v-bind="getSummarySweepInput(group.actions, group.id)"
                :reel-view="
                  summaryToolReel.shouldShow(group.id)
                    ? summaryToolReel.getView(group.id)
                    : undefined
                "
                @settled="onSummarySettled(group.id, $event)"
              />
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
        <div
          class="steps-container"
          :class="{ show: expandedGroups.has(group.id) }"
          :inert="!expandedGroups.has(group.id)"
        >
          <div
            v-if="expandedGroups.has(group.id) || collapsingGroups.has(group.id)"
            class="steps-wrapper"
            :class="{ 'height-limited': heightLimited }"
            :ref="(el) => registerStepsWrapper(group.id, el)"
          >
            <MinimalStepWindow
              :actions="group.actions"
              :limited="heightLimited"
              :expanded="expandedGroups.has(group.id)"
              v-slot="{ action, index }"
            >
              <div class="step-item">
                <div class="step-timeline">
                  <span
                    class="step-icon icon icon-sm"
                    :style="getStepIconStyle(action)"
                    aria-hidden="true"
                  ></span>
                  <div class="step-line"></div>
                </div>
                <div class="step-content">
                  <div v-if="action.type === 'thinking'" class="step-body">
                    <div
                      class="thinking-content"
                      :ref="
                        (el) =>
                          registerThinking(group.id, stepId(action, index), el, action.streaming)
                      "
                      @scroll="handleScroll(stepId(action, index), $event)"
                    >
                      <MarkdownRenderer
                        :content="action.content || ''"
                        :is-streaming="!!action.streaming"
                        thinking
                      />
                    </div>
                  </div>
                  <div v-else class="step-body">
                    <div class="step-header">{{ getToolName(action) }}</div>
                    <div v-if="action.tool?.intent" class="tool-intent">
                      {{ action.tool.intent }}
                    </div>
                    <div class="tool-result">
                      <MinimalToolResult
                        v-if="action.tool?.result"
                        :action="action"
                        :format-search-topic="formatSearchTopic"
                        :format-search-time="formatSearchTime"
                        :format-search-domains="formatSearchDomains"
                      />
                      <div v-else class="result-item">{{ $t('chat.executing') }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </MinimalStepWindow>
          </div>
        </div>
      </div>

      <!-- 文本组 -->
      <div
        v-else-if="group.type === 'text'"
        class="text-output"
        :class="{ 'streaming-text': group.streaming }"
      >
        <slot
          v-if="editingMessageId && editingMessageId === group.responseMessageId"
          name="text-editor"
          :action="group"
        />
        <div v-else class="text-content" :class="{ 'streaming-text': group.streaming }">
          <MarkdownRenderer
            :content="group.content || ''"
            :is-streaming="group.streaming"
            :citations="citations"
            :citations-final="citationsFinal"
            :enable-citations="enableCitations"
          />
        </div>
        <slot name="text-actions" :action="group" />
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
import { ref, computed, nextTick, onBeforeUnmount, Component } from 'vue';
import { t, currentLocale } from '@/locales';
import { usePersonalizationStore } from '@/stores/personalization';
import { useSummaryToolReel } from '@/composables/useSummaryToolReel';
import {
  createElementScrollFollow,
  type ElementScrollFollow
} from '@/composables/elementScrollFollow';
import SummarySweepText from './SummarySweepText.vue';
import type { SummarySweepInput } from './summarySweepController';
import MinimalToolResult from './MinimalToolResult.vue';
import MinimalStepWindow from './MinimalStepWindow.vue';
import { getLatestToolBatch as getLatestToolSegment } from './toolSummaryBatch';
import { getToolStateText } from '@/utils/chatDisplay';

const personalizationStore = usePersonalizationStore();
const heightLimited = computed(() => personalizationStore.form.minimal_expand_height_limited);
import { getRandomLoader } from './loaders/index';
import MarkdownRenderer from './MarkdownRenderer.vue';
import type { CitationAnnotation } from './citationChips';

interface Action {
  id?: string;
  type: 'thinking' | 'tool' | 'text' | 'system';
  content: string;
  streaming?: boolean;
  blockId?: string;
  toolBatchId?: string;
  responseMessageId?: string;
  thinkingSummary?: string;
  thinkingSummaryRevision?: number;
  tool?: {
    name?: string;
    intent?: string;
    intent_rendered?: string;
    intent_full?: string;
    intent_complete?: boolean;
    status?: string;
    display_name?: string;
    arguments?: any;
    result?: any;
  };
}

interface BlockGroup {
  type: 'summary' | 'text' | 'system';
  id: string;
  actions?: Action[];
  content?: string;
  streaming?: boolean;
  responseMessageId?: string;
}

const props = defineProps<{
  actions: Action[];
  editingMessageId?: string;
  citations?: CitationAnnotation[];
  citationsFinal?: boolean;
  enableCitations?: boolean;
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
  (event: 'group-collapse-finished', payload: { groupId: string }): void;
}>();

const expandedGroups = ref(new Set<string>());
// 每次收起独立标记，快速重新展开时旧动画不能卸载新详情。
const collapsingGroups = ref(new Map<string, symbol>());
const thinkingRefs = new Map<string, HTMLElement>();
const stepsWrapperRefs = new Map<string, HTMLElement>();
const stepsWrapperFollowers = new Map<string, ElementScrollFollow>();

const summaryLoaders = new Map<string, Component>();

const getFirstLine = (raw: string) => {
  const text = typeof raw === 'string' ? raw : '';
  const firstLineEnd = text.indexOf('\n');
  return firstLineEnd >= 0 ? text.substring(0, firstLineEnd) : text;
};

const getToolSummaryText = (action: Action) => {
  void currentLocale.value;
  const tool = action.tool;
  if (!tool) return '';

  const intentEnabled = personalizationStore.form.tool_intent_enabled;
  const intentText = tool.intent_full || tool.intent_rendered || tool.arguments?.intent || '';

  // 只对 intent 片段设门闸：完整 intent 到达前不展示半截解码文字，但下方的
  // 稳定状态兜底文案（准备/正在/完成/失败四态）是安全的，必须照常显示，避免摘要行
  // 在完整 intent 到达前整行空白。完整 intent 到达后由上方分支同步替换。
  if (intentEnabled && isSummaryIntentReady(action) && intentText) {
    const firstLine = getFirstLine(intentText);
    if (firstLine) return firstLine;
  }

  // 无 intent 或 intent 未就绪：按工具名出四态文案（与完整视图同一来源）
  const stateText = getToolStateText(tool);
  if (stateText) return stateText;

  return action.streaming
    ? t('chat.executingTool')
    : tool.display_name || tool.name || t('chat.runTool');
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

const getToolBatchKey = (actions: Action[]) => {
  const first = getLatestToolSegment(actions)[0];
  return first?.toolBatchId || first?.id || '';
};

const isSummaryIntentReady = (action: Action): boolean => {
  if (!personalizationStore.form.tool_intent_enabled) return true;
  const tool = action.tool;
  return (
    tool?.intent_complete === true ||
    (tool?.intent_complete === undefined &&
      tool?.status !== 'preparing' &&
      tool?.status !== 'hinted')
  );
};

const getSummaryToolItems = (actions: Action[]) => {
  const batchKey = getToolBatchKey(actions);
  return getLatestToolSegment(actions).map((action, index) => {
    const text = getToolSummaryText(action);
    return {
      identity: `${batchKey}:${action.id || action.blockId || index}`,
      text,
      // ready 表示“这一行是否有可稳定展示的文案”，而非“intent 是否完整”。
      // intent 片段本身仍在 getToolSummaryText 内被门闸拦住，未就绪时回落到
      // 状态兜底文案；因此有文案即可渲染，控制器与滚筒不再整行空白。
      ready: !!text
    };
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
          id: action.id || `text-${idx}`,
          content: text,
          streaming: action.streaming,
          responseMessageId: action.responseMessageId
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
  | 'skill_read'
  | 'vision'
  | 'command'
  | 'edit'
  | 'search'
  | 'webpage'
  | 'browser'
  | 'mcp'
  | 'workflow_activate'
  | 'workflow_advance'
  | 'workflow'
  | 'todo_create'
  | 'todo_update'
  | 'schedule'
  | 'memory_update'
  | 'memory_read'
  | 'conversation'
  | 'sub_agent'
  | 'sub_agent_manage'
  | 'worktree_enter'
  | 'worktree_exit'
  | 'wait'
  | 'ask'
  | 'plan'
  | 'skill'
  | 'settings'
  | 'easter_egg'
  | 'other';

const TOOL_CATEGORY_MAP: Record<string, ToolCategory> = {
  // 读取文件 / 技能 / 视觉内容（分档统计：看图与读技能不再计入“读文件”）
  read_file: 'read',
  read_skill: 'skill_read',
  search_skills: 'search',
  vlm_analyze: 'vision',
  view_image: 'vision',
  view_video: 'vision',

  // 运行指令
  run_command: 'command',
  terminal_session: 'command',
  terminal_input: 'command',
  terminal_snapshot: 'command',

  // 编辑文件
  write_file: 'edit',
  edit_file: 'edit',

  // 搜索
  web_search: 'search',

  // 查看网页
  extract_webpage: 'webpage',
  save_webpage: 'webpage',

  // 浏览器操作（browser_* 由 getToolCategory 前缀判定，不在此逐条登记）

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

  // 定时任务：三个工具共用极简分类
  submit_schedule: 'schedule',
  query_schedules: 'schedule',
  manage_schedule: 'schedule',

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
  terminate_sub_agent: 'sub_agent_manage',
  get_sub_agent_status: 'sub_agent_manage',
  send_message_to_sub_agent: 'sub_agent_manage',
  stop_sub_agent: 'sub_agent_manage',
  answer_sub_agent_question: 'sub_agent_manage',

  // Git 工作树：进入/创建 / 退出
  enter_worktree: 'worktree_enter',
  exit_worktree: 'worktree_exit',

  // 其他原生工具
  sleep: 'wait',
  ask_user: 'ask',
  submit_plan: 'plan',
  create_skill: 'skill',
  manage_personalization: 'settings',
  trigger_easter_egg: 'easter_egg'
};

// 分类摘要文案 key（模块顶层禁止调 t()：存 key 字符串，渲染时 t() 求值）
// TODO(common): 候选公共词（若跨域复用于摘要统计可归并 common）
const CATEGORY_LABEL_KEYS: Record<ToolCategory, string> = {
  read: 'chat.summaryRead',
  skill_read: 'chat.summarySkillRead',
  vision: 'chat.summaryVision',
  command: 'chat.summaryCommand',
  edit: 'chat.summaryEdit',
  search: 'chat.summarySearch',
  webpage: 'chat.summaryWebpage',
  browser: 'chat.summaryBrowser',
  mcp: 'chat.summaryMcp',
  workflow_activate: 'chat.summaryWorkflowActivate',
  workflow_advance: 'chat.summaryWorkflowAdvance',
  workflow: 'chat.summaryWorkflow',
  todo_create: 'chat.summaryTodoCreate',
  todo_update: 'chat.summaryTodoUpdate',
  schedule: 'toolResults.summarySchedule',
  memory_update: 'chat.summaryMemoryUpdate',
  memory_read: 'chat.summaryMemoryRead',
  conversation: 'chat.summaryConversation',
  sub_agent: 'chat.summarySubAgent',
  sub_agent_manage: 'chat.summarySubAgentManage',
  worktree_enter: 'chat.summaryWorktreeEnter',
  worktree_exit: 'chat.summaryWorktreeExit',
  wait: 'chat.summaryWait',
  ask: 'chat.summaryAsk',
  plan: 'chat.summaryPlan',
  skill: 'chat.summarySkill',
  settings: 'chat.summarySettings',
  easter_egg: 'chat.summaryEasterEgg',
  other: 'chat.summaryOther'
};

const CATEGORY_ORDER: ToolCategory[] = [
  'read',
  'skill_read',
  'vision',
  'command',
  'edit',
  'search',
  'webpage',
  'browser',
  'mcp',
  'workflow_activate',
  'workflow_advance',
  'workflow',
  'todo_create',
  'todo_update',
  'schedule',
  'memory_update',
  'memory_read',
  'conversation',
  'sub_agent',
  'sub_agent_manage',
  'worktree_enter',
  'worktree_exit',
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
  // 浏览器工具名同为动态增长（browser_*），按前缀统一归到浏览器类别
  if (name.startsWith('browser_')) return 'browser';
  return TOOL_CATEGORY_MAP[name] || 'other';
};

// 完成后：汇总本组所有工具的执行次数
const getCompletedSummaryText = (actions: Action[]): string => {
  void currentLocale.value;
  const toolActions = actions.filter((a) => a.type === 'tool');

  if (toolActions.length === 0) {
    // 没有工具时回退到最后一条思考的单行内容
    const lastThinking = actions.filter((a) => a.type === 'thinking').pop();
    if (lastThinking) {
      return lastThinking.thinkingSummary || getFirstLine(lastThinking.content || '');
    }
    return '';
  }

  // 只有一个工具时，直接显示该工具的 intent/名称
  if (toolActions.length === 1) {
    return getToolSummaryText(toolActions[0]);
  }

  // counts 必须由 CATEGORY_ORDER 生成：手写初始化清单曾在新增分类时漏同步，
  // 导致新分类计数从 undefined 累加成 NaN、被 count > 0 过滤掉，摘要行整行空白
  const counts = Object.fromEntries(CATEGORY_ORDER.map((category) => [category, 0])) as Record<
    ToolCategory,
    number
  >;

  toolActions.forEach((action) => {
    counts[getToolCategory(action)]++;
  });

  const parts: string[] = [];
  CATEGORY_ORDER.forEach((category) => {
    const count = counts[category];
    if (count > 0) {
      parts.push(t(CATEGORY_LABEL_KEYS[category], { n: count }));
    }
  });

  return parts.join('，');
};

// 判断摘要组是否正在运行（显示加载动画还是对勾）
const isSummaryRunning = (actions: Action[], groupId: string) => {
  if (!props.conversationRunning || !props.isLatestMessage) {
    return false;
  }

  // 找到当前 group 在 blockGroups 中的索引
  const currentIndex = blockGroups.value.findIndex((g) => g.id === groupId);
  if (currentIndex === -1) return false;

  // text_start creates an empty action before its first nonempty chunk.
  const lastActionIndex = props.actions.indexOf(actions[actions.length - 1]);
  const hasTextAfter =
    lastActionIndex >= 0 &&
    props.actions.slice(lastActionIndex + 1).some((action) => action.type === 'text');

  // 只有当后面有文本输出时，才显示对勾（说明这个摘要组已经完成并开始输出了）
  // 否则一直显示加载动画（即使工具执行完了，也要等下一个步骤或文本输出）
  return !hasTextAfter;
};

const summaryEntries = ref<Record<string, string>>({});
const summaryEntryTexts = ref<Record<string, string>>({});
const summaryThinkingEntries = ref<Record<string, string>>({});
const onSummarySettled = (groupId: string, input: SummarySweepInput) => {
  summaryEntries.value[groupId] = input.identity;
  summaryEntryTexts.value[groupId] = input.text;
  // Keep thinking entry completion separate: the final summary's settled
  // event must not make the completed thought look unentered again.
  if (input.kind === 'thinking' && input.text) {
    summaryThinkingEntries.value[groupId] = input.identity;
  }
};

const summaryToolReel = useSummaryToolReel(() =>
  blockGroups.value.flatMap((group) => {
    if (group.type !== 'summary' || !group.actions) return [];
    const tools = getLatestToolSegment(group.actions);
    const batchKey = getToolBatchKey(group.actions);
    return [
      {
        id: group.id,
        batchKey,
        items: getSummaryToolItems(group.actions)
          .filter((item) => item.ready)
          .map((item) => item.text),
        ready: summaryEntries.value[group.id] === `${batchKey}:tools`,
        running: isSummaryRunning(group.actions, group.id),
        active: tools.some(isActiveToolAction)
      }
    ];
  })
);

const thinkingSweepIdentity = (action: Action | undefined, groupId: string): string => {
  const base = action?.id || action?.blockId || `${groupId}:thinking`;
  return action?.thinkingSummary ? `${base}:summary:${action.thinkingSummaryRevision || 0}` : base;
};

const getSummarySweepInput = (actions: Action[], groupId: string): SummarySweepInput => {
  const running = isSummaryRunning(actions, groupId);
  const lastAction = actions[actions.length - 1];
  const thinkingIdentity = thinkingSweepIdentity(lastAction, groupId);
  const thinkingComplete =
    lastAction?.type === 'thinking' &&
    lastAction.streaming === false &&
    summaryThinkingEntries.value[groupId] === thinkingIdentity;
  const latestSummary = blockGroups.value.filter((group) => group.type === 'summary').pop();
  // Only keep a finished thought until its entry has appeared. Body streaming
  // must not postpone the final summary until the whole API call completes.
  const thinkingDuringOutput =
    lastAction?.type === 'thinking' &&
    !thinkingComplete &&
    latestSummary?.id === groupId &&
    props.conversationRunning &&
    props.isLatestMessage;
  if (thinkingComplete || (!running && !thinkingDuringOutput)) {
    const summaryUpdate =
      lastAction?.type === 'thinking' &&
      !!lastAction.thinkingSummary &&
      props.conversationRunning &&
      props.isLatestMessage;
    return {
      text: getCompletedSummaryText(actions),
      identity: summaryUpdate ? thinkingIdentity : `${groupId}:complete`,
      kind: summaryUpdate ? 'thinking' : 'static',
      animate: !!summaryUpdate,
      sweeping: false
    };
  }
  const tools = getLatestToolSegment(actions);
  const items = getSummaryToolItems(actions);
  if (items.length) {
    const running = tools.some(isActiveToolAction);
    const item = items[0];
    const identity = `${getToolBatchKey(actions)}:tools`;
    // 并行滚筒接管后续行。仅当首项仍是“状态占位文案”时才复用缓存的已入场
    // 首项文字，避免状态变化重播外层入场；一旦首项拿到完整 intent，就回落到
    // 实时文案，使“准备调用…”兜底能被真实 intent 替换，而不会被永久锁住。
    const firstTool = tools[0];
    const firstHasIntent =
      personalizationStore.form.tool_intent_enabled &&
      isSummaryIntentReady(firstTool) &&
      !!(
        firstTool?.tool?.intent_full ||
        firstTool?.tool?.intent_rendered ||
        firstTool?.tool?.arguments?.intent
      );
    const entryText =
      items.length > 1 && summaryEntries.value[groupId] === identity && !firstHasIntent
        ? summaryEntryTexts.value[groupId]
        : undefined;
    return {
      // The reel owns subsequent row updates. Status changes must not replay
      // this batch's first entry or rebuild the outer summary animation.
      text: entryText ?? item.text,
      identity,
      kind: 'tool',
      animate: true,
      sweeping: items.length === 1 && running,
      parallel: items.length > 1,
      // Same-batch parallel members keep the first entry intact; the reel
      // waits for its settled event even when later members arrive early.
      ready: item.ready
    };
  }
  const thinking = actions.filter((action) => action.type === 'thinking').pop();
  return {
    text: thinking?.thinkingSummary || getFirstLine(thinking?.content || ''),
    identity: thinkingSweepIdentity(thinking, groupId),
    kind: 'thinking',
    animate: true,
    sweeping: running && thinking?.streaming !== false
  };
};

const stepId = (action: Action, index: number) => action.id || action.blockId || `step-${index}`;

const registerStepsWrapper = (groupId: string, el: Element | null) => {
  if (stepsWrapperRefs.get(groupId) === el) return;
  stepsWrapperFollowers.get(groupId)?.dispose();
  stepsWrapperFollowers.delete(groupId);
  if (el instanceof HTMLElement) {
    stepsWrapperRefs.set(groupId, el);
    stepsWrapperFollowers.set(
      groupId,
      createElementScrollFollow(el, {
        threshold: 20,
        isCurrent: () => {
          const group = blockGroups.value.find((item) => item.id === groupId);
          return (
            stepsWrapperRefs.get(groupId) === el &&
            expandedGroups.value.has(groupId) &&
            !!group?.actions &&
            isSummaryRunning(group.actions, groupId)
          );
        }
      })
    );
  } else {
    stepsWrapperRefs.delete(groupId);
  }
};

const finishCollapse = async (groupId: string, token: symbol) => {
  await nextTick();
  if (collapsingGroups.value.get(groupId) !== token) return;
  const container = stepsWrapperRefs.get(groupId)?.parentElement;
  const transitions =
    container
      ?.getAnimations()
      .filter(
        (animation) =>
          'transitionProperty' in animation && animation.transitionProperty === 'grid-template-rows'
      ) || [];
  await Promise.allSettled(transitions.map((animation) => animation.finished));
  if (collapsingGroups.value.get(groupId) !== token) return;
  collapsingGroups.value.delete(groupId);
  emit('group-collapse-finished', { groupId });
};

const toggleExpand = (groupId: string) => {
  if (expandedGroups.value.has(groupId)) {
    expandedGroups.value.delete(groupId);
    const token = Symbol(groupId);
    collapsingGroups.value.set(groupId, token);
    void finishCollapse(groupId, token);
    emit('group-toggle', { groupId, expanded: false });
  } else {
    expandedGroups.value.add(groupId);
    collapsingGroups.value.delete(groupId);
    emit('group-toggle', { groupId, expanded: true });

    nextTick(() => {
      if (!expandedGroups.value.has(groupId)) return;
      const group = blockGroups.value.find((item) => item.id === groupId);
      if (group?.actions && isSummaryRunning(group.actions, groupId)) {
        stepsWrapperFollowers.get(groupId)?.reset();
      }
    });
  }
};

const getStepIconStyle = (action: Action) => {
  if (action.type === 'thinking') {
    return props.iconStyle ? props.iconStyle('brain') : {};
  } else if (action.type === 'tool' && action.tool) {
    const iconKey = props.getToolIcon ? props.getToolIcon(action.tool) : 'terminal';
    return props.iconStyle ? props.iconStyle(iconKey) : {};
  }
  return props.iconStyle ? props.iconStyle('terminal') : {};
};

const getToolName = (action: Action) => {
  void currentLocale.value;
  const tool = action.tool;
  if (!tool) return t('chat.runTool');

  const intentEnabled = personalizationStore.form.tool_intent_enabled;
  const intentText = tool.intent_rendered || tool.intent_full || '';

  if (intentEnabled && intentText) {
    // 开启intent模式且有intent时，只显示intent
    return intentText;
  }

  // 没有intent或未开启intent模式时，按工具名出四态文案
  return getToolStateText(tool) || tool.name || t('chat.runTool');
};

const registerThinking = (
  groupId: string,
  key: string,
  el: Element | null,
  streaming?: boolean
) => {
  if (el instanceof HTMLElement && expandedGroups.value.has(groupId)) {
    if (thinkingRefs.get(key) === el) return;
    thinkingRefs.set(key, el);
    props.registerThinkingRef?.(key, el);
    if (streaming) el.scrollTop = el.scrollHeight;
  } else if (thinkingRefs.delete(key)) {
    props.registerThinkingRef?.(key, null);
  }
};

const handleScroll = (blockId: string, event: Event) => {
  if (typeof props.handleThinkingScroll === 'function') {
    props.handleThinkingScroll(blockId, event);
  }
};

onBeforeUnmount(() => {
  collapsingGroups.value.clear();
  thinkingRefs.forEach((_, key) => props.registerThinkingRef?.(key, null));
  thinkingRefs.clear();
  stepsWrapperFollowers.forEach((follow) => follow.dispose());
  stepsWrapperFollowers.clear();
  stepsWrapperRefs.clear();
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
  /* 摘要、打印和文字切换统一保持 26px，避免虚拟列表高度随动画变化。 */
  line-height: 26px;
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

.summary-status-icon {
  flex-shrink: 0;
  margin-left: 0;
  align-self: start;
  /* (26px 行高 - 18px 图标) / 2，与 summary-content-wrapper 固定行高对齐 */
  margin-top: 4px;
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

/* 明亮主题的静态摘要维持三级文字色；运行扫光由独立组件与语义 token 控制。 */
body[data-theme='light'] .summary-content-wrapper {
  color: var(--text-tertiary);
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
}

@media (prefers-reduced-motion: reduce) {
  .steps-container {
    transition: none;
  }
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
  white-space: normal;
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
  /* 卡片是极简模式下唯一的滚动层：滚到底不带动外层步骤列表 */
  overscroll-behavior: contain;
  /* 隐藏滚动条 */
  scrollbar-width: none; /* Firefox */
  -ms-overflow-style: none; /* IE and Edge */
}

.tool-result::-webkit-scrollbar {
  display: none; /* Chrome, Safari, Opera */
}

/* 结果内容经 v-html 注入，scoped 选择器需 :deep 才能命中。
   卡片已限高滚动，内部的结果框/diff/长参数块不再各自限高滚动，避免双层滚动。
   （内部元素本身已无底色/描边框，见 ToolAction.vue 样式注释。） */
.tool-result :deep(.tool-result-content.scrollable),
.tool-result :deep(.tool-result-diff.scrollable),
.tool-result :deep(.tool-param-text),
.tool-result :deep(.tool-result-error),
.tool-result :deep(.tool-result-warning) {
  max-height: none;
  overflow: visible;
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
