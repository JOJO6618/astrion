import { defineStore } from 'pinia';
import { t, currentLocale, setLocale } from '@/locales';
import { useModelStore } from './model';

// 语气预设：模块顶层禁止调 t()，这里只存 key，由 getter tonePresets 在读取时 t(key) 求值（随语言切换刷新）
const TONE_PRESET_KEYS = [
  'stores.toneChatty',
  'stores.toneHumorous',
  'stores.toneBlunt',
  'stores.toneEncouraging',
  'stores.tonePoetic',
  'stores.toneCorporate',
  'stores.toneUnconventional',
  'stores.toneEmpathetic'
] as const;

export type BlockDisplayMode = 'traditional' | 'stacked' | 'minimal';
export type CompactMessageDisplay = 'full' | 'brief';
type RunMode = 'fast' | 'thinking';
export type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';
type PermissionMode = 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
export type WorkMode = 'plan' | 'ask' | 'execute';
type CommunicationStyle = 'default' | 'human_like' | 'auto';
type ConversationContinuity = 'low' | 'medium' | 'high';
type VersioningBackupMode = 'shallow' | 'full';

/** 与后端 DEFAULT_PERSONALIZATION_CONFIG['review_agents'] 保持一致 */
const defaultReviewAgents = (): Record<ReviewAgentKey, ReviewAgentSetting> => ({
  auto_approval: {
    model: '',
    thinking: false,
    timeout_seconds: 60,
    max_rounds: 3,
    max_command_timeout: 20
  },
  goal_review: {
    model: '',
    thinking: false,
    timeout_seconds: 60,
    max_rounds: 3,
    max_command_timeout: 60
  },
  workflow_review: {
    model: '',
    thinking: false,
    timeout_seconds: 120,
    max_rounds: 6,
    max_command_timeout: 60
  }
});

/** 清洗后端返回的 review_agents：缺键/类型错时回落默认值，数值钳到合理区间 */
const sanitizeReviewAgents = (raw: any): Record<ReviewAgentKey, ReviewAgentSetting> => {
  const defaults = defaultReviewAgents();
  const src = raw && typeof raw === 'object' ? raw : {};
  const clamp = (v: any, fallback: number, lo: number, hi: number): number => {
    const n = typeof v === 'number' ? v : parseInt(v, 10);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(lo, Math.min(Math.trunc(n), hi));
  };
  const keys: ReviewAgentKey[] = ['auto_approval', 'goal_review', 'workflow_review'];
  const out = {} as Record<ReviewAgentKey, ReviewAgentSetting>;
  for (const key of keys) {
    const d = defaults[key];
    const item = src[key] && typeof src[key] === 'object' ? src[key] : {};
    out[key] = {
      model: typeof item.model === 'string' ? item.model : '',
      thinking: typeof item.thinking === 'boolean' ? item.thinking : d.thinking,
      timeout_seconds: clamp(item.timeout_seconds, d.timeout_seconds, 5, 3600),
      max_rounds: clamp(item.max_rounds, d.max_rounds, 1, 50),
      max_command_timeout: clamp(item.max_command_timeout, d.max_command_timeout, 1, 600)
    };
  }
  return out;
};

/** 审核智能体键：自动审批 / 目标审核 / 工作流审核 */
export type ReviewAgentKey = 'auto_approval' | 'goal_review' | 'workflow_review';
export interface ReviewAgentSetting {
  /** 模型名（子智能体模型库条目）；留空 = 模型库 default_model */
  model: string;
  /** 思考模式开关（模型不支持思考时后端自动回落 fast 段） */
  thinking: boolean;
  /** 审核请求超时（秒） */
  timeout_seconds: number;
  /** 审核最大轮次 */
  max_rounds: number;
  /** 只读取证命令超时（秒） */
  max_command_timeout: number;
}

interface PersonalForm {
  enabled: boolean;
  communication_style: CommunicationStyle;
  conversation_continuity: ConversationContinuity;
  auto_generate_title: boolean;
  /** 标题生成模型（子智能体模型库条目名）；留空 = 模型库 default_model */
  title_model: string;
  recent_conversations_prompt_enabled: boolean;
  recent_conversations_prompt_limit: number | string;
  project_memory_inject_limit: number | string | null;
  tool_intent_enabled: boolean;
  skill_hints_enabled: boolean;
  skill_strict_terminal_enabled: boolean;
  skill_strict_sub_agent_enabled: boolean;
  skill_strict_run_command_foreground_enabled: boolean;
  skill_strict_run_command_background_enabled: boolean;
  silent_tool_disable: boolean;
  hide_tool_approval_panel: boolean;
  enhanced_tool_display: boolean;
  compact_message_display: CompactMessageDisplay;
  block_display_mode: BlockDisplayMode;
  show_status_avatar: boolean;
  show_git_status_bar: boolean;
  auto_open_terminal_panel: boolean;
  quick_dock_auto_expand: boolean;
  file_preview_auto_wrap: boolean;
  edit_summary_live_display: boolean;
  modify_history_enabled: boolean;
  stacked_hide_borders: boolean;
  minimal_expand_height_limited: boolean;
  enhanced_tool_display_categories: string[];
  enabled_skills: string[];
  self_identify: string;
  user_name: string;
  use_custom_names: boolean;
  profession: string;
  tone: string;
  considerations: string;
  disabled_tool_categories: string[];
  default_run_mode: RunMode | null;
  default_reasoning_effort: ReasoningEffort | null;
  default_permission_mode: PermissionMode;
  default_work_mode: WorkMode;
  versioning_enabled_by_default: boolean;
  versioning_backup_mode: VersioningBackupMode;
  versioning_restore_mode: 'overwrite';
  default_model: string | null;
  image_compression: string;
  auto_shallow_compress_enabled: boolean;
  auto_deep_compress_enabled: boolean;
  shallow_compress_trigger_tokens: number | null;
  shallow_compress_keep_recent_tools: number | null;
  shallow_compress_keep_user_turn_tools: number | null;
  shallow_compress_max_replace_per_round: number | null;
  shallow_compress_trigger_tool_calls_interval: number | null;
  deep_compress_trigger_tokens: number | null;
  deep_compress_form: 'file' | 'inject';
  agents_md_auto_inject: boolean;
  claude_md_auto_inject: boolean;
  agents_skills_scan_enabled: boolean;
  new_chat_button_behavior: 'route' | 'blank';
  group_sidebar_by_workspace: boolean;
  sidebar_pinned_workspaces: string[];
  sidebar_workspace_order: string[];
  theme: 'classic' | 'light' | 'dark';
  ui_locale: 'zh-CN' | 'en-US';
  goal_review_mode: 'readonly' | 'active';
  goal_end_conditions: string[];
  goal_max_turns: number;
  goal_max_tokens: number | null;
  review_agents: Record<ReviewAgentKey, ReviewAgentSetting>;
}

interface ExperimentState {
  blockDisplayMode: BlockDisplayMode;
  compactMessageDisplay: CompactMessageDisplay;
}

interface PersonalizationState {
  visible: boolean;
  loading: boolean;
  saving: boolean;
  loaded: boolean;
  status: string;
  error: string;
  toggleUpdating: boolean;
  overlayPressActive: boolean;
  form: PersonalForm;
  toolCategories: Array<{ id: string; label: string }>;
  skillsCatalog: Array<{ id: string; label: string; description?: string }>;
  recentConversationsPromptLimitRange: { min: number; max: number };
  projectMemoryInjectLimitMin: number;
  experiments: ExperimentState;
}

const DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT = 10;
const DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE = { min: 1, max: 30 };
const DEFAULT_PROJECT_MEMORY_INJECT_LIMIT = 20;
const PROJECT_MEMORY_INJECT_LIMIT_MIN = 5;
const DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOKENS = 80000;
const DEFAULT_DEEP_COMPRESS_TRIGGER_TOKENS = 150000;
const RUN_MODE_OPTIONS: RunMode[] = ['fast', 'thinking'];
const REASONING_EFFORT_OPTIONS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];
const EXPERIMENT_STORAGE_KEY = 'agents_personalization_experiments';
const THEME_STORAGE_KEY = 'agents_ui_theme';
const STACKED_HIDE_BORDERS_STORAGE_KEY = 'agents_stacked_hide_borders';
const MINIMAL_EXPAND_HEIGHT_LIMITED_STORAGE_KEY = 'agents_minimal_expand_height_limited';
const QUICK_DOCK_AUTO_EXPAND_STORAGE_KEY = 'agents_quick_dock_auto_expand';

const loadCachedTheme = (): PersonalForm['theme'] => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return 'classic';
  }
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  return saved === 'light' || saved === 'dark' || saved === 'classic' ? saved : 'classic';
};

const loadCachedStackedHideBorders = (): boolean => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return false;
  }
  return window.localStorage.getItem(STACKED_HIDE_BORDERS_STORAGE_KEY) === 'true';
};

const loadCachedBlockDisplayMode = (): BlockDisplayMode => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return 'stacked';
  }
  try {
    const raw = window.localStorage.getItem(EXPERIMENT_STORAGE_KEY);
    if (!raw) {
      return 'stacked';
    }
    const parsed = JSON.parse(raw);
    if (
      typeof parsed?.blockDisplayMode === 'string' &&
      ['traditional', 'stacked', 'minimal'].includes(parsed.blockDisplayMode)
    ) {
      return parsed.blockDisplayMode as BlockDisplayMode;
    }
  } catch (error) {
    console.warn('读取堆叠块显示模式缓存失败：', error);
  }
  return 'stacked';
};

const persistStackedHideBorders = (value: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(STACKED_HIDE_BORDERS_STORAGE_KEY, value ? 'true' : 'false');
  } catch (error) {
    console.warn('写入堆叠块边线设置失败：', error);
  }
};

/**
 * 快捷窗口自动展开设置的本地缓存（与主题缓存同理）：
 * 进入页面时个性化接口是异步的，首帧渲染必须同步拿到该设置，
 * 否则 QuickDock 只能先按默认值渲染再等接口纠偏，产生「先展开再收起」/「先空再展开」闪烁。
 */
export const loadCachedQuickDockAutoExpand = (): boolean => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return true;
  }
  const saved = window.localStorage.getItem(QUICK_DOCK_AUTO_EXPAND_STORAGE_KEY);
  return saved === null ? true : saved === 'true';
};

/** 是否已有自动展开设置的本地缓存（无缓存的首次访问仍需等接口就绪） */
export const hasCachedQuickDockAutoExpand = (): boolean => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return false;
  }
  return window.localStorage.getItem(QUICK_DOCK_AUTO_EXPAND_STORAGE_KEY) !== null;
};

const persistQuickDockAutoExpand = (value: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(QUICK_DOCK_AUTO_EXPAND_STORAGE_KEY, value ? 'true' : 'false');
  } catch (error) {
    console.warn('写入快捷窗口自动展开设置缓存失败：', error);
  }
};

const loadCachedMinimalExpandHeightLimited = (): boolean => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return true;
  }
  const saved = window.localStorage.getItem(MINIMAL_EXPAND_HEIGHT_LIMITED_STORAGE_KEY);
  return saved === null ? true : saved === 'true';
};

const persistMinimalExpandHeightLimited = (value: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(MINIMAL_EXPAND_HEIGHT_LIMITED_STORAGE_KEY, value ? 'true' : 'false');
  } catch (error) {
    console.warn('写入极简展开高度限制设置失败：', error);
  }
};

const defaultForm = (): PersonalForm => ({
  enabled: false,
  communication_style: 'default',
  conversation_continuity: 'medium',
  auto_generate_title: true,
  title_model: '',
  recent_conversations_prompt_enabled: false,
  recent_conversations_prompt_limit: DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT,
  project_memory_inject_limit: DEFAULT_PROJECT_MEMORY_INJECT_LIMIT,
  tool_intent_enabled: true,
  skill_hints_enabled: false,
  skill_strict_terminal_enabled: false,
  skill_strict_sub_agent_enabled: false,
  skill_strict_run_command_foreground_enabled: false,
  skill_strict_run_command_background_enabled: false,
  silent_tool_disable: false,
  hide_tool_approval_panel: true,
  enhanced_tool_display: true,
  compact_message_display: 'full',
  block_display_mode: loadCachedBlockDisplayMode(),
  show_status_avatar: true,
  show_git_status_bar: true,
  auto_open_terminal_panel: true,
  quick_dock_auto_expand: loadCachedQuickDockAutoExpand(),
  file_preview_auto_wrap: false,
  edit_summary_live_display: false,
  modify_history_enabled: true,
  stacked_hide_borders: loadCachedStackedHideBorders(),
  minimal_expand_height_limited: loadCachedMinimalExpandHeightLimited(),
  enhanced_tool_display_categories: [],
  enabled_skills: [],
  self_identify: '',
  user_name: '',
  use_custom_names: false,
  profession: '',
  tone: '',
  considerations: '',
  disabled_tool_categories: [],
  default_run_mode: null,
  default_reasoning_effort: null,
  default_permission_mode: 'unrestricted',
  default_work_mode: 'plan',
  versioning_enabled_by_default: true,
  versioning_backup_mode: 'shallow',
  versioning_restore_mode: 'overwrite',
  default_model: null,
  image_compression: 'original',
  auto_shallow_compress_enabled: false,
  auto_deep_compress_enabled: true,
  shallow_compress_trigger_tokens: null,
  shallow_compress_keep_recent_tools: null,
  shallow_compress_keep_user_turn_tools: null,
  shallow_compress_max_replace_per_round: null,
  shallow_compress_trigger_tool_calls_interval: null,
  deep_compress_trigger_tokens: null,
  deep_compress_form: 'file',
  agents_md_auto_inject: false,
  claude_md_auto_inject: false,
  agents_skills_scan_enabled: true,
  new_chat_button_behavior: 'route',
  group_sidebar_by_workspace: false,
  sidebar_pinned_workspaces: [],
  sidebar_workspace_order: [],
  theme: loadCachedTheme(),
  ui_locale: 'zh-CN' as 'zh-CN' | 'en-US',
  goal_review_mode: 'readonly',
  goal_end_conditions: ['max_turns'],
  goal_max_turns: 5,
  goal_max_tokens: null,
  review_agents: defaultReviewAgents()
});

const defaultExperimentState = (): ExperimentState => ({
  blockDisplayMode: 'stacked',
  compactMessageDisplay: 'full'
});

const loadExperimentState = (): ExperimentState => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return defaultExperimentState();
  }
  try {
    const raw = window.localStorage.getItem(EXPERIMENT_STORAGE_KEY);
    if (!raw) {
      return defaultExperimentState();
    }
    const parsed = JSON.parse(raw);

    // 兼容旧版 stackedBlocksEnabled
    let blockDisplayMode: BlockDisplayMode = defaultExperimentState().blockDisplayMode;
    if (
      typeof parsed?.blockDisplayMode === 'string' &&
      ['traditional', 'stacked', 'minimal'].includes(parsed.blockDisplayMode)
    ) {
      blockDisplayMode = parsed.blockDisplayMode;
    } else if (typeof parsed?.stackedBlocksEnabled === 'boolean') {
      // 兼容旧版：true -> stacked, false -> traditional
      blockDisplayMode = parsed.stackedBlocksEnabled ? 'stacked' : 'traditional';
    }

    let compactMessageDisplay: CompactMessageDisplay =
      defaultExperimentState().compactMessageDisplay;
    if (
      typeof parsed?.compactMessageDisplay === 'string' &&
      ['full', 'brief'].includes(parsed.compactMessageDisplay)
    ) {
      compactMessageDisplay = parsed.compactMessageDisplay;
    }

    return {
      blockDisplayMode,
      compactMessageDisplay
    };
  } catch (error) {
    console.warn('无法读取实验功能设置：', error);
    return defaultExperimentState();
  }
};

// 防抖自动保存计时器（非响应式，避免触发不必要的重渲染）
let autoSaveTimer: ReturnType<typeof setTimeout> | null = null;

export const usePersonalizationStore = defineStore('personalization', {
  state: (): PersonalizationState => ({
    visible: false,
    loading: false,
    saving: false,
    loaded: false,
    status: '',
    error: '',
    toggleUpdating: false,
    overlayPressActive: false,
    form: defaultForm(),
    toolCategories: [],
    skillsCatalog: [],
    recentConversationsPromptLimitRange: { ...DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE },
    projectMemoryInjectLimitMin: PROJECT_MEMORY_INJECT_LIMIT_MIN,
    experiments: loadExperimentState()
  }),
  getters: {
    // 语气预设：响应式依赖 currentLocale，语言切换时自动刷新
    tonePresets: () => {
      void currentLocale.value;
      return TONE_PRESET_KEYS.map((key) => t(key));
    }
  },
  actions: {
    async openDrawer() {
      this.visible = true;
      // 每次打开都刷新数据，确保显示最新内容
      if (!this.loading) {
        await this.fetchPersonalization();
      }
    },
    closeDrawer() {
      this.visible = false;
      this.overlayPressActive = false;
    },
    handleOverlayPressStart(event: Event) {
      // 只有直接按在遮罩上才算有效按压；按在卡片内部（如输入框）时重置状态，
      // 避免「按下遮罩→拖入卡片松开」遗留的卡死状态导致后续遮罩 mouseup 误关抽屉
      if (event.target !== event.currentTarget) {
        this.overlayPressActive = false;
        return;
      }
      if (event.type === 'mousedown' && (event as MouseEvent).button !== 0) {
        return;
      }
      this.overlayPressActive = true;
    },
    handleOverlayPressEnd(event: Event) {
      // 按下与松开都发生在遮罩上才关闭；输入框内拖选文字、松开落在遮罩上时
      // target 虽为遮罩，但按压并未起始于遮罩，不会关闭
      const shouldClose =
        this.overlayPressActive && event.target === event.currentTarget;
      this.overlayPressActive = false;
      if (!shouldClose) {
        return;
      }
      if (event.cancelable) {
        event.preventDefault();
      }
      this.closeDrawer();
    },
    handleOverlayPressCancel() {
      this.overlayPressActive = false;
    },
    async fetchPersonalization() {
      this.loading = true;
      this.error = '';
      try {
        const resp = await fetch('/api/personalization');
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || t('common.loadFailed'));
        }
        this.applyPersonalizationData(result.data || {});
        this.applyPersonalizationMeta(result);
        this.loaded = true;
      } catch (error: any) {
        this.error = error?.message || t('common.loadFailed');
      } finally {
        this.loading = false;
      }
    },
    applyPersonalizationData(data: any) {
      // 若后端未返回默认模型（旧版本接口），保持当前已选模型而不是回退到内置模型
      const fallbackModel =
        this.form && typeof this.form.default_model === 'string' ? this.form.default_model : null;
      const fallbackTheme = this.form?.theme || loadCachedTheme();
      this.form = {
        enabled: !!data.enabled,
        communication_style:
          data.communication_style === 'human_like' || data.communication_style === 'auto'
            ? data.communication_style
            : 'default',
        conversation_continuity:
          data.conversation_continuity === 'low' || data.conversation_continuity === 'high'
            ? data.conversation_continuity
            : 'medium',
        auto_generate_title: data.auto_generate_title !== false,
        title_model: typeof data.title_model === 'string' ? data.title_model : '',
        recent_conversations_prompt_enabled: !!data.recent_conversations_prompt_enabled,
        recent_conversations_prompt_limit: this.normalizeRecentConversationsPromptLimit(
          data.recent_conversations_prompt_limit
        ),
        project_memory_inject_limit: this.normalizeProjectMemoryInjectLimit(
          data.project_memory_inject_limit
        ),
        tool_intent_enabled: !!data.tool_intent_enabled,
        skill_hints_enabled: !!data.skill_hints_enabled,
        skill_strict_terminal_enabled: !!data.skill_strict_terminal_enabled,
        skill_strict_sub_agent_enabled: !!data.skill_strict_sub_agent_enabled,
        skill_strict_run_command_foreground_enabled:
          !!data.skill_strict_run_command_foreground_enabled,
        skill_strict_run_command_background_enabled:
          !!data.skill_strict_run_command_background_enabled,
        silent_tool_disable: !!data.silent_tool_disable,
        hide_tool_approval_panel: data.hide_tool_approval_panel !== false,
        enhanced_tool_display: data.enhanced_tool_display !== false,
        compact_message_display: data.compact_message_display === 'brief' ? 'brief' : 'full',
        block_display_mode:
          data.block_display_mode === 'traditional' ||
          data.block_display_mode === 'stacked' ||
          data.block_display_mode === 'minimal'
            ? data.block_display_mode
            : 'stacked',
        show_status_avatar: data.show_status_avatar !== false,
        show_git_status_bar: data.show_git_status_bar !== false,
        auto_open_terminal_panel: data.auto_open_terminal_panel !== false,
        quick_dock_auto_expand: data.quick_dock_auto_expand !== false,
        file_preview_auto_wrap: !!data.file_preview_auto_wrap,
        edit_summary_live_display: !!data.edit_summary_live_display,
        modify_history_enabled: data.modify_history_enabled !== false,
        stacked_hide_borders: !!data.stacked_hide_borders,
        minimal_expand_height_limited: data.minimal_expand_height_limited !== false,
        enhanced_tool_display_categories: Array.isArray(data.enhanced_tool_display_categories)
          ? data.enhanced_tool_display_categories.filter(
              (item: unknown) => typeof item === 'string'
            )
          : [],
        enabled_skills: Array.isArray(data.enabled_skills)
          ? data.enabled_skills.filter((item: unknown) => typeof item === 'string')
          : [],
        self_identify: data.self_identify || '',
        user_name: data.user_name || '',
        use_custom_names: !!data.use_custom_names,
        profession: data.profession || '',
        tone: data.tone || '',
        considerations:
          typeof data.considerations === 'string'
            ? data.considerations
            : Array.isArray(data.considerations)
              ? data.considerations.filter((item: unknown) => typeof item === 'string').join('\n')
              : '',
        disabled_tool_categories: Array.isArray(data.disabled_tool_categories)
          ? data.disabled_tool_categories.filter((item: unknown) => typeof item === 'string')
          : [],
        default_run_mode: (() => {
          // 历史值 deep 映射为 thinking
          const saved = data.default_run_mode === 'deep' ? 'thinking' : data.default_run_mode;
          return typeof saved === 'string' && RUN_MODE_OPTIONS.includes(saved as RunMode)
            ? (saved as RunMode)
            : null;
        })(),
        default_reasoning_effort:
          typeof data.default_reasoning_effort === 'string' &&
          REASONING_EFFORT_OPTIONS.includes(data.default_reasoning_effort as ReasoningEffort)
            ? (data.default_reasoning_effort as ReasoningEffort)
            : null,
        default_permission_mode:
          data.default_permission_mode === 'readonly' ||
          data.default_permission_mode === 'approval' ||
          data.default_permission_mode === 'auto_approval' ||
          data.default_permission_mode === 'unrestricted'
            ? data.default_permission_mode
            : 'unrestricted',
        default_work_mode:
          data.default_work_mode === 'plan' ||
          data.default_work_mode === 'ask' ||
          data.default_work_mode === 'execute'
            ? data.default_work_mode
            : 'plan',
        versioning_enabled_by_default: data.versioning_enabled_by_default !== false,
        versioning_backup_mode: data.versioning_backup_mode === 'full' ? 'full' : 'shallow',
        versioning_restore_mode: 'overwrite',
        default_model: typeof data.default_model === 'string' ? data.default_model : fallbackModel,
        image_compression:
          typeof data.image_compression === 'string' ? data.image_compression : 'original',
        auto_shallow_compress_enabled: !!data.auto_shallow_compress_enabled,
        auto_deep_compress_enabled: !!data.auto_deep_compress_enabled,
        shallow_compress_trigger_tokens: this.normalizeCompressionNumber(
          data.shallow_compress_trigger_tokens
        ),
        shallow_compress_keep_recent_tools: this.normalizeCompressionNumber(
          data.shallow_compress_keep_recent_tools
        ),
        shallow_compress_keep_user_turn_tools: this.normalizeCompressionNumber(
          data.shallow_compress_keep_user_turn_tools
        ),
        shallow_compress_max_replace_per_round: this.normalizeCompressionNumber(
          data.shallow_compress_max_replace_per_round
        ),
        shallow_compress_trigger_tool_calls_interval: this.normalizeCompressionNumber(
          data.shallow_compress_trigger_tool_calls_interval
        ),
        deep_compress_trigger_tokens: this.normalizeCompressionNumber(
          data.deep_compress_trigger_tokens
        ),
        deep_compress_form: data.deep_compress_form === 'inject' ? 'inject' : 'file',
        agents_md_auto_inject: !!data.agents_md_auto_inject,
        claude_md_auto_inject: !!data.claude_md_auto_inject,
        agents_skills_scan_enabled: data.agents_skills_scan_enabled === undefined ? true : !!data.agents_skills_scan_enabled,
        new_chat_button_behavior: data.new_chat_button_behavior === 'blank' ? 'blank' : 'route',
        group_sidebar_by_workspace: !!data.group_sidebar_by_workspace,
        sidebar_pinned_workspaces: Array.isArray(data.sidebar_pinned_workspaces)
          ? data.sidebar_pinned_workspaces.filter((item: unknown) => typeof item === 'string')
          : [],
        sidebar_workspace_order: Array.isArray(data.sidebar_workspace_order)
          ? data.sidebar_workspace_order.filter((item: unknown) => typeof item === 'string')
          : [],
        theme: ['classic', 'light', 'dark'].includes(data.theme) ? data.theme : fallbackTheme,
        ui_locale: ['zh-CN', 'en-US'].includes(data.ui_locale)
          ? data.ui_locale
          : (this.form?.ui_locale || 'zh-CN'),
        goal_review_mode: data.goal_review_mode === 'active' ? 'active' : 'readonly',
        goal_end_conditions: Array.isArray(data.goal_end_conditions)
          ? data.goal_end_conditions.filter((x: any) => x === 'max_turns' || x === 'max_tokens')
          : ['max_turns'],
        goal_max_turns:
          typeof data.goal_max_turns === 'number' && data.goal_max_turns > 0
            ? data.goal_max_turns
            : 5,
        goal_max_tokens:
          typeof data.goal_max_tokens === 'number' && data.goal_max_tokens > 0
            ? data.goal_max_tokens
            : null,
        review_agents: sanitizeReviewAgents(data.review_agents)
      };
      // 如果theme发生变化，应用到界面
      const currentTheme =
        typeof window !== 'undefined' && window.localStorage
          ? window.localStorage.getItem(THEME_STORAGE_KEY)
          : null;
      if (this.form.theme !== currentTheme) {
        this.applyTheme(this.form.theme);
      }
      // 界面语言以后端个人偏好为准（多设备/多工作区共享），覆盖本地缓存
      if (this.form.ui_locale && this.form.ui_locale !== currentLocale.value) {
        setLocale(this.form.ui_locale);
      }
      persistStackedHideBorders(this.form.stacked_hide_borders);
      persistMinimalExpandHeightLimited(this.form.minimal_expand_height_limited);
      persistQuickDockAutoExpand(this.form.quick_dock_auto_expand);
      // 简略消息显示：以配置文件为准，同步到旧版 localStorage 镜像供 ChatArea 读取。
      // 一次性迁移：后端仍为默认 full，但本地缓存遗留 brief（旧版纯前端记录）时，回写到配置文件。
      const cachedCompact = this.experiments.compactMessageDisplay;
      if (this.form.compact_message_display === 'full' && cachedCompact === 'brief') {
        this.form = { ...this.form, compact_message_display: 'brief' };
        void this.persistCompactMessageDisplay('brief');
      }
      // 一次性迁移：堆叠块显示模式旧版仅在 localStorage 保存，后端默认 stacked 时回写本地缓存值。
      const cachedBlockMode = this.experiments.blockDisplayMode;
      if (this.form.block_display_mode === 'stacked' && cachedBlockMode !== 'stacked') {
        this.form = { ...this.form, block_display_mode: cachedBlockMode };
      }
      this.experiments = {
        ...this.experiments,
        compactMessageDisplay: this.form.compact_message_display,
        blockDisplayMode: this.form.block_display_mode
      };
      this.persistExperiments();
      this.clearFeedback();
    },
    applyTheme(theme: 'classic' | 'light' | 'dark') {
      if (typeof window === 'undefined') return;
      // 同步到localStorage
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
      // 应用主题
      const root = document.documentElement;
      root.setAttribute('data-theme', theme);
      document.body.setAttribute('data-theme', theme);
    },
    normalizeCompressionNumber(value: any): number | null {
      if (value === null || typeof value === 'undefined' || value === '') {
        return null;
      }
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return null;
      }
      const rounded = Math.round(parsed);
      if (rounded <= 0) {
        return null;
      }
      return rounded;
    },
    normalizeRecentConversationsPromptLimit(value: any): number {
      const parsed = Number(value);
      const min =
        this.recentConversationsPromptLimitRange.min ??
        DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE.min;
      const max =
        this.recentConversationsPromptLimitRange.max ??
        DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE.max;
      if (!Number.isFinite(parsed)) {
        return DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT;
      }
      return Math.max(min, Math.min(max, Math.round(parsed)));
    },
    normalizeProjectMemoryInjectLimit(value: any): number | null {
      // undefined（老接口未下发）/非法值 → 默认 20；null/''/0/负数 → null（无上限）；正整数钳到 >= min
      if (typeof value === 'undefined') {
        return DEFAULT_PROJECT_MEMORY_INJECT_LIMIT;
      }
      if (value === null || value === '') {
        return null;
      }
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return DEFAULT_PROJECT_MEMORY_INJECT_LIMIT;
      }
      const rounded = Math.round(parsed);
      if (rounded <= 0) {
        return null;
      }
      const min = this.projectMemoryInjectLimitMin ?? PROJECT_MEMORY_INJECT_LIMIT_MIN;
      return Math.max(min, rounded);
    },
    applyPersonalizationMeta(payload: any) {
      if (payload && payload.recent_conversations_prompt_limit_range) {
        const { min, max } = payload.recent_conversations_prompt_limit_range;
        this.recentConversationsPromptLimitRange = {
          min: typeof min === 'number' ? min : DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE.min,
          max: typeof max === 'number' ? max : DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE.max
        };
      } else {
        this.recentConversationsPromptLimitRange = {
          ...DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT_RANGE
        };
      }
      if (payload && payload.project_memory_inject_limit_range) {
        const { min } = payload.project_memory_inject_limit_range;
        this.projectMemoryInjectLimitMin =
          typeof min === 'number' ? min : PROJECT_MEMORY_INJECT_LIMIT_MIN;
      } else {
        this.projectMemoryInjectLimitMin = PROJECT_MEMORY_INJECT_LIMIT_MIN;
      }
      if (payload && Array.isArray(payload.tool_categories)) {
        this.toolCategories = payload.tool_categories
          .map((item: { id?: string; label?: string } = {}) => ({
            id: typeof item.id === 'string' ? item.id : String(item.id ?? ''),
            label:
              (item.label && String(item.label)) ||
              (typeof item.id === 'string' ? item.id : String(item.id ?? ''))
          }))
          .filter((item: { id: string }) => !!item.id);
      } else {
        this.toolCategories = [];
      }
      if (payload && Array.isArray(payload.skills_catalog)) {
        this.skillsCatalog = payload.skills_catalog
          .map((item: { id?: string; label?: string; description?: string } = {}) => ({
            id: typeof item.id === 'string' ? item.id : String(item.id ?? ''),
            label:
              (item.label && String(item.label)) ||
              (typeof item.id === 'string' ? item.id : String(item.id ?? '')),
            description: typeof item.description === 'string' ? item.description : undefined
          }))
          .filter((item: { id: string }) => !!item.id);
      } else {
        this.skillsCatalog = [];
      }
    },
    clearFeedback() {
      this.status = '';
      this.error = '';
    },
    async toggleEnabled() {
      if (this.toggleUpdating) {
        return;
      }
      const newValue = !this.form.enabled;
      const previousValue = this.form.enabled;
      this.toggleUpdating = true;
      this.status = '';
      this.error = '';
      this.form.enabled = newValue;
      try {
        const resp = await fetch('/api/personalization', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: newValue })
        });
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || t('stores.updateFailed'));
        }
        if (result.data) {
          this.applyPersonalizationData(result.data);
        }
        this.applyPersonalizationMeta(result);
        const statusLabel = newValue ? t('stores.enabled') : t('stores.disabled');
        this.status = statusLabel;
        setTimeout(() => {
          if (this.status === statusLabel) {
            this.status = '';
          }
        }, 2000);
      } catch (error: any) {
        this.form.enabled = previousValue;
        this.error = error?.message || t('stores.updateFailed');
      } finally {
        this.toggleUpdating = false;
      }
    },
    async save() {
      if (this.saving) {
        return;
      }
      this.saving = true;
      this.status = '';
      this.error = '';
      try {
        const shallowTrigger =
          this.normalizeCompressionNumber(this.form.shallow_compress_trigger_tokens) ??
          DEFAULT_SHALLOW_COMPRESS_TRIGGER_TOKENS;
        const deepTrigger =
          this.normalizeCompressionNumber(this.form.deep_compress_trigger_tokens) ??
          DEFAULT_DEEP_COMPRESS_TRIGGER_TOKENS;
        if (deepTrigger <= shallowTrigger) {
          throw new Error(t('stores.deepCompressMustExceedShallow'));
        }
        const resp = await fetch('/api/personalization', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.form)
        });
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || t('stores.saveFailed'));
        }
        this.applyPersonalizationData(result.data || {});
        this.applyPersonalizationMeta(result);
        const savedStatus = t('stores.saved');
        this.status = savedStatus;
        setTimeout(() => {
          if (this.status === savedStatus) {
            this.status = '';
          }
        }, 3000);
      } catch (error: any) {
        this.error = error?.message || t('stores.saveFailed');
      } finally {
        this.saving = false;
      }
    },
    updateField(payload: { key: keyof PersonalForm; value: any }) {
      if (!payload || !payload.key) {
        return;
      }
      this.form = {
        ...this.form,
        [payload.key]: payload.value
      };
      if (payload.key === 'stacked_hide_borders') {
        persistStackedHideBorders(!!payload.value);
      }
      if (payload.key === 'minimal_expand_height_limited') {
        persistMinimalExpandHeightLimited(!!payload.value);
      }
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    /** 防抖自动保存：500ms 内多次调用只执行最后一次 */
    scheduleAutoSave() {
      if (autoSaveTimer) {
        clearTimeout(autoSaveTimer);
      }
      autoSaveTimer = setTimeout(() => {
        autoSaveTimer = null;
        this.autoSave();
      }, 500);
    },
    /** 静默自动保存，成功不显示提示，失败显示错误 */
    async autoSave() {
      if (this.saving) {
        return;
      }
      this.saving = true;
      try {
        const resp = await fetch('/api/personalization', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.form)
        });
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          throw new Error(result.error || t('stores.autoSaveFailed'));
        }
        // 静默成功，不刷新表单（避免打断用户编辑）
      } catch (error: any) {
        this.error = error?.message || t('stores.autoSaveFailed');
      } finally {
        this.saving = false;
      }
    },
    setRecentConversationsPromptLimit(value: number | null) {
      const target =
        value === null || typeof value === 'undefined'
          ? DEFAULT_RECENT_CONVERSATIONS_PROMPT_LIMIT
          : this.normalizeRecentConversationsPromptLimit(value);
      this.form = {
        ...this.form,
        recent_conversations_prompt_limit: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setProjectMemoryInjectLimit(value: number | null) {
      // null = 无上限
      const target =
        value === null || typeof value === 'undefined'
          ? null
          : this.normalizeProjectMemoryInjectLimit(value);
      this.form = {
        ...this.form,
        project_memory_inject_limit: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    restoreProjectMemoryInjectLimit() {
      this.form = {
        ...this.form,
        project_memory_inject_limit: DEFAULT_PROJECT_MEMORY_INJECT_LIMIT
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    toggleDefaultToolCategory(categoryId: string) {
      if (!categoryId) {
        return;
      }
      const current = new Set(this.form.disabled_tool_categories || []);
      if (current.has(categoryId)) {
        current.delete(categoryId);
      } else {
        current.add(categoryId);
      }
      this.form = {
        ...this.form,
        disabled_tool_categories: Array.from(current)
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    toggleSkill(skillId: string) {
      if (!skillId) {
        return;
      }
      const current = new Set(this.form.enabled_skills || []);
      if (current.has(skillId)) {
        current.delete(skillId);
      } else {
        current.add(skillId);
      }
      this.form = {
        ...this.form,
        enabled_skills: Array.from(current)
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setDefaultRunMode(mode: RunMode | null) {
      let target: RunMode | null = null;
      if (typeof mode === 'string' && RUN_MODE_OPTIONS.includes(mode as RunMode)) {
        target = mode as RunMode;
      }
      this.form = {
        ...this.form,
        default_run_mode: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setDefaultReasoningEffort(effort: ReasoningEffort | null) {
      let target: ReasoningEffort | null = null;
      if (typeof effort === 'string' && REASONING_EFFORT_OPTIONS.includes(effort as ReasoningEffort)) {
        target = effort as ReasoningEffort;
      }
      this.form = {
        ...this.form,
        default_reasoning_effort: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setDefaultPermissionMode(mode: PermissionMode) {
      const allowed: PermissionMode[] = ['readonly', 'approval', 'auto_approval', 'unrestricted'];
      const target = allowed.includes(mode) ? mode : 'unrestricted';
      this.form = {
        ...this.form,
        default_permission_mode: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setDefaultWorkMode(mode: WorkMode) {
      const allowed: WorkMode[] = ['plan', 'ask', 'execute'];
      const target = allowed.includes(mode) ? mode : 'plan';
      this.form = {
        ...this.form,
        default_work_mode: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setVersioningRestoreMode(_mode: 'overwrite') {
      const target: 'overwrite' = 'overwrite';
      this.form = {
        ...this.form,
        versioning_restore_mode: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setVersioningBackupMode(mode: VersioningBackupMode) {
      const target: VersioningBackupMode = mode === 'full' ? 'full' : 'shallow';
      this.form = {
        ...this.form,
        versioning_backup_mode: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setDefaultModel(model: string | null) {
      const modelStore = useModelStore();
      const allowed = new Set((modelStore.models || []).map((m) => m.key));
      const target = typeof model === 'string' && allowed.has(model) ? model : null;
      this.form = {
        ...this.form,
        default_model: target
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setImageCompression(mode: string) {
      const allowed = ['original', '1080p', '720p', '540p'];
      if (!allowed.includes(mode)) return;
      this.form = {
        ...this.form,
        image_compression: mode
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    applyTonePreset(preset: string) {
      if (!preset) {
        return;
      }
      this.form = {
        ...this.form,
        tone: preset
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    updateConsiderations(value: string) {
      this.form = {
        ...this.form,
        considerations: value
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    async logout() {
      try {
        const resp = await fetch('/logout', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store'
        });
        let payload: any = null;
        try {
          payload = await resp.json();
        } catch (_err) {
          payload = null;
        }
        if (resp.ok && (!payload || payload.success !== false)) {
          window.location.replace(`/login?logged_out=1&ts=${Date.now()}`);
          return;
        }
        // 接口失败时，走 GET /logout 做兜底清理
        window.location.replace(`/logout?ts=${Date.now()}`);
      } catch (error: any) {
        console.error('退出登录失败:', error);
        this.error = error?.message || t('stores.logoutFailedRetry');
        // 兜底：直接访问 GET /logout
        window.location.replace(`/logout?ts=${Date.now()}`);
      }
    },
    persistExperiments() {
      if (typeof window === 'undefined' || !window.localStorage) {
        return;
      }
      try {
        window.localStorage.setItem(EXPERIMENT_STORAGE_KEY, JSON.stringify(this.experiments));
      } catch (error) {
        console.warn('写入实验功能设置失败：', error);
      }
    },
    setBlockDisplayMode(mode: BlockDisplayMode) {
      this.experiments = {
        ...this.experiments,
        blockDisplayMode: mode
      };
      this.persistExperiments();
      this.form = {
        ...this.form,
        block_display_mode: mode
      };
      this.scheduleAutoSave();
    },
    setCompactMessageDisplay(mode: CompactMessageDisplay) {
      const target: CompactMessageDisplay = mode === 'brief' ? 'brief' : 'full';
      this.form = {
        ...this.form,
        compact_message_display: target
      };
      // 同步旧版 localStorage 镜像，保持兼容并避免迁移逻辑回写旧值
      this.experiments = {
        ...this.experiments,
        compactMessageDisplay: target
      };
      this.persistExperiments();
      this.clearFeedback();
      // 持久化到后端配置文件（即时保存，参照主题/默认隐藏工作区）
      void this.persistCompactMessageDisplay(target);
    },
    async persistCompactMessageDisplay(mode: CompactMessageDisplay) {
      try {
        const resp = await fetch('/api/personalization', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ compact_message_display: mode })
        });
        const result = await resp.json();
        if (!resp.ok || !result.success) {
          console.warn('保存简略消息显示失败:', result?.error);
        }
      } catch (error) {
        console.warn('保存简略消息显示失败:', error);
      }
    },
    setCommunicationStyle(style: CommunicationStyle) {
      this.form = {
        ...this.form,
        communication_style: style
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    },
    setConversationContinuity(value: ConversationContinuity) {
      this.form = {
        ...this.form,
        conversation_continuity: value
      };
      this.clearFeedback();
      this.scheduleAutoSave();
    }
  }
});
