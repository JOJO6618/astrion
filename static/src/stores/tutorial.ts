import { defineStore } from 'pinia';
import { useModelStore } from './model';
import { t, currentLocale } from '@/locales';

export type TutorialPlacement = 'auto' | 'top' | 'right' | 'bottom' | 'left' | 'center';
export type TutorialStepMode = 'info' | 'must_click';
export type TutorialCondition =
  | 'model_supports_media'
  | 'is_app_shell'
  | 'is_mobile_viewport'
  | 'not_mobile_viewport';

export interface TutorialStep {
  id: string;
  title: string;
  description: string;
  target: string | null;
  mode: TutorialStepMode;
  autoClick?: boolean;
  autoOutsideClick?: boolean;
  placement?: TutorialPlacement;
  condition?: TutorialCondition;
}

// 内部步骤定义：文案只存 key（titleKey/descriptionKey），由 activeStep getter 经 t() 解析。
// 避免在模块顶层常量里调用 t() 固化语言（i18n_spec.md §3.2 响应式陷阱）；
// activeStep 读取 currentLocale 建立依赖，切换语言时自动重新解析。
interface TutorialStepDef {
  id: string;
  titleKey: string;
  descriptionKey: string;
  target: string | null;
  mode: TutorialStepMode;
  autoClick?: boolean;
  autoOutsideClick?: boolean;
  placement?: TutorialPlacement;
  condition?: TutorialCondition;
}

const STORAGE_COMPLETED_KEY = 'agents_tutorial_completed_v1';
const STORAGE_VERSION_KEY = 'agents_tutorial_version';
const TUTORIAL_VERSION = 'v1';

const TUTORIAL_STEP_DEFS: TutorialStepDef[] = [
  {
    id: 'welcome',
    titleKey: 'tutorial.welcomeTitle',
    descriptionKey: 'tutorial.welcomeDesc',
    target: null,
    mode: 'info',
    placement: 'center'
  },
  {
    id: 'sidebar-conversations-open',
    titleKey: 'tutorial.sidebarConversationsOpenTitle',
    descriptionKey: 'tutorial.sidebarConversationsOpenDesc',
    target: '[data-tutorial="conversation-menu"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-conversations-close',
    titleKey: 'tutorial.sidebarConversationsCloseTitle',
    descriptionKey: 'tutorial.sidebarConversationsCloseDesc',
    target: '[data-tutorial="conversation-collapse"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-new-chat',
    titleKey: 'tutorial.newChatTitle',
    descriptionKey: 'tutorial.sidebarNewChatDesc',
    target: '[data-tutorial="quick-new-conversation"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-workspace-toggle',
    titleKey: 'tutorial.sidebarWorkspaceToggleTitle',
    descriptionKey: 'tutorial.sidebarWorkspaceToggleDesc',
    target: '[data-tutorial="workspace-toggle"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-monitor-toggle',
    titleKey: 'tutorial.sidebarMonitorToggleTitle',
    descriptionKey: 'tutorial.sidebarMonitorToggleDesc',
    target: '[data-tutorial="monitor-toggle"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-panel-switch',
    titleKey: 'tutorial.workspacePanelSwitchTitle',
    descriptionKey: 'tutorial.workspacePanelSwitchDesc',
    target: '[data-tutorial="panel-menu-toggle"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-panel-options',
    titleKey: 'tutorial.workspacePanelOptionsTitle',
    descriptionKey: 'tutorial.workspacePanelOptionsDesc',
    target: '[data-tutorial="panel-menu"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-mode-indicator',
    titleKey: 'tutorial.workspaceModeIndicatorTitle',
    descriptionKey: 'tutorial.workspaceModeIndicatorDesc',
    target: '[data-tutorial="run-mode-indicator"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-connection-indicator',
    titleKey: 'tutorial.workspaceConnectionIndicatorTitle',
    descriptionKey: 'tutorial.workspaceConnectionIndicatorDesc',
    target: '[data-tutorial="connection-indicator"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-model-selector',
    titleKey: 'tutorial.headerModelSelectorTitle',
    descriptionKey: 'tutorial.headerModelSelectorDesc',
    target: '[data-tutorial="header-model-selector"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-model-options',
    titleKey: 'tutorial.modelListTitle',
    descriptionKey: 'tutorial.headerModelOptionsDesc',
    target: '[data-tutorial="header-model-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-runmode-options',
    titleKey: 'tutorial.headerRunmodeOptionsTitle',
    descriptionKey: 'tutorial.headerRunmodeOptionsDesc',
    target: '[data-tutorial="header-runmode-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-menu-close',
    titleKey: 'tutorial.headerMenuCloseTitle',
    descriptionKey: 'tutorial.headerMenuCloseDesc',
    target: '.model-mode-dropdown',
    mode: 'info',
    autoOutsideClick: true,
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-conversation',
    titleKey: 'tutorial.openMenuTitle',
    descriptionKey: 'tutorial.mobileMenuOpenConversationDesc',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-conversation',
    titleKey: 'tutorial.mobileMenuConversationTitle',
    descriptionKey: 'tutorial.mobileMenuConversationDesc',
    target: '[data-tutorial="mobile-menu-conversation"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-conversation-close',
    titleKey: 'tutorial.mobileConversationCloseTitle',
    descriptionKey: 'tutorial.mobileConversationCloseDesc',
    target: '[data-tutorial="conversation-collapse"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-workspace',
    titleKey: 'tutorial.openMenuAgainTitle',
    descriptionKey: 'tutorial.mobileMenuOpenWorkspaceDesc',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-workspace',
    titleKey: 'tutorial.mobileMenuWorkspaceTitle',
    descriptionKey: 'tutorial.mobileMenuWorkspaceDesc',
    target: '[data-tutorial="mobile-menu-workspace"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-panel-switch',
    titleKey: 'tutorial.workspacePanelSwitchTitle',
    descriptionKey: 'tutorial.mobileWorkspacePanelSwitchDesc',
    target: '[data-tutorial="panel-menu-toggle"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-panel-options',
    titleKey: 'tutorial.mobileWorkspacePanelOptionsTitle',
    descriptionKey: 'tutorial.mobileWorkspacePanelOptionsDesc',
    target: '[data-tutorial="panel-menu"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-close',
    titleKey: 'tutorial.mobileWorkspaceCloseTitle',
    descriptionKey: 'tutorial.mobileWorkspaceCloseDesc',
    target: '[data-tutorial="mobile-workspace-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-newchat',
    titleKey: 'tutorial.openMenuAgainTitle',
    descriptionKey: 'tutorial.mobileMenuOpenNewchatDesc',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-new-chat',
    titleKey: 'tutorial.newChatTitle',
    descriptionKey: 'tutorial.mobileMenuNewChatDesc',
    target: '[data-tutorial="mobile-menu-new-chat"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-selector-open',
    titleKey: 'tutorial.mobileModelSelectorOpenTitle',
    descriptionKey: 'tutorial.mobileModelSelectorOpenDesc',
    target: '[data-tutorial="header-model-selector-mobile"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-options',
    titleKey: 'tutorial.modelListTitle',
    descriptionKey: 'tutorial.mobileModelOptionsDesc',
    target: '[data-tutorial="header-model-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-runmode-options',
    titleKey: 'tutorial.mobileRunmodeOptionsTitle',
    descriptionKey: 'tutorial.mobileRunmodeOptionsDesc',
    target: '[data-tutorial="header-runmode-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-selector-close',
    titleKey: 'tutorial.mobileModelSelectorCloseTitle',
    descriptionKey: 'tutorial.mobileModelSelectorCloseDesc',
    target: '.model-mode-dropdown',
    mode: 'info',
    autoOutsideClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'open-quick-menu',
    titleKey: 'tutorial.openQuickMenuTitle',
    descriptionKey: 'tutorial.openQuickMenuDesc',
    target: '[data-tutorial="quick-menu-open"]',
    mode: 'info',
    autoClick: true,
    placement: 'top'
  },
  {
    id: 'quick-upload',
    titleKey: 'tutorial.quickUploadTitle',
    descriptionKey: 'tutorial.quickUploadDesc',
    target: '[data-tutorial="quick-upload"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-review',
    titleKey: 'tutorial.quickReviewTitle',
    descriptionKey: 'tutorial.quickReviewDesc',
    target: '[data-tutorial="quick-review"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-send-image',
    titleKey: 'tutorial.quickSendImageTitle',
    descriptionKey: 'tutorial.quickSendImageDesc',
    target: '[data-tutorial="quick-send-image"]',
    mode: 'info',
    placement: 'left',
    condition: 'model_supports_media'
  },
  {
    id: 'quick-send-video',
    titleKey: 'tutorial.quickSendVideoTitle',
    descriptionKey: 'tutorial.quickSendVideoDesc',
    target: '[data-tutorial="quick-send-video"]',
    mode: 'info',
    placement: 'left',
    condition: 'model_supports_media'
  },
  {
    id: 'quick-tool-disable',
    titleKey: 'tutorial.quickToolDisableTitle',
    descriptionKey: 'tutorial.quickToolDisableDesc',
    target: '[data-tutorial="quick-tool-menu"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-settings',
    titleKey: 'tutorial.quickSettingsTitle',
    descriptionKey: 'tutorial.quickSettingsDesc',
    target: '[data-tutorial="quick-settings-menu"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'open-settings-submenu',
    titleKey: 'tutorial.openSettingsSubmenuTitle',
    descriptionKey: 'tutorial.openSettingsSubmenuDesc',
    target: '[data-tutorial="quick-settings-menu"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'open-token-panel',
    titleKey: 'tutorial.openTokenPanelTitle',
    descriptionKey: 'tutorial.openTokenPanelDesc',
    target: '[data-tutorial="settings-token-panel"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'token-panel',
    titleKey: 'tutorial.tokenPanelTitle',
    descriptionKey: 'tutorial.tokenPanelDesc',
    target: '[data-tutorial="token-drawer"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'close-token-panel',
    titleKey: 'tutorial.closeTokenPanelTitle',
    descriptionKey: 'tutorial.closeTokenPanelDesc',
    target: '[data-tutorial="token-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'open-personal-space',
    titleKey: 'tutorial.openPersonalSpaceTitle',
    descriptionKey: 'tutorial.openPersonalSpaceDesc',
    target: '[data-tutorial="open-personal-space"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-personal',
    titleKey: 'tutorial.openMenuTitle',
    descriptionKey: 'tutorial.mobileMenuOpenPersonalDesc',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-personal',
    titleKey: 'tutorial.mobileMenuPersonalTitle',
    descriptionKey: 'tutorial.mobileMenuPersonalDesc',
    target: '[data-tutorial="mobile-menu-personal"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'personal-overview',
    titleKey: 'tutorial.personalOverviewTitle',
    descriptionKey: 'tutorial.personalOverviewDesc',
    target: '[data-tutorial="personal-card"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-preferences',
    titleKey: 'tutorial.tabPreferencesTitle',
    descriptionKey: 'tutorial.tabPreferencesDesc',
    target: '[data-tutorial="personal-tab-preferences"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-preferences',
    titleKey: 'tutorial.pagePreferencesTitle',
    descriptionKey: 'tutorial.pagePreferencesDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-model',
    titleKey: 'tutorial.tabModelTitle',
    descriptionKey: 'tutorial.tabModelDesc',
    target: '[data-tutorial="personal-tab-model"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-model',
    titleKey: 'tutorial.pageModelTitle',
    descriptionKey: 'tutorial.pageModelDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    // 用量统计已并入「常规」页签，锚点指向 general
    id: 'tab-usage',
    titleKey: 'tutorial.tabUsageTitle',
    descriptionKey: 'tutorial.tabUsageDesc',
    target: '[data-tutorial="personal-tab-general"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-usage',
    titleKey: 'tutorial.pageUsageTitle',
    descriptionKey: 'tutorial.pageUsageDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-app-update',
    titleKey: 'tutorial.tabAppUpdateTitle',
    descriptionKey: 'tutorial.tabAppUpdateDesc',
    target: '[data-tutorial="personal-tab-app-update"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_app_shell'
  },
  {
    id: 'page-app-update',
    titleKey: 'tutorial.pageAppUpdateTitle',
    descriptionKey: 'tutorial.pageAppUpdateDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right',
    condition: 'is_app_shell'
  },
  {
    id: 'tab-behavior',
    titleKey: 'tutorial.tabBehaviorTitle',
    descriptionKey: 'tutorial.tabBehaviorDesc',
    target: '[data-tutorial="personal-tab-behavior"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-behavior',
    titleKey: 'tutorial.pageBehaviorTitle',
    descriptionKey: 'tutorial.pageBehaviorDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-skills',
    titleKey: 'tutorial.tabSkillsTitle',
    descriptionKey: 'tutorial.tabSkillsDesc',
    target: '[data-tutorial="personal-tab-skills"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-skills',
    titleKey: 'tutorial.pageSkillsTitle',
    descriptionKey: 'tutorial.pageSkillsDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-image',
    titleKey: 'tutorial.tabImageTitle',
    descriptionKey: 'tutorial.tabImageDesc',
    target: '[data-tutorial="personal-tab-image"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-image',
    titleKey: 'tutorial.pageImageTitle',
    descriptionKey: 'tutorial.pageImageDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-theme',
    titleKey: 'tutorial.tabThemeTitle',
    descriptionKey: 'tutorial.tabThemeDesc',
    target: '[data-tutorial="personal-tab-theme"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-theme',
    titleKey: 'tutorial.pageThemeTitle',
    descriptionKey: 'tutorial.pageThemeDesc',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'close-personal-space',
    titleKey: 'tutorial.closePersonalSpaceTitle',
    descriptionKey: 'tutorial.closePersonalSpaceDesc',
    target: '[data-tutorial="personal-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom'
  },
  {
    id: 'done',
    titleKey: 'tutorial.doneTitle',
    descriptionKey: 'tutorial.doneDesc',
    target: null,
    mode: 'info',
    placement: 'center'
  }
];

// 把步骤定义解析为带本地化文案的步骤（调用时求值）
const resolveTutorialStep = (def: TutorialStepDef): TutorialStep => ({
  id: def.id,
  title: t(def.titleKey),
  description: t(def.descriptionKey),
  target: def.target,
  mode: def.mode,
  autoClick: def.autoClick,
  autoOutsideClick: def.autoOutsideClick,
  placement: def.placement,
  condition: def.condition
});

interface TutorialState {
  running: boolean;
  completed: boolean;
  currentIndex: number;
  steps: TutorialStepDef[];
  activeSelector: string | null;
}

const detectAppShell = () => {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.has('app_shell')) return true;
  return Boolean((window as any)?.AndroidThemeBridge);
};

const detectMobileViewport = () => {
  if (typeof window === 'undefined') return false;
  return window.innerWidth <= 768;
};

const shouldIncludeStepByCondition = (step: TutorialStepDef): boolean => {
  if (!step.condition) return true;
  if (step.condition === 'is_app_shell') {
    return detectAppShell();
  }
  if (step.condition === 'model_supports_media') {
    const modelStore = useModelStore();
    const current = modelStore.currentModel as any;
    return !!(current?.supportsImage || current?.supportsVideo);
  }
  if (step.condition === 'is_mobile_viewport') {
    return detectMobileViewport();
  }
  if (step.condition === 'not_mobile_viewport') {
    return !detectMobileViewport();
  }
  return true;
};

const readCompletedState = () => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return false;
  }
  try {
    const version = window.localStorage.getItem(STORAGE_VERSION_KEY);
    if (version !== TUTORIAL_VERSION) {
      return false;
    }
    return window.localStorage.getItem(STORAGE_COMPLETED_KEY) === '1';
  } catch {
    return false;
  }
};

const persistCompletedState = (completed: boolean) => {
  if (typeof window === 'undefined' || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_VERSION_KEY, TUTORIAL_VERSION);
    window.localStorage.setItem(STORAGE_COMPLETED_KEY, completed ? '1' : '0');
  } catch {
    // ignore
  }
};

export const useTutorialStore = defineStore('tutorial', {
  state: (): TutorialState => ({
    running: false,
    completed: readCompletedState(),
    currentIndex: 0,
    steps: TUTORIAL_STEP_DEFS,
    activeSelector: null
  }),
  getters: {
    activeStep(state): TutorialStep | null {
      const def = state.steps[state.currentIndex] || null;
      // 读取 currentLocale 建立语言依赖：切换语言时 getter 重新求值并解析文案（i18n_spec §3.2）
      void currentLocale.value;
      return def ? resolveTutorialStep(def) : null;
    },
    totalVisibleSteps(state): number {
      return state.steps.filter((step) => shouldIncludeStepByCondition(step)).length;
    },
    visibleStepIndex(state): number {
      if (!state.running) return 0;
      let count = 0;
      for (let i = 0; i <= state.currentIndex; i += 1) {
        const step = state.steps[i];
        if (step && shouldIncludeStepByCondition(step)) {
          count += 1;
        }
      }
      return count;
    }
  },
  actions: {
    shouldIncludeStep(step: TutorialStepDef): boolean {
      return shouldIncludeStepByCondition(step);
    },
    startTutorial() {
      this.running = true;
      this.completed = false;
      this.currentIndex = 0;
      this.activeSelector = null;
      persistCompletedState(false);
      this.skipHiddenSteps('forward');
    },
    exitTutorial() {
      this.running = false;
      this.activeSelector = null;
    },
    finishTutorial() {
      this.running = false;
      this.completed = true;
      this.activeSelector = null;
      persistCompletedState(true);
    },
    goNext() {
      if (!this.running) return;
      if (this.currentIndex >= this.steps.length - 1) {
        this.finishTutorial();
        return;
      }
      this.currentIndex += 1;
      this.skipHiddenSteps('forward');
      if (this.currentIndex >= this.steps.length - 1 && this.activeStep?.id === 'done') {
        this.completed = true;
      }
    },
    goPrev() {
      if (!this.running) return;
      if (this.currentIndex <= 0) return;
      this.currentIndex -= 1;
      this.skipHiddenSteps('backward');
    },
    skipCurrentStep() {
      this.goNext();
    },
    markCompleted() {
      this.completed = true;
      persistCompletedState(true);
    },
    skipHiddenSteps(direction: 'forward' | 'backward') {
      if (!this.running) return;
      if (direction === 'forward') {
        while (
          this.currentIndex < this.steps.length &&
          !this.shouldIncludeStep(this.steps[this.currentIndex])
        ) {
          this.currentIndex += 1;
        }
        if (this.currentIndex >= this.steps.length) {
          this.finishTutorial();
          return;
        }
      } else {
        while (this.currentIndex > 0 && !this.shouldIncludeStep(this.steps[this.currentIndex])) {
          this.currentIndex -= 1;
        }
      }
    },
    setActiveSelector(selector: string | null) {
      this.activeSelector = selector;
    }
  }
});