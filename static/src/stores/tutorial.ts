import { defineStore } from 'pinia';
import { useModelStore } from './model';

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

const STORAGE_COMPLETED_KEY = 'agents_tutorial_completed_v1';
const STORAGE_VERSION_KEY = 'agents_tutorial_version';
const TUTORIAL_VERSION = 'v1';

const DEFAULT_STEPS: TutorialStep[] = [
  {
    id: 'welcome',
    title: '欢迎使用 Astrion',
    description: '我们将用 2-3 分钟带你快速了解核心功能。',
    target: null,
    mode: 'info',
    placement: 'center'
  },
  {
    id: 'sidebar-conversations-open',
    title: '展开对话记录',
    description: '下一步会自动点击展开对话记录。',
    target: '[data-tutorial="conversation-menu"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-conversations-close',
    title: '折叠对话记录',
    description: '下一步会自动点击折叠按钮收起对话记录。',
    target: '[data-tutorial="conversation-collapse"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-new-chat',
    title: '新建对话',
    description: '下一步会自动新建并进入一个对话。',
    target: '[data-tutorial="quick-new-conversation"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-workspace-toggle',
    title: '工作区折叠',
    description: '显示/隐藏左侧工作区面板。',
    target: '[data-tutorial="workspace-toggle"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'sidebar-monitor-toggle',
    title: '虚拟显示器',
    description: '切换到虚拟显示器模式。',
    target: '[data-tutorial="monitor-toggle"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-panel-switch',
    title: '工作区面板切换',
    description: '下一步会自动展开面板切换菜单。',
    target: '[data-tutorial="panel-menu-toggle"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-panel-options',
    title: '四合一面板',
    description: '这里可以切换文件 / 待办 / 子智能体 / 后台指令。',
    target: '[data-tutorial="panel-menu"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-mode-indicator',
    title: '思考模式',
    description: '点击切换快速 / 思考。',
    target: '[data-tutorial="run-mode-indicator"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'workspace-connection-indicator',
    title: '连接状态指示灯',
    description: '绿色表示连接正常；红色表示与后端断开连接。',
    target: '[data-tutorial="connection-indicator"]',
    mode: 'info',
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-model-selector',
    title: '模型与模式选择',
    description: '下一步会自动展开模型与运行模式弹窗。',
    target: '[data-tutorial="header-model-selector"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-model-options',
    title: '模型列表',
    description: '这里是可用模型列表。',
    target: '[data-tutorial="header-model-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-runmode-options',
    title: '运行模式列表',
    description: '这里可切换快速 / 思考。',
    target: '[data-tutorial="header-runmode-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'header-menu-close',
    title: '关闭模型菜单',
    description: '下一步会自动点击空白区域关闭菜单。',
    target: '.model-mode-dropdown',
    mode: 'info',
    autoOutsideClick: true,
    placement: 'bottom',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-conversation',
    title: '打开菜单',
    description: '下一步会自动打开左上角菜单。',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-conversation',
    title: '对话记录',
    description: '下一步会自动进入对话记录。',
    target: '[data-tutorial="mobile-menu-conversation"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-conversation-close',
    title: '关闭对话记录',
    description: '下一步会自动关闭对话记录面板。',
    target: '[data-tutorial="conversation-collapse"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-workspace',
    title: '再次打开菜单',
    description: '下一步会自动打开菜单。',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-workspace',
    title: '工作文件',
    description: '下一步会自动进入工作文件。',
    target: '[data-tutorial="mobile-menu-workspace"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-panel-switch',
    title: '工作区面板切换',
    description: '下一步会自动展开切换弹窗。',
    target: '[data-tutorial="panel-menu-toggle"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-panel-options',
    title: '切换弹窗选项',
    description: '这里可切换文件 / 待办 / 子智能体 / 后台指令。',
    target: '[data-tutorial="panel-menu"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-workspace-close',
    title: '关闭工作文件',
    description: '下一步会自动关闭工作文件面板。',
    target: '[data-tutorial="mobile-workspace-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-newchat',
    title: '再次打开菜单',
    description: '下一步会自动打开菜单。',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-new-chat',
    title: '新建对话',
    description: '下一步会自动新建对话。',
    target: '[data-tutorial="mobile-menu-new-chat"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-selector-open',
    title: '模型与思考模式',
    description: '下一步会自动展开手机端模型/思考模式选择。',
    target: '[data-tutorial="header-model-selector-mobile"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-options',
    title: '模型列表',
    description: '这里是手机端可选模型。',
    target: '[data-tutorial="header-model-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-runmode-options',
    title: '思考模式列表',
    description: '这里可切换快速 / 思考。',
    target: '[data-tutorial="header-runmode-options"]',
    mode: 'info',
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-model-selector-close',
    title: '关闭选择菜单',
    description: '下一步会自动点击空白区域关闭菜单。',
    target: '.model-mode-dropdown',
    mode: 'info',
    autoOutsideClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'open-quick-menu',
    title: '打开 + 菜单',
    description: '将自动为你展开快捷菜单。',
    target: '[data-tutorial="quick-menu-open"]',
    mode: 'info',
    autoClick: true,
    placement: 'top'
  },
  {
    id: 'quick-upload',
    title: '上传文件',
    description: '上传本地文件到当前工作区。',
    target: '[data-tutorial="quick-upload"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-review',
    title: '对话回顾',
    description: '回顾并压缩上下文。',
    target: '[data-tutorial="quick-review"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-send-image',
    title: '发送图片',
    description: '当前模型支持时，可发送图片给 AI 分析。',
    target: '[data-tutorial="quick-send-image"]',
    mode: 'info',
    placement: 'left',
    condition: 'model_supports_media'
  },
  {
    id: 'quick-send-video',
    title: '发送视频',
    description: '当前模型支持时，可发送视频给 AI 分析。',
    target: '[data-tutorial="quick-send-video"]',
    mode: 'info',
    placement: 'left',
    condition: 'model_supports_media'
  },
  {
    id: 'quick-tool-disable',
    title: '工具禁用',
    description: '临时禁用工具分类，控制模型行为范围。',
    target: '[data-tutorial="quick-tool-menu"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'quick-settings',
    title: '设置菜单',
    description: '这里可快速访问常用功能。',
    target: '[data-tutorial="quick-settings-menu"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'open-settings-submenu',
    title: '打开设置子菜单',
    description: '下一步会自动展开设置子菜单。',
    target: '[data-tutorial="quick-settings-menu"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'open-token-panel',
    title: '打开用量统计',
    description: '下一步会自动打开用量统计面板。',
    target: '[data-tutorial="settings-token-panel"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'token-panel',
    title: 'Token 统计面板',
    description: '这里可以查看上下文、额度和资源统计。',
    target: '[data-tutorial="token-drawer"]',
    mode: 'info',
    placement: 'left'
  },
  {
    id: 'close-token-panel',
    title: '关闭统计面板',
    description: '下一步会自动关闭统计面板。',
    target: '[data-tutorial="token-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'left'
  },
  {
    id: 'open-personal-space',
    title: '打开个人空间',
    description: '下一步会自动打开个人空间。',
    target: '[data-tutorial="open-personal-space"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'not_mobile_viewport'
  },
  {
    id: 'mobile-menu-open-personal',
    title: '打开菜单',
    description: '下一步会自动打开菜单。',
    target: '[data-tutorial="mobile-menu-trigger"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'mobile-menu-personal',
    title: '进入个人空间',
    description: '下一步会自动进入个人空间。',
    target: '[data-tutorial="mobile-menu-personal"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom',
    condition: 'is_mobile_viewport'
  },
  {
    id: 'personal-overview',
    title: '个人空间总览',
    description: '这里是个性化设置与高级配置中心。',
    target: '[data-tutorial="personal-card"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-preferences',
    title: '个性化设置',
    description: '下一步自动切换到“个性化设置”。',
    target: '[data-tutorial="personal-tab-preferences"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-preferences',
    title: '个性化设置内容',
    description: '可配置昵称、语气、职业和必备信息。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-model',
    title: '模型偏好',
    description: '下一步自动切换到“模型偏好”。',
    target: '[data-tutorial="personal-tab-model"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-model',
    title: '模型偏好内容',
    description: '可设置默认模型、默认运行模式和思考频率。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-usage',
    title: '用量统计',
    description: '下一步自动切换到“用量统计”。',
    target: '[data-tutorial="personal-tab-usage"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-usage',
    title: '用量统计内容',
    description: '可查看累计 Token、对话数和工具调用数据。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-app-update',
    title: '软件更新',
    description: '移动端将自动切换到“软件更新”。',
    target: '[data-tutorial="personal-tab-app-update"]',
    mode: 'info',
    autoClick: true,
    placement: 'right',
    condition: 'is_app_shell'
  },
  {
    id: 'page-app-update',
    title: '软件更新内容',
    description: '这里会展示最新版本和更新说明。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right',
    condition: 'is_app_shell'
  },
  {
    id: 'tab-behavior',
    title: '智能体行为',
    description: '下一步自动切换到“智能体行为”。',
    target: '[data-tutorial="personal-tab-behavior"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-behavior',
    title: '智能体行为内容',
    description: '可配置工具显示、压缩策略等高级选项。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-skills',
    title: 'Skills',
    description: '下一步自动切换到“Skills”。',
    target: '[data-tutorial="personal-tab-skills"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-skills',
    title: 'Skills 内容',
    description: '可启用/禁用技能并同步到工作区。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-image',
    title: '图片压缩',
    description: '下一步自动切换到“图片压缩”。',
    target: '[data-tutorial="personal-tab-image"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-image',
    title: '图片压缩内容',
    description: '可选择原图、1080p、720p、540p 压缩策略。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'tab-theme',
    title: '主题切换',
    description: '下一步自动切换到“主题切换”。',
    target: '[data-tutorial="personal-tab-theme"]',
    mode: 'info',
    autoClick: true,
    placement: 'right'
  },
  {
    id: 'page-theme',
    title: '主题切换内容',
    description: '可在经典、明亮、夜间主题间切换。',
    target: '[data-tutorial="personal-content-shell"]',
    mode: 'info',
    placement: 'right'
  },
  {
    id: 'close-personal-space',
    title: '关闭个人空间',
    description: '下一步自动关闭个人空间并结束导览。',
    target: '[data-tutorial="personal-close"]',
    mode: 'info',
    autoClick: true,
    placement: 'bottom'
  },
  {
    id: 'done',
    title: '教程完成！',
    description: '恭喜你完成新手教程。可随时在个人空间「新手教程」再次查看。',
    target: null,
    mode: 'info',
    placement: 'center'
  }
];

interface TutorialState {
  running: boolean;
  completed: boolean;
  currentIndex: number;
  steps: TutorialStep[];
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

const shouldIncludeStepByCondition = (step: TutorialStep): boolean => {
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
    steps: DEFAULT_STEPS,
    activeSelector: null
  }),
  getters: {
    activeStep(state): TutorialStep | null {
      return state.steps[state.currentIndex] || null;
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
    shouldIncludeStep(step: TutorialStep): boolean {
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
