<template>
  <transition name="personal-page-fade" appear>
    <div
      class="personal-page-overlay"
      v-if="visible"
      @click="closeDropdown"
      @mousedown="personalization.handleOverlayPressStart($event)"
      @mouseup="personalization.handleOverlayPressEnd($event)"
      @mouseleave.self="personalization.handleOverlayPressCancel"
      @touchstart.self.prevent="personalization.handleOverlayPressStart($event)"
      @touchend="personalization.handleOverlayPressEnd($event)"
      @touchcancel.self="personalization.handleOverlayPressCancel"
    >
      <div
        class="personal-page-card settings-redesign-card"
        :class="{ 'mobile-in-sub': mobileInSubPage }"
        data-tutorial="personal-card"
      >
        <div class="personalization-body settings-redesign-body" v-if="!loading">
          <form class="personal-form settings-redesign-form">
            <div class="settings-redesign-layout">
              <nav class="settings-redesign-nav" :aria-label="$t('personalization.tabAriaLabel')">
                <div class="settings-mobile-bar">
                  <span class="settings-mobile-bar-cell" aria-hidden="true"></span>
                  <span class="settings-mobile-bar-title">{{ $t('common.settings') }}</span>
                  <button
                    type="button"
                    class="settings-mobile-bar-btn"
                    :aria-label="$t('personalization.closePersonalSpaceAriaLabel')"
                    @click="personalization.closeDrawer()"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                      <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                  </button>
                </div>
                <div class="settings-nav-head">
                  <button
                    type="button"
                    class="settings-close-button"
                    data-tutorial="personal-close"
                    :aria-label="$t('personalization.closePersonalSpaceAriaLabel')"
                    @click="personalization.closeDrawer()"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round">
                      <path d="M6 6l12 12M18 6 6 18" />
                    </svg>
                  </button>
                </div>
                <div class="settings-redesign-tabs">
                <button
                  v-for="tab in personalTabs"
                  :key="tab.id"
                  type="button"
                  class="settings-redesign-tab"
                  :data-tutorial="`personal-tab-${tab.id}`"
                  :class="{ active: activeTab === tab.id }"
                  :aria-pressed="activeTab === tab.id"
                  @click.prevent="setActiveTab(tab.id)"
                >
                  <span
                    v-if="tab.id === 'context'"
                    class="settings-tab-icon settings-tab-icon--chat"
                    aria-hidden="true"
                  >
                    <!-- 24 viewBox + stroke-width 2 与其他图标同规范；内容保留 18 宽（75% 占位，与其他图标视觉等大），x-2/y-0.5 居中 -->
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M3 6c0-1.38 1.12-2.5 2.5-2.5h13c1.38 0 2.5 1.12 2.5 2.5v8.5c0 1.38-1.12 2.5-2.5 2.5h-5.6l-3.4 3.2.6-3.2H5.5c-1.38 0-2.5-1.12-2.5-2.5V6z" />
                      <path d="M7 9h10" />
                      <path d="M7 12.5h6" />
                    </svg>
                  </span>
                  <span
                    v-else
                    class="icon settings-tab-icon"
                    :style="settingsTabIconStyle(tab.icon)"
                    aria-hidden="true"
                  ></span>
                  <span class="settings-tab-label">{{ $t(tab.labelKey) }}</span>
                  <svg
                    class="settings-tab-chevron"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                  >
                    <path d="m9 6 6 6-6 6" />
                  </svg>
                </button>
                </div>
              </nav>

              <section class="settings-redesign-content" data-tutorial="personal-content-shell">
                <div class="settings-mobile-bar">
                  <button
                    type="button"
                    class="settings-mobile-bar-btn settings-mobile-back"
                    @click="backToNavList"
                  >
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                      <path d="m15 6-6 6 6 6" />
                    </svg>
                    <span>{{ $t('common.back') }}</span>
                  </button>
                  <span class="settings-mobile-bar-title">{{ activeTabLabel }}</span>
                  <span class="settings-mobile-bar-cell" aria-hidden="true"></span>
                </div>
                <header class="settings-redesign-content-header">
                  <h2>{{ activeTabLabel }}</h2>
                </header>

                <div class="settings-redesign-scroll">
                  <!-- 窄屏布局下去掉 out-in（改默认同时切换 + CSS 隐藏离场元素），避免先闪旧内容 -->
                  <transition name="personal-page-vertical" :mode="isMobileLayout() ? undefined : 'out-in'">
                                        <GeneralTab v-if="activeTab === 'general'" key="general" />

                    <PreferencesTab v-else-if="activeTab === 'preferences'" key="preferences" />

                    <ModelTab v-else-if="activeTab === 'model'" key="model" />

                    <AppearanceTab v-else-if="activeTab === 'appearance'" key="appearance" />

                    <WorkspaceTab v-else-if="activeTab === 'workspace'" key="workspace" />

                    <ContextTab v-else-if="activeTab === 'context'" key="context" />

                    <ToolsTab v-else-if="activeTab === 'tools'" key="tools" />

                    <FilesTab v-else-if="activeTab === 'files'" key="files" />

                    <DataTab v-else-if="activeTab === 'data'" key="data" />

                    <VoiceTab v-else-if="activeTab === 'voice'" key="voice" />

                    <SubAgentsTab v-else-if="activeTab === 'sub-agents'" key="sub-agents" />

                    <ReviewAgentsTab v-else-if="activeTab === 'review-agents'" key="review-agents" />

                    <AdminTab v-else-if="activeTab === 'admin'" key="admin" />
                  </transition>
                </div>

                <div class="settings-save-bar">
                  <div class="personal-status-group">
                    <transition name="personal-status-fade"
                      ><span class="status success" v-if="status">{{ status }}</span></transition
                    ><transition name="personal-status-fade"
                      ><span class="status error" v-if="error">{{ error }}</span></transition
                    >
                  </div>
                </div>
              </section>
            </div>
          </form>
        </div>
        <div class="personalization-loading" v-else>{{ $t('personalization.loadingPersonalization') }}</div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick, provide } from 'vue';
import { storeToRefs } from 'pinia';
import FancyCheck from '@/components/common/FancyCheck.vue';
import GeneralTab from './tabs/GeneralTab.vue';
import PreferencesTab from './tabs/PreferencesTab.vue';
import ModelTab from './tabs/ModelTab.vue';
import AppearanceTab from './tabs/AppearanceTab.vue';
import WorkspaceTab from './tabs/WorkspaceTab.vue';
import ContextTab from './tabs/ContextTab.vue';
import ToolsTab from './tabs/ToolsTab.vue';
import FilesTab from './tabs/FilesTab.vue';
import DataTab from './tabs/DataTab.vue';
import VoiceTab from './tabs/VoiceTab.vue';
import SubAgentsTab from './tabs/SubAgentsTab.vue';
import ReviewAgentsTab from './tabs/ReviewAgentsTab.vue';
import AdminTab from './tabs/AdminTab.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import type { ReviewAgentKey, ReviewAgentSetting } from '@/stores/personalization';
import { useResourceStore } from '@/stores/resource';
import { useUiStore } from '@/stores/ui';
import { usePolicyStore } from '@/stores/policy';
import { useTutorialStore } from '@/stores/tutorial';
import { useModelStore } from '@/stores/model';
import { formatTokenCount } from '@/utils/formatters';
import { ICONS } from '@/utils/icons';
import { useTheme } from '@/utils/theme';
import type { ThemeKey } from '@/utils/theme';
import { t, currentLocale, useLocale } from '@/locales';
import type { LocaleKey } from '@/locales';
import './styles/settings-shared.css';

defineOptions({ name: 'PersonalizationDrawer' });

const personalization = usePersonalizationStore();
const resourceStore = useResourceStore();
const uiStore = useUiStore();
const tutorialStore = useTutorialStore();
const {
  visible,
  loading,
  form,
  tonePresets,
  status,
  error,
  saving,
  toggleUpdating,
  toolCategories,
  skillsCatalog,
  recentConversationsPromptLimitRange,
  projectMemoryInjectLimitMin,
  experiments
} = storeToRefs(personalization);

// ---- 目标模式设置辅助 ----
const goalTokenLimitEnabled = computed(
  () => typeof form.value.goal_max_tokens === 'number' && form.value.goal_max_tokens > 0
);

const clampGoalMaxTurns = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 5;
  return Math.min(100, Math.max(1, n));
};

const clampGoalMaxTokens = (raw: any): number => {
  const n = Math.round(Number(raw));
  if (!Number.isFinite(n)) return 100000;
  return Math.min(100000000, Math.max(1000, n));
};

const toggleGoalTokenLimit = (enabled: boolean) => {
  personalization.updateField({
    key: 'goal_max_tokens',
    value: enabled ? form.value.goal_max_tokens || 100000 : null
  });
};

type IconKey = keyof typeof ICONS;

type PersonalTab =
  | 'general'
  | 'preferences'
  | 'model'
  | 'appearance'
  | 'workspace'
  | 'context'
  | 'tools'
  | 'files'
  | 'data'
  | 'voice'
  | 'sub-agents'
  | 'review-agents'
  | 'admin';

const baseTabs = [
  { id: 'general', labelKey: 'personalization.tabGeneral', icon: 'settings' },
  { id: 'preferences', labelKey: 'personalization.tabPreferences', icon: 'userPen' },
  { id: 'model', labelKey: 'personalization.tabModel', icon: 'brainCog' },
  { id: 'appearance', labelKey: 'personalization.tabAppearance', icon: 'monitor' },
  { id: 'workspace', labelKey: 'personalization.tabWorkspace', icon: 'folder' },
  { id: 'context', labelKey: 'personalization.tabContext', icon: 'chatBubble' },
  { id: 'tools', labelKey: 'personalization.tabTools', icon: 'wrench' },
  { id: 'files', labelKey: 'personalization.tabFiles', icon: 'file' },
  { id: 'data', labelKey: 'personalization.tabData', icon: 'layers' },
  { id: 'voice', labelKey: 'personalization.tabVoice', icon: 'mic' },
  { id: 'sub-agents', labelKey: 'personalization.tabSubAgents', icon: 'bot' },
  { id: 'review-agents', labelKey: 'personalization.tabReviewAgents', icon: 'checkbox' }
] as const satisfies ReadonlyArray<{ id: PersonalTab; labelKey: string; icon: IconKey }>;

const sessionRole = ref('');
const sessionHostMode = ref(false);

const isAdmin = computed(() => {
  const quotaRole = (resourceStore.usageQuota.role || '').toLowerCase();
  const loginRole = (sessionRole.value || '').toLowerCase();
  return quotaRole === 'admin' || loginRole === 'admin';
});
const isHostMode = computed(() => {
  const mode = (resourceStore.containerStatus?.mode || '').toLowerCase();
  if (mode) {
    return mode === 'host';
  }
  return sessionHostMode.value;
});
const showMcpConfigEntry = computed(() => isAdmin.value && isHostMode.value);
const isAppShell = computed(() => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('app_shell')) return true;
  return Boolean((window as any)?.AndroidThemeBridge);
});

const personalTabs = computed(() => {
  const tabs: Array<{ id: PersonalTab; labelKey: string; icon: IconKey }> = [...baseTabs];
  if (isAdmin.value) {
    tabs.push({ id: 'admin', labelKey: 'personalization.tabAdmin', icon: 'wrench' });
  }
  return tabs;
});

const activeTab = ref<PersonalTab>('general');
const activeDropdown = ref<string | null>(null);
const floatingMenuStyle = ref<Record<string, string>>({});

const activeTabLabel = computed(() => {
  void currentLocale.value;
  const tab = personalTabs.value.find((tab) => tab.id === activeTab.value);
  return tab ? t(tab.labelKey) : t('personalization.tabGeneral');
});

// ── 语音模型下载 ──
const voiceModelReady = ref(false);
const voiceModelPartial = ref(false); // 文件存在但不完整
const voiceDownloading = ref(false);
const voiceDownloadPercent = ref(0);
const voiceDownloadMsg = ref('');

const checkVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (bridge) {
    try {
      if (typeof bridge.isModelReady === 'function') {
        voiceModelReady.value = bridge.isModelReady();
      }
      if (!voiceModelReady.value && typeof bridge.isModelPartial === 'function') {
        voiceModelPartial.value = bridge.isModelPartial();
      }
    } catch (_) {
      /* ignore */
    }
  }
};

const downloadVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) {
    alert(t('personalization.voiceAppOnlyAlert'));
    return;
  }
  voiceDownloading.value = true;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = t('personalization.voicePreparing');
  voiceModelPartial.value = false;

  (window as any).__onVoiceDownloadProgress = (pct: number, msg: string) => {
    voiceDownloadPercent.value = pct;
    voiceDownloadMsg.value = msg;
    if (pct >= 100) {
      voiceDownloading.value = false;
      voiceModelReady.value = true;
      voiceModelPartial.value = false;
    }
  };
  (window as any).__onVoiceModelReady = () => {
    voiceModelReady.value = true;
    voiceDownloading.value = false;
    voiceModelPartial.value = false;
  };

  bridge.downloadModel();
};

const deleteVoiceModel = () => {
  const bridge = (window as any)?.AndroidVoiceBridge;
  if (!bridge) return;
  if (typeof bridge.deleteModel === 'function') {
    bridge.deleteModel();
  }
  voiceModelReady.value = false;
  voiceModelPartial.value = false;
  voiceDownloadPercent.value = 0;
  voiceDownloadMsg.value = '';
};

// 打开个人空间时检查模型状态
checkVoiceModel();

const updateFloatingMenuPosition = async () => {
  if (!activeDropdown.value || typeof window === 'undefined') {
    floatingMenuStyle.value = {};
    return;
  }
  await nextTick();
  const button = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-select-button'
  );
  const menu = document.querySelector<HTMLElement>(
    '.settings-select-wrap.open .settings-floating-menu'
  );
  if (!button) {
    return;
  }
  const rect = button.getBoundingClientRect();
  const menuWidth = Math.min(300, Math.max(240, window.innerWidth - 32));
  const left = Math.max(16, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 16));

  const padding = 16;
  const gap = 10;

  // 测量单个选项高度，计算 4 选项固定 max-height
  const optionEl = menu?.querySelector<HTMLElement>('.settings-menu-option');
  const optionHeight = optionEl ? optionEl.getBoundingClientRect().height : 44;
  const menuPadding = 10; // menu padding 5px top + 5px bottom
  const fixedMaxHeight = Math.round(optionHeight * 4) + menuPadding;

  // 先限制菜单高度为固定值，再次获取实际高度
  if (menu) {
    menu.style.maxHeight = `${fixedMaxHeight}px`;
  }
  // 重新读取受限后的菜单高度
  const menuHeight = menu?.getBoundingClientRect().height || fixedMaxHeight;

  const spaceBelow = window.innerHeight - rect.bottom - padding;
  const spaceAbove = rect.top - padding;

  let top: number;
  let maxHeight: number | undefined;

  if (spaceBelow >= menuHeight) {
    // 下方空间足够完整显示
    top = rect.bottom + gap;
  } else if (spaceAbove >= menuHeight) {
    // 上方空间足够完整显示，翻转到上方
    top = rect.top - menuHeight - gap;
  } else if (spaceBelow >= spaceAbove) {
    // 上下都不够，下方空间相对更大：限制高度到可用空间，贴按钮向下
    const availableHeight = Math.max(80, spaceBelow - gap);
    maxHeight = availableHeight;
    top = rect.bottom + gap;
  } else {
    // 上方空间相对更大：限制高度到可用空间，翻转到上方
    const availableHeight = Math.max(80, spaceAbove - gap);
    maxHeight = availableHeight;
    top = Math.max(padding, rect.top - availableHeight - gap);
  }

  floatingMenuStyle.value = {
    position: 'fixed',
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    right: 'auto',
    width: `${Math.round(menuWidth)}px`,
    zIndex: '2147483647',
    ...(maxHeight !== undefined ? { maxHeight: `${Math.round(maxHeight)}px` } : {})
  };
};

const toggleDropdown = async (key: string) => {
  activeDropdown.value = activeDropdown.value === key ? null : key;
  await updateFloatingMenuPosition();
};

const closeDropdown = () => {
  activeDropdown.value = null;
  floatingMenuStyle.value = {};
};

const settingsTabIconStyle = (icon: IconKey) => ({
  '--icon-src': `url(${ICONS[icon]})`
});

onMounted(() => {
  window.addEventListener('resize', updateFloatingMenuPosition);
  window.addEventListener('scroll', updateFloatingMenuPosition, true);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateFloatingMenuPosition);
  window.removeEventListener('scroll', updateFloatingMenuPosition, true);
});

type RunModeValue = 'fast' | 'thinking' | null;
type ReasoningEffortValue = 'low' | 'medium' | 'high' | 'xhigh' | 'max' | null;
type PermissionModeValue = 'readonly' | 'approval' | 'auto_approval' | 'unrestricted';
type CompressionField =
  | 'shallow_compress_trigger_tokens'
  | 'shallow_compress_keep_recent_tools'
  | 'shallow_compress_keep_user_turn_tools'
  | 'shallow_compress_max_replace_per_round'
  | 'shallow_compress_trigger_tool_calls_interval'
  | 'deep_compress_trigger_tokens';

const runModeOptions: Array<{
  id: string;
  labelKey: string;
  descKey: string;
  value: RunModeValue;
}> = [
  { id: 'fast', labelKey: 'personalization.runModeFast', descKey: 'personalization.runModeFastDesc', value: 'fast' },
  { id: 'thinking', labelKey: 'personalization.runModeThinking', descKey: 'personalization.runModeThinkingDesc', value: 'thinking' }
];

const reasoningEffortOptions: Array<{
  id: string;
  labelKey: string;
  descKey: string;
  value: ReasoningEffortValue;
}> = [
  { id: 'default', labelKey: 'personalization.effortDefault', descKey: 'personalization.effortDefaultDesc', value: null },
  { id: 'low', labelKey: 'personalization.effortLow', descKey: 'personalization.effortLowDesc', value: 'low' },
  { id: 'medium', labelKey: 'personalization.effortMedium', descKey: 'personalization.effortMediumDesc', value: 'medium' },
  { id: 'high', labelKey: 'personalization.effortHigh', descKey: 'personalization.effortHighDesc', value: 'high' },
  { id: 'xhigh', labelKey: 'personalization.effortXhigh', descKey: 'personalization.effortXhighDesc', value: 'xhigh' },
  { id: 'max', labelKey: 'personalization.effortMax', descKey: 'personalization.effortMaxDesc', value: 'max' }
];

const permissionModeOptions: Array<{
  id: PermissionModeValue;
  labelKey: string;
  descKey: string;
}> = [
  { id: 'readonly', labelKey: 'personalization.permissionReadonly', descKey: 'personalization.permissionReadonlyDesc' },
  { id: 'approval', labelKey: 'personalization.permissionApproval', descKey: 'personalization.permissionApprovalDesc' },
  {
    id: 'auto_approval',
    labelKey: 'personalization.permissionAutoApproval',
    descKey: 'personalization.permissionAutoApprovalDesc'
  },
  { id: 'unrestricted', labelKey: 'personalization.permissionUnrestricted', descKey: 'personalization.permissionUnrestrictedDesc' }
];

type WorkModeValue = 'plan' | 'ask' | 'execute';
const workModeOptions: Array<{
  id: WorkModeValue;
  labelKey: string;
  descKey: string;
}> = [
  { id: 'plan', labelKey: 'personalization.workModePlan', descKey: 'personalization.workModePlanDesc' },
  { id: 'ask', labelKey: 'personalization.workModeAsk', descKey: 'personalization.workModeAskDesc' },
  { id: 'execute', labelKey: 'personalization.workModeExecute', descKey: 'personalization.workModeExecuteDesc' }
];

const policyStore = usePolicyStore();
const modelStore = useModelStore();

const filteredModelOptions = computed(() => {
  void currentLocale.value;
  return (modelStore.models || []).map((opt: any) => {
    const multimodal = String(opt.multimodal || 'none');
    return {
      id: opt.key,
      value: opt.key,
      label: opt.label,
      desc: opt.description || '',
      badge: multimodal === 'image,video' ? t('personalization.badgeTextOnly') : opt.thinkingOnly ? t('personalization.badgeThinking') : undefined,
      thinkingOnly: !!opt.thinkingOnly,
      fastOnly: !!opt.fastOnly,
      disabled: policyStore.disabledModelSet.has(opt.key)
    };
  });
});

const imageCompressionOptions = [
  { id: 'original', labelKey: 'personalization.imageOriginal', descKey: 'personalization.imageOriginalDesc' },
  { id: '1080p', labelKey: 'personalization.image1080p', descKey: 'personalization.image1080pDesc' },
  { id: '720p', labelKey: 'personalization.image720p', descKey: 'personalization.image720pDesc' },
  { id: '540p', labelKey: 'personalization.image540p', descKey: 'personalization.image540pDesc' }
] as const;

const defaultModelLabel = computed(() => {
  void currentLocale.value;
  return (
    filteredModelOptions.value.find((option: any) => option.value === form.value.default_model)
      ?.label || t('common.unset')
  );
});

const runModeLabel = computed(() => {
  void currentLocale.value;
  const found = runModeOptions.find((option) => isRunModeActive(option.value));
  return found ? t(found.labelKey) : t('common.unset');
});

const isEffortActive = (value: ReasoningEffortValue) => {
  if (value === null) {
    return !form.value.default_reasoning_effort;
  }
  return form.value.default_reasoning_effort === value;
};

const reasoningEffortLabel = computed(() => {
  void currentLocale.value;
  const found = reasoningEffortOptions.find((option) => isEffortActive(option.value));
  return found ? t(found.labelKey) : t('personalization.effortDefault');
});

const selectDefaultReasoningEffort = (value: ReasoningEffortValue) => {
  personalization.setDefaultReasoningEffort(value);
  closeDropdown();
};

const permissionModeLabel = computed(() => {
  void currentLocale.value;
  const found = permissionModeOptions.find((option) => option.id === form.value.default_permission_mode);
  return found ? t(found.labelKey) : t('common.unset');
});

const workModeLabel = computed(() => {
  void currentLocale.value;
  const found = workModeOptions.find((option) => option.id === form.value.default_work_mode);
  return found ? t(found.labelKey) : t('common.unset');
});

const versioningBackupModeLabel = computed(() => {
  void currentLocale.value;
  if (form.value.versioning_backup_mode === 'full') return t('personalization.fullBackup');
  return t('personalization.shallowBackup');
});

const imageCompressionLabel = computed(() => {
  void currentLocale.value;
  const found = imageCompressionOptions.find((option) => option.id === form.value.image_compression);
  return found ? t(found.labelKey) : t('common.unset');
});

const communicationStyleLabel = computed(() => {
  void currentLocale.value;
  if (form.value.communication_style === 'human_like') return t('personalization.communicationHumanLike');
  if (form.value.communication_style === 'auto') return t('personalization.communicationAuto');
  return t('personalization.communicationDefault');
});

const conversationContinuityLabel = computed(() => {
  void currentLocale.value;
  if (form.value.conversation_continuity === 'high') return t('personalization.continuityHigh');
  if (form.value.conversation_continuity === 'low') return t('personalization.continuityLow');
  return t('personalization.continuityMedium');
});

const currentBlockDisplayMode = computed(() => experiments.value.blockDisplayMode);

const stackedHideBorders = computed(() => form.value.stacked_hide_borders);
const minimalExpandHeightLimited = computed(() => form.value.minimal_expand_height_limited);

const blockDisplayLabel = computed(() => {
  void currentLocale.value;
  const found = blockDisplayOptions.find((option) => option.value === currentBlockDisplayMode.value);
  return found ? t(found.labelKey) : t('personalization.blockDisplayStacked');
});

const currentCompactMessageDisplay = computed(() => form.value.compact_message_display || 'full');

const compactMessageDisplayLabel = computed(() => {
  void currentLocale.value;
  const found = compactMessageDisplayOptions.find(
    (option) => option.value === currentCompactMessageDisplay.value
  );
  return found ? t(found.labelKey) : t('personalization.compactMessageFull');
});

const usageSummary = ref({
  total_input_tokens: 0,
  total_output_tokens: 0,
  total_conversations: 0,
  total_user_messages: 0,
  total_tools: 0
});
const usageLoading = ref(false);
const usageError = ref('');
const usageUpdatedAt = ref<string | null>(null);

const usageUpdatedText = computed(() => {
  void currentLocale.value;
  if (!usageUpdatedAt.value) {
    return t('personalization.usageNeverRefreshed');
  }
  const date = new Date(usageUpdatedAt.value);
  if (Number.isNaN(date.getTime())) {
    return t('personalization.usageUnknownTime');
  }
  return date.toLocaleString(currentLocale.value === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
});

const appCurrentVersionCode = ref<number | null>(null);
const appCurrentVersionName = ref<string>('');
const appUpdateChecking = ref(false);
const appUpdateError = ref('');
const appUpdateCheckedAt = ref<string | null>(null);
const appUpdateInfo = ref<{
  latestVersionCode: number;
  latestVersionName: string;
  apkUrl: string;
  fileSizeBytes: number;
  changelog?: string;
  publishedAt?: string | null;
  hasUpdate?: boolean | null;
} | null>(null);

if (typeof window !== 'undefined') {
  const params = new URLSearchParams(window.location.search);
  const vcRaw = params.get('app_vc') || '';
  const vnRaw = params.get('app_vn') || '';
  const bridge = (window as any)?.AndroidThemeBridge;
  const bridgeVcRaw =
    bridge && typeof bridge.getAppVersionCode === 'function'
      ? String(bridge.getAppVersionCode() || '')
      : '';
  const bridgeVnRaw =
    bridge && typeof bridge.getAppVersionName === 'function'
      ? String(bridge.getAppVersionName() || '')
      : '';
  // App 端优先使用 Bridge 实时版本，避免 WebView 恢复旧 URL 参数导致显示过期版本号
  const finalVcRaw = bridgeVcRaw || vcRaw;
  const finalVnRaw = bridgeVnRaw || vnRaw;
  appCurrentVersionCode.value = /^\d+$/.test(finalVcRaw) ? Number(finalVcRaw) : null;
  appCurrentVersionName.value = finalVnRaw || '';
}

const hydrateAppVersionFromBridge = () => {
  if (typeof window === 'undefined') return;
  const bridge = (window as any)?.AndroidThemeBridge;
  if (!bridge) return;
  try {
    const vcRaw =
      typeof bridge.getAppVersionCode === 'function'
        ? String(bridge.getAppVersionCode() || '')
        : '';
    const vnRaw =
      typeof bridge.getAppVersionName === 'function'
        ? String(bridge.getAppVersionName() || '')
        : '';
    if (vcRaw && /^\d+$/.test(vcRaw)) {
      appCurrentVersionCode.value = Number(vcRaw);
    }
    if (vnRaw) {
      appCurrentVersionName.value = vnRaw;
    }
  } catch {
    // ignore bridge read failures
  }
};

const appCurrentVersionText = computed(() => {
  void currentLocale.value;
  return appCurrentVersionName.value || t('personalization.appVersionUnknown');
});

const appHasUpdate = computed(() => {
  if (!appUpdateInfo.value) return false;
  if (typeof appUpdateInfo.value.hasUpdate === 'boolean') return appUpdateInfo.value.hasUpdate;
  if (appCurrentVersionCode.value == null) return false;
  return Number(appUpdateInfo.value.latestVersionCode || 0) > appCurrentVersionCode.value;
});

const appDownloadUrl = computed(() => appUpdateInfo.value?.apkUrl || '');

const appUpdateStateText = computed(() => {
  void currentLocale.value;
  if (!appUpdateInfo.value) return t('personalization.appUpdateNotChecked');
  return appHasUpdate.value ? t('personalization.appUpdateFound') : t('personalization.appUpdateLatest');
});

const appUpdateCheckedText = computed(() => {
  void currentLocale.value;
  if (!appUpdateCheckedAt.value) return t('personalization.appUpdateNeverChecked');
  const d = new Date(appUpdateCheckedAt.value);
  if (Number.isNaN(d.getTime())) return t('personalization.appUpdateJustChecked');
  const formatted = d.toLocaleString(currentLocale.value === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
  return t('personalization.appUpdateCheckedAt', { time: formatted });
});

const fetchUsageSummary = async () => {
  if (usageLoading.value) {
    return;
  }
  usageLoading.value = true;
  usageError.value = '';
  try {
    const response = await fetch('/api/conversations/statistics');
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || payload.message || t('personalization.usageFetchFailed'));
    }
    const data = payload.data || {};
    const tokenStats = data.token_statistics || {};
    usageSummary.value = {
      total_input_tokens: Number(tokenStats.total_input_tokens || 0),
      total_output_tokens: Number(tokenStats.total_output_tokens || 0),
      total_conversations: Number(data.total_conversations || 0),
      total_user_messages: Number(data.total_user_messages || 0),
      total_tools: Number(data.total_tools || 0)
    };
    usageUpdatedAt.value = new Date().toISOString();
  } catch (error: any) {
    usageError.value = error?.message || t('personalization.usageFetchFailed');
  } finally {
    usageLoading.value = false;
  }
};

const checkAppUpdate = async () => {
  if (appUpdateChecking.value) return;
  hydrateAppVersionFromBridge();
  appUpdateChecking.value = true;
  appUpdateError.value = '';
  try {
    const params = new URLSearchParams();
    if (appCurrentVersionCode.value != null) {
      params.set('currentVersionCode', String(appCurrentVersionCode.value));
    }
    const suffix = params.toString() ? `?${params.toString()}` : '';
    const resp = await fetch(`/api/app/version${suffix}`);
    const payload = await resp.json();
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || t('personalization.appUpdateFailed'));
    }
    const data = payload?.data || {};
    appUpdateInfo.value = {
      latestVersionCode: Number(data.latestVersionCode || 0),
      latestVersionName: String(data.latestVersionName || ''),
      apkUrl: String(data.apkUrl || ''),
      fileSizeBytes: Number(data.fileSizeBytes || 0),
      changelog: String(data.changelog || ''),
      publishedAt: data.publishedAt || null,
      hasUpdate: typeof data.hasUpdate === 'boolean' ? data.hasUpdate : null
    };
    appUpdateCheckedAt.value = new Date().toISOString();
  } catch (error: any) {
    appUpdateError.value = error?.message || t('personalization.appUpdateFailed');
  } finally {
    appUpdateChecking.value = false;
  }
};

const downloadLatestApp = () => {
  const url = appDownloadUrl.value;
  if (!url) {
    appUpdateError.value = t('personalization.appUpdateNoUrl');
    return;
  }
  window.location.href = url;
};

onMounted(async () => {
  hydrateAppVersionFromBridge();
  try {
    const resp = await fetch('/api/session-status', { credentials: 'same-origin' });
    if (!resp.ok) return;
    const payload = await resp.json();
    const snapshot = payload?.session || {};
    sessionRole.value = String(snapshot.role || '');
    sessionHostMode.value = !!snapshot.host_mode;
  } catch (_err) {
    sessionRole.value = '';
  }
});

watch(
  () => [activeTab.value, visible.value],
  ([tab, isVisible]) => {
    if (isVisible && tab === 'data') {
      fetchUsageSummary();
    }
    if (isVisible && tab === 'sub-agents') {
      loadSubAgentRoles();
      loadSubAgentSettings();
      loadSubAgentModels();
    }
    if (isVisible && tab === 'review-agents') {
      loadSubAgentModels();
    }
    if (isVisible && tab === 'general') {
      // 「标题生成模型」菜单复用子智能体模型库，需在常规页签也加载模型列表
      loadSubAgentModels();
      if (
        isAppShell.value &&
        !appUpdateInfo.value &&
        !appUpdateChecking.value
      ) {
        checkAppUpdate();
      }
    }
  }
);

// ----- 子智能体管理 -----
const subAgentRoles = ref<any[]>([]);
const subAgentRolesLoading = ref(false);
const subAgentCompressThreshold = ref(150000);
/** 子智能体最大执行轮次（仅传统后台子智能体；多智能体成员不受限）：null/'' = 默认 50；0 = 无上限；正整数 = 该值 */
const subAgentMaxTurns = ref<number | null>(null);
const subAgentSettingsSaving = ref(false);
const subAgentModels = ref<any[]>([]);
const roleEditorOpen = ref(false);
const editingRole = ref<any>(null);
const roleSaving = ref(false);
const roleForm = ref({
  role_id: '',
  name: '',
  description: '',
  body_prompt: '',
  thinking_mode: 'fast',
  model_key: ''
});

const loadSubAgentRoles = async () => {
  subAgentRolesLoading.value = true;
  try {
    const resp = await fetch('/api/multiagent/roles', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentRoles.value = data.roles || [];
    }
  } catch (e) {
    // 静默处理
  } finally {
    subAgentRolesLoading.value = false;
  }
};

const loadSubAgentModels = async () => {
  try {
    const resp = await fetch('/api/multiagent/models', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success) {
      subAgentModels.value = data.models || [];
    }
  } catch (e) {
    // 静默处理
  }
};

// ----- 审核智能体（模型与运行参数统一配置，模型库复用子智能体模型列表） -----
const reviewAgentDefs: Array<{ key: ReviewAgentKey; nameKey: string; descKey: string }> = [
  { key: 'auto_approval', nameKey: 'personalization.reviewAgentAutoApproval', descKey: 'personalization.reviewAgentAutoApprovalDesc' },
  { key: 'goal_review', nameKey: 'personalization.reviewAgentGoalReview', descKey: 'personalization.reviewAgentGoalReviewDesc' },
  { key: 'workflow_review', nameKey: 'personalization.reviewAgentWorkflowReview', descKey: 'personalization.reviewAgentWorkflowReviewDesc' }
];

const reviewAgentOf = (key: ReviewAgentKey): ReviewAgentSetting => {
  const agents = (form.value as any).review_agents;
  return agents?.[key] || { model: '', thinking: false, timeout_seconds: 60, max_rounds: 3, max_command_timeout: 60 };
};

const updateReviewAgent = (key: ReviewAgentKey, patch: Partial<ReviewAgentSetting>) => {
  const agents = { ...((form.value as any).review_agents || {}) };
  agents[key] = { ...reviewAgentOf(key), ...patch };
  personalization.updateField({ key: 'review_agents', value: agents });
};

const updateReviewAgentInt = (key: ReviewAgentKey, field: 'timeout_seconds' | 'max_rounds' | 'max_command_timeout', raw: string, lo: number, hi: number) => {
  const parsed = parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return;
  updateReviewAgent(key, { [field]: Math.max(lo, Math.min(parsed, hi)) });
};

const loadSubAgentSettings = async () => {
  try {
    const resp = await fetch('/api/multiagent/settings', { credentials: 'same-origin' });
    const data = await resp.json();
    if (data.success && data.settings) {
      subAgentCompressThreshold.value = data.settings.sub_agent_compress_threshold_tokens || 150000;
      // 未设置时为 null，输入框留空（placeholder 提示默认 50）
      subAgentMaxTurns.value = data.settings.sub_agent_max_turns ?? null;
    }
  } catch (e) {
    // 静默处理
  }
};

const saveSubAgentSettings = async () => {
  subAgentSettingsSaving.value = true;
  try {
    await fetch('/api/multiagent/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        settings: {
          sub_agent_compress_threshold_tokens: subAgentCompressThreshold.value,
          // 留空（null/''）提交 null → 服务端删除该键恢复默认 50；0 → 无上限
          sub_agent_max_turns:
            subAgentMaxTurns.value === null || (subAgentMaxTurns.value as unknown) === ''
              ? null
              : Number(subAgentMaxTurns.value)
        }
      })
    });
  } finally {
    subAgentSettingsSaving.value = false;
  }
};

const openRoleEditor = (role?: any) => {
  if (role) {
    editingRole.value = role;
    roleForm.value = {
      role_id: role.role_id || '',
      name: role.name || '',
      description: role.description || '',
      body_prompt: role.body_prompt || '',
      thinking_mode: role.thinking_mode || 'fast',
      model_key: role.model_key || ''
    };
  } else {
    editingRole.value = null;
    roleForm.value = { role_id: '', name: '', description: '', body_prompt: '', thinking_mode: 'fast', model_key: '' };
  }
  closeDropdown();
  roleEditorOpen.value = true;
};

const closeRoleEditor = () => {
  roleEditorOpen.value = false;
  editingRole.value = null;
  closeDropdown();
};

// 角色编辑弹窗遮罩：按下与松开都发生在遮罩上才视为「点击遮罩关闭」。
// 修复：在输入框内拖选文字、松开落在遮罩上时，click 事件 target 为遮罩，
// 会被 @click.self 误判为点击遮罩，导致整个编辑器关闭、表单内容丢失。
const roleEditorOverlayPressActive = ref(false);

const handleRoleEditorOverlayPressStart = (event: MouseEvent) => {
  roleEditorOverlayPressActive.value =
    event.target === event.currentTarget && event.button === 0;
};

const handleRoleEditorOverlayPressEnd = (event: MouseEvent) => {
  const shouldClose =
    roleEditorOverlayPressActive.value && event.target === event.currentTarget;
  roleEditorOverlayPressActive.value = false;
  if (shouldClose) {
    closeRoleEditor();
  }
};

const saveRole = async () => {
  roleSaving.value = true;
  try {
    const isEdit = !!editingRole.value;
    const url = isEdit
      ? `/api/multiagent/roles/${roleForm.value.role_id}`
      : '/api/multiagent/roles';
    const method = isEdit ? 'PUT' : 'POST';
    const resp = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        ...roleForm.value,
        model_key: roleForm.value.model_key || null
      })
    });
    const data = await resp.json();
    if (data.success) {
      closeRoleEditor();
      await loadSubAgentRoles();
    }
  } finally {
    roleSaving.value = false;
  }
};

const deleteRole = async (role: any) => {
  if (!confirm(t('personalization.deleteRoleConfirm', { name: role.name }))) return;
  try {
    const resp = await fetch(`/api/multiagent/roles/${role.role_id}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    const data = await resp.json();
    if (data.success) {
      await loadSubAgentRoles();
    }
  } catch (e) {
    // 静默处理
  }
};

// ---- 窄屏（≤760px）iOS 设置式「列表 → 子页」导航 ----
const mobileInSubPage = ref(false);
const isMobileLayout = () =>
  typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches;

const setActiveTab = (tab: PersonalTab) => {
  activeTab.value = tab;
  closeDropdown();
  if (isMobileLayout()) {
    mobileInSubPage.value = true;
  }
};

const backToNavList = () => {
  mobileInSubPage.value = false;
};

// 抽屉重新打开时始终回到导航列表，避免上次停留的子页状态残留
watch(visible, (next) => {
  if (next) mobileInSubPage.value = false;
});

const startTutorial = () => {
  fetch('/api/tutorial-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ tutorial_completed: true })
  }).catch(() => {});
  tutorialStore.markCompleted();
  personalization.closeDrawer();
  window.setTimeout(() => {
    tutorialStore.startTutorial();
  }, 320);
};

const isRunModeActive = (value: RunModeValue) => {
  if (value === null) {
    return !form.value.default_run_mode;
  }
  return form.value.default_run_mode === value;
};

const setDefaultRunMode = (value: RunModeValue) => {
  if (checkModeModelConflict(value, form.value.default_model)) {
    return;
  }
  personalization.setDefaultRunMode(value);
};

const setDefaultModel = (value: string) => {
  if (policyStore.disabledModelSet.has(value)) {
    uiStore.pushToast({
      title: t('personalization.modelDisabledTitle'),
      message: t('personalization.modelDisabledMessage'),
      type: 'warning'
    });
    return;
  }
  if (checkModeModelConflict(form.value.default_run_mode, value)) {
    return;
  }
  personalization.setDefaultModel(value);
};

const setDefaultPermissionMode = (value: PermissionModeValue) => {
  personalization.setDefaultPermissionMode(value);
};

const setCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  personalization.setCommunicationStyle(value);
};

const setConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  personalization.setConversationContinuity(value);
};

const selectDefaultModel = (value: string) => {
  setDefaultModel(value);
  closeDropdown();
};

const selectDefaultRunMode = (value: RunModeValue) => {
  setDefaultRunMode(value);
  closeDropdown();
};

const selectDefaultPermissionMode = (value: PermissionModeValue) => {
  setDefaultPermissionMode(value);
  closeDropdown();
};

const selectDefaultWorkMode = (value: WorkModeValue) => {
  personalization.setDefaultWorkMode(value);
  closeDropdown();
};

const selectVersioningBackupMode = (value: 'shallow' | 'full') => {
  personalization.setVersioningBackupMode(value);
  closeDropdown();
};

const selectCommunicationStyle = (value: 'default' | 'human_like' | 'auto') => {
  setCommunicationStyle(value);
  closeDropdown();
};

const selectConversationContinuity = (value: 'low' | 'medium' | 'high') => {
  setConversationContinuity(value);
  closeDropdown();
};

const selectImageCompression = (value: (typeof imageCompressionOptions)[number]['id']) => {
  personalization.setImageCompression(value);
  closeDropdown();
};

const selectBlockDisplayMode = (mode: 'traditional' | 'stacked' | 'minimal') => {
  handleBlockDisplayModeChange(mode);
  closeDropdown();
};

const checkModeModelConflict = (mode: RunModeValue, model: string | null): boolean => {
  const found = (filteredModelOptions.value || []).find((item: any) => item.value === model);
  const warnings: string[] = [];
  if (found?.thinkingOnly && mode && mode !== 'thinking') {
    warnings.push(t('personalization.modelThinkingOnlyWarning', { label: found.label }));
  }
  if (found?.fastOnly && mode && mode !== 'fast') {
    warnings.push(t('personalization.modelFastOnlyWarning', { label: found.label }));
  }
  if (warnings.length) {
    uiStore.pushToast({
      title: t('personalization.modelModeConflictTitle'),
      message: warnings.join(' '),
      type: 'warning',
      duration: 6000
    });
    return true;
  }
  return false;
};

const handleRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'recent_conversations_prompt_limit',
    value: target.value
  });
};

const commitRecentConversationsPromptLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.setRecentConversationsPromptLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setRecentConversationsPromptLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreRecentConversationsPromptLimit = () => {
  personalization.setRecentConversationsPromptLimit(null);
};

const handleProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target) {
    return;
  }
  personalization.updateField({
    key: 'project_memory_inject_limit',
    value: target.value
  });
};

const commitProjectMemoryInjectLimitInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    // 留空 = 无上限
    personalization.setProjectMemoryInjectLimit(null);
    return;
  }
  const parsed = Number(target.value);
  personalization.setProjectMemoryInjectLimit(Number.isNaN(parsed) ? null : parsed);
};

const restoreProjectMemoryInjectLimit = () => {
  personalization.restoreProjectMemoryInjectLimit();
};

const handleCompressionNumberInput = (key: CompressionField, event: Event) => {
  const target = event.target as HTMLInputElement | null;
  if (!target || !target.value) {
    personalization.updateField({ key, value: null });
    return;
  }
  const parsed = Number(target.value);
  if (!Number.isFinite(parsed)) {
    personalization.updateField({ key, value: null });
    return;
  }
  personalization.updateField({ key, value: Math.round(parsed) });
};

const restoreCompressionDefaults = () => {
  personalization.updateField({ key: 'shallow_compress_trigger_tokens', value: null });
  personalization.updateField({ key: 'shallow_compress_keep_recent_tools', value: null });
  personalization.updateField({ key: 'shallow_compress_max_replace_per_round', value: null });
  personalization.updateField({ key: 'shallow_compress_trigger_tool_calls_interval', value: null });
  personalization.updateField({ key: 'deep_compress_trigger_tokens', value: null });
};

const toggleCategory = (categoryId: string) => {
  personalization.toggleDefaultToolCategory(categoryId);
};

const blockDisplayOptions = [
  {
    id: 'traditional',
    labelKey: 'personalization.blockDisplayTraditional',
    descKey: 'personalization.blockDisplayTraditionalDesc',
    value: 'traditional' as const
  },
  {
    id: 'stacked',
    labelKey: 'personalization.blockDisplayStacked',
    descKey: 'personalization.blockDisplayStackedDesc',
    value: 'stacked' as const,
    badgeKey: 'personalization.badgeRecommended'
  },
  {
    id: 'minimal',
    labelKey: 'personalization.blockDisplayMinimal',
    descKey: 'personalization.blockDisplayMinimalDesc',
    value: 'minimal' as const,
    badgeKey: 'personalization.badgeNew'
  }
];

const handleBlockDisplayModeChange = (mode: 'traditional' | 'stacked' | 'minimal') => {
  personalization.setBlockDisplayMode(mode);
};

const handleStackedHideBordersChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'stacked_hide_borders',
    value: target.checked
  });
};

const handleMinimalExpandHeightLimitedChange = (event: Event) => {
  const target = event.target as HTMLInputElement;
  personalization.updateField({
    key: 'minimal_expand_height_limited',
    value: target.checked
  });
};

const compactMessageDisplayOptions = [
  {
    id: 'full',
    labelKey: 'personalization.compactMessageFull',
    descKey: 'personalization.compactMessageFullDesc',
    value: 'full' as const
  },
  {
    id: 'brief',
    labelKey: 'personalization.compactMessageBrief',
    descKey: 'personalization.compactMessageBriefDesc',
    value: 'brief' as const
  }
];

const selectCompactMessageDisplay = (mode: 'full' | 'brief') => {
  personalization.setCompactMessageDisplay(mode);
  closeDropdown();
};

const openAdminPanel = () => {
  window.open('/admin/monitor', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openCustomTools = () => {
  window.open('/admin/custom-tools', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openApiAdmin = () => {
  window.open('/admin/api', '_blank', 'noopener');
  personalization.closeDrawer();
};

const openMcpConfig = () => {
  window.open('/admin/policy', '_blank', 'noopener');
  personalization.closeDrawer();
};

// ===== 主题切换 =====
const { setTheme, loadTheme } = useTheme();
const themeOptions: Array<{ id: ThemeKey; labelKey: string; descKey: string; swatches: string[] }> = [
  {
    id: 'classic',
    labelKey: 'personalization.themeClassic',
    descKey: 'personalization.themeClassicDesc',
    swatches: ['#eeece2', '#f7f3ea', '#da7756']
  },
  {
    id: 'light',
    labelKey: 'personalization.themeLight',
    descKey: 'personalization.themeLightDesc',
    swatches: ['#ffffff', '#f7f7f8', '#6b7280']
  },
  {
    id: 'dark',
    labelKey: 'personalization.themeDark',
    descKey: 'personalization.themeDarkDesc',
    swatches: ['#1a1a1a', '#2a2a2a', '#3a3a3a']
  }
];

const activeTheme = ref<ThemeKey>(loadTheme());

const themeLabel = computed(() => {
  void currentLocale.value;
  const found = themeOptions.find((option) => option.id === activeTheme.value);
  return found ? t(found.labelKey) : t('personalization.themeClassic');
});

// 监听store中的theme变化，同步到activeTheme（用于从后端加载主题后更新UI）
watch(
  () => personalization.form.theme,
  (newTheme) => {
    if (newTheme && newTheme !== activeTheme.value) {
      activeTheme.value = newTheme as ThemeKey;
      setTheme(newTheme as ThemeKey);
    }
  },
  { immediate: true }
);

const selectThemeOption = (theme: ThemeKey) => {
  applyThemeOption(theme);
  closeDropdown();
};

// ===== 界面语言切换 =====
const { locale, setLocale } = useLocale();
const localeOptions: Array<{ id: LocaleKey; labelKey: string; descKey: string }> = [
  { id: 'zh-CN', labelKey: 'personalization.localeZhCN', descKey: 'personalization.localeChineseDesc' },
  { id: 'en-US', labelKey: 'personalization.localeEnglish', descKey: 'personalization.localeEnglishDesc' }
];

const localeLabel = computed(() => {
  void currentLocale.value;
  const found = localeOptions.find((option) => option.id === locale.value);
  return found ? t(found.labelKey) : t('personalization.localeZhCN');
});

const selectLocaleOption = (nextLocale: LocaleKey) => {
  setLocale(nextLocale);
  // 同步到后端个人偏好（personalization.json ui_locale）：后端用户可见消息语言随动
  personalization.updateField({ key: 'ui_locale', value: nextLocale });
  closeDropdown();
};

const applyThemeOption = async (theme: ThemeKey) => {
  activeTheme.value = theme;
  setTheme(theme);

  // 同步更新到store并保存到后端配置文件
  personalization.updateField({ key: 'theme', value: theme });

  try {
    const resp = await fetch('/api/personalization', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ theme })
    });
    const result = await resp.json();
    if (!resp.ok || !result.success) {
      console.warn('保存主题到后端失败:', result.error);
    }
  } catch (error) {
    console.warn('保存主题到后端失败:', error);
  }
};

// ──── 向拆分出的 tab 子组件（tabs/*.vue）提供共享上下文 ────
// 子组件 inject('personalizationDrawer') 后按同名解构，模板绑定与原单文件保持一致。
const drawerContext = {
    activeDropdown, activeTab, activeTheme, appCurrentVersionText, appHasUpdate,
    appUpdateCheckedText, appUpdateChecking, appUpdateStateText, blockDisplayLabel, blockDisplayOptions,
    checkAppUpdate, closeDropdown, closeRoleEditor, commitProjectMemoryInjectLimitInput,
    commitRecentConversationsPromptLimitInput, communicationStyleLabel, compactMessageDisplayLabel,
    compactMessageDisplayOptions, conversationContinuityLabel, currentBlockDisplayMode,
    currentCompactMessageDisplay, defaultModelLabel, deleteRole, deleteVoiceModel, downloadLatestApp,
    downloadVoiceModel, editingRole, fetchUsageSummary, filteredModelOptions, floatingMenuStyle, form,
    formatTokenCount, goalTokenLimitEnabled, handleCompressionNumberInput,
    handleMinimalExpandHeightLimitedChange, handleProjectMemoryInjectLimitInput,
    handleRecentConversationsPromptLimitInput, handleRoleEditorOverlayPressEnd,
    handleRoleEditorOverlayPressStart, handleStackedHideBordersChange, imageCompressionLabel,
    imageCompressionOptions, isAppShell, isEffortActive, isRunModeActive, loading, locale, localeLabel,
    localeOptions, minimalExpandHeightLimited, openAdminPanel, openApiAdmin, openCustomTools, openMcpConfig,
    openRoleEditor, permissionModeLabel, personalization, projectMemoryInjectLimitMin, reasoningEffortLabel,
    recentConversationsPromptLimitRange, restoreCompressionDefaults, restoreProjectMemoryInjectLimit,
    restoreRecentConversationsPromptLimit, reviewAgentDefs, reviewAgentOf, roleEditorOpen, roleForm,
    roleSaving, runModeLabel, saveRole, saveSubAgentSettings, saving, selectBlockDisplayMode,
    selectCommunicationStyle, selectCompactMessageDisplay, selectConversationContinuity, selectDefaultModel,
    selectDefaultPermissionMode, selectDefaultReasoningEffort, selectDefaultRunMode, selectDefaultWorkMode,
    selectImageCompression, selectLocaleOption, selectThemeOption, selectVersioningBackupMode,
    showMcpConfigEntry, skillsCatalog, stackedHideBorders, startTutorial, subAgentCompressThreshold,
    subAgentMaxTurns, subAgentModels, subAgentRoles, subAgentRolesLoading, subAgentSettingsSaving,
    themeLabel, themeOptions, toggleCategory, toggleDropdown, toggleGoalTokenLimit, toggleUpdating,
    tonePresets, toolCategories, updateReviewAgent, updateReviewAgentInt, usageError, usageLoading,
    usageSummary, usageUpdatedText, versioningBackupModeLabel, voiceDownloadMsg, voiceDownloadPercent,
    voiceDownloading, voiceModelPartial, voiceModelReady, workModeLabel,
    runModeOptions, reasoningEffortOptions, permissionModeOptions, workModeOptions,
    clampGoalMaxTurns, clampGoalMaxTokens
};
provide('personalizationDrawer', drawerContext);

</script>

