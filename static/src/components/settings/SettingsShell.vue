<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import { storeToRefs } from 'pinia';
import SettingsNavIcon from './SettingsNavIcon.vue';
import ProvidersTab from './tabs/ProvidersTab.vue';
import ModelsTab from './tabs/ModelsTab.vue';
import ModelPrefTab from './tabs/ModelPrefTab.vue';
import CodexTab from './tabs/CodexTab.vue';
import GeneralTab from './tabs/GeneralTab.vue';
import WorkspaceTab from './tabs/WorkspaceTab.vue';
import ToolsTab from './tabs/ToolsTab.vue';
import ContextTab from './tabs/ContextTab.vue';
import FilesTab from './tabs/FilesTab.vue';
import VoiceTab from './tabs/VoiceTab.vue';
import SubAgentsTab from './tabs/SubAgentsTab.vue';
import ReviewAgentsTab from './tabs/ReviewAgentsTab.vue';
import AppearanceTab from './tabs/AppearanceTab.vue';
import AdminTab from './tabs/AdminTab.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import { useSettingsStore } from '@/stores/settings';
import type { SettingsSection } from '@/stores/settings';
import { SETTINGS_ADMIN_SECTIONS } from '@/stores/settings';
import { usePersonalizationContext } from '@/components/personalization/usePersonalizationContext';
// 复用个人空间的共享样式（settings-* 设置行 / 浮层 / 控件均为全局类）
import '@/components/personalization/styles/settings-shared.css';

defineOptions({ name: 'SettingsShell' });

/**
 * 全屏设置页壳（自包含、无 props）。
 *
 * 接线约定（由 App.vue 侧完成）：
 * - 挂载：<SettingsShell v-if="settingsOpen" @close="settingsOpen = false" />
 * - 返回按钮仅 emit('close')，本组件不自行决定关闭后的去向。
 * - 打开前无需手动初始化：挂载时自动补拉 personalization 数据（已加载则跳过）；
 *   管理员可见性由 settings store 判定（会话状态自动拉取一次）。
 */
const emit = defineEmits<{ (e: 'close'): void }>();

const personalization = usePersonalizationStore();
const settingsStore = useSettingsStore();
const { activeSection } = storeToRefs(settingsStore);

// 向设置页子树提供与个人空间抽屉同一份上下文（tabs 零改动复用）。
// 设置页由 App.vue v-if 挂载，存在即可见，hostVisible 恒为 true。
const hostVisible = ref(true);
usePersonalizationContext({ activeTab: activeSection, hostVisible });

// ── 导航分组（顺序与 settings-demo 一致；admin 标记项仅管理员可见） ──
interface NavItem {
  id: SettingsSection;
  labelKey: string;
  icon: string;
}
interface NavGroup {
  id: string;
  titleKey: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    id: 'model',
    titleKey: 'settings.groupModel',
    items: [
      { id: 'providers', labelKey: 'settings.navProviders', icon: 'providers' },
      { id: 'models', labelKey: 'settings.navModels', icon: 'models' },
      { id: 'model-pref', labelKey: 'settings.navModelPref', icon: 'model-pref' },
      { id: 'codex', labelKey: 'settings.navCodex', icon: 'codex' }
    ]
  },
  {
    id: 'system',
    titleKey: 'settings.groupSystem',
    items: [
      { id: 'general', labelKey: 'settings.navGeneral', icon: 'general' },
      { id: 'workspace', labelKey: 'settings.navWorkspace', icon: 'workspace' },
      { id: 'tools', labelKey: 'settings.navTools', icon: 'tools' },
      { id: 'context', labelKey: 'settings.navContext', icon: 'context' },
      { id: 'files', labelKey: 'settings.navFiles', icon: 'files' },
      { id: 'voice', labelKey: 'settings.navVoice', icon: 'voice' }
    ]
  },
  {
    id: 'agents',
    titleKey: 'settings.groupAgents',
    items: [
      { id: 'sub-agents', labelKey: 'settings.navSubAgents', icon: 'sub-agents' },
      { id: 'review-agents', labelKey: 'settings.navReviewAgents', icon: 'review-agents' }
    ]
  },
  {
    id: 'interface',
    titleKey: 'settings.groupInterface',
    items: [{ id: 'appearance', labelKey: 'settings.navAppearance', icon: 'appearance' }]
  },
  {
    id: 'admin',
    titleKey: 'settings.groupAdmin',
    items: [{ id: 'admin', labelKey: 'settings.navAdmin', icon: 'admin' }]
  }
];

const isAdmin = computed(() => settingsStore.isAdmin);

/** 过滤管理员项后的可见导航（整组为空时隐藏组标题） */
const visibleNavGroups = computed(() =>
  NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !SETTINGS_ADMIN_SECTIONS.has(item.id) || isAdmin.value)
  })).filter((group) => group.items.length > 0)
);

// 管理员身份变化导致当前分区不可见时，回退到默认分区
watch(isAdmin, () => {
  if (!isAdmin.value && SETTINGS_ADMIN_SECTIONS.has(activeSection.value)) {
    settingsStore.setActiveSection('general');
  }
});

// ── 分区元信息（页标题/说明文案 key + 组件映射） ──
const SECTION_META: Record<SettingsSection, { titleKey: string; descKey: string }> = {
  providers: { titleKey: 'settings.providersTitle', descKey: 'settings.providersDesc' },
  models: { titleKey: 'settings.modelsTitle', descKey: 'settings.modelsDesc' },
  'model-pref': { titleKey: 'settings.modelPrefTitle', descKey: 'settings.modelPrefDesc' },
  codex: { titleKey: 'settings.codexTitle', descKey: 'settings.codexDesc' },
  general: { titleKey: 'settings.generalTitle', descKey: 'settings.generalDesc' },
  workspace: { titleKey: 'settings.workspaceTitle', descKey: 'settings.workspaceDesc' },
  tools: { titleKey: 'settings.toolsTitle', descKey: 'settings.toolsDesc' },
  context: { titleKey: 'settings.contextTitle', descKey: 'settings.contextDesc' },
  files: { titleKey: 'settings.filesTitle', descKey: 'settings.filesDesc' },
  voice: { titleKey: 'settings.voiceTitle', descKey: 'settings.voiceDesc' },
  'sub-agents': { titleKey: 'settings.subAgentsTitle', descKey: 'settings.subAgentsDesc' },
  'review-agents': { titleKey: 'settings.reviewAgentsTitle', descKey: 'settings.reviewAgentsDesc' },
  appearance: { titleKey: 'settings.appearanceTitle', descKey: 'settings.appearanceDesc' },
  admin: { titleKey: 'settings.adminTitle', descKey: 'settings.adminDesc' }
};

const SECTION_COMPONENTS: Record<SettingsSection, Component> = {
  providers: ProvidersTab,
  models: ModelsTab,
  'model-pref': ModelPrefTab,
  codex: CodexTab,
  general: GeneralTab,
  workspace: WorkspaceTab,
  tools: ToolsTab,
  context: ContextTab,
  files: FilesTab,
  voice: VoiceTab,
  'sub-agents': SubAgentsTab,
  'review-agents': ReviewAgentsTab,
  appearance: AppearanceTab,
  admin: AdminTab
};

const activeMeta = computed(() => SECTION_META[activeSection.value] || SECTION_META.general);
const activeComponent = computed(
  () => SECTION_COMPONENTS[activeSection.value] || SECTION_COMPONENTS.general
);

const selectSection = (section: SettingsSection) => {
  settingsStore.setActiveSection(section);
};

const { loaded, loading, error, status } = storeToRefs(personalization);

// 切换分区时内容区回到顶部（各分区语义独立，不继承上一页滚动位置）
const contentEl = ref<HTMLElement | null>(null);
watch(activeSection, () => {
  contentEl.value?.scrollTo({ top: 0 });
});

onMounted(() => {
  // 数据兜底：设置页可能是 personalization 数据的首个消费者（个人空间未打开过）
  if (!loaded.value && !loading.value) {
    void personalization.fetchPersonalization();
  }
  // URL 深链：/settings/<section> 直接落地到指定分区；
  // 非法分区、或管理员分区但当前会话非管理员时不生效（保持默认分区）。
  void (async () => {
    await settingsStore.fetchSessionStatus();
    const match = window.location.pathname.match(/^\/settings\/([^/]+)\/?$/);
    if (!match) return;
    const target = match[1] as SettingsSection;
    const exists = NAV_GROUPS.some((group) => group.items.some((item) => item.id === target));
    if (exists && (!SETTINGS_ADMIN_SECTIONS.has(target) || settingsStore.isAdmin)) {
      settingsStore.setActiveSection(target);
    }
  })();
});
</script>

<template>
  <!-- settings-redesign-card 是迁移 tab 的宿主契约：提供浮层背景变量
       （--settings-floating-menu-bg，缺失时浅色/经典主题浮层透明）
       与 @container settings-card 响应式上下文；
       position: fixed 由下方 scoped 样式以更高特异性保持，不受该类 relative 影响 -->
  <div class="settings-shell settings-redesign-card">
    <!-- 顶栏：返回 + 标题 + 保存反馈 -->
    <header class="settings-shell-topbar">
      <button
        type="button"
        class="settings-shell-back"
        :aria-label="$t('settings.backAriaLabel')"
        :title="$t('settings.backAriaLabel')"
        @click="emit('close')"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path
            d="M10 3L5 8l5 5"
            stroke="currentColor"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
      <span class="settings-shell-title">{{ $t('settings.title') }}</span>
      <span class="settings-shell-spacer"></span>
      <span v-if="status" class="settings-shell-status success">{{ status }}</span>
      <span v-else-if="error && loaded" class="settings-shell-status error">{{ error }}</span>
    </header>

    <div class="settings-shell-layout">
      <!-- 左侧分组导航 -->
      <nav class="settings-shell-nav" :aria-label="$t('settings.navAriaLabel')">
        <div v-for="group in visibleNavGroups" :key="group.id" class="settings-shell-nav-group">
          <div class="settings-shell-nav-group-title">{{ $t(group.titleKey) }}</div>
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            class="settings-shell-nav-item"
            :class="{ active: activeSection === item.id }"
            :aria-pressed="activeSection === item.id"
            @click="selectSection(item.id)"
          >
            <SettingsNavIcon :name="item.icon" />
            <span class="settings-shell-nav-label">{{ $t(item.labelKey) }}</span>
            <span v-if="SETTINGS_ADMIN_SECTIONS.has(item.id)" class="settings-shell-admin-badge">
              {{ $t('settings.adminBadge') }}
            </span>
          </button>
        </div>
      </nav>

      <!-- 右内容区（独立滚动，760px 居中） -->
      <main class="settings-shell-content" ref="contentEl">
        <div class="settings-shell-content-inner" v-if="loaded">
          <div class="settings-shell-page-title">{{ $t(activeMeta.titleKey) }}</div>
          <div class="settings-shell-page-desc">{{ $t(activeMeta.descKey) }}</div>
          <component :is="activeComponent" :key="activeSection" />
        </div>
        <div class="settings-shell-content-inner settings-shell-placeholder" v-else-if="error">
          <span class="settings-shell-loaderror">{{ error }}</span>
          <button
            type="button"
            class="settings-secondary-button"
            @click="personalization.fetchPersonalization()"
          >
            {{ $t('common.retry') }}
          </button>
        </div>
        <div class="settings-shell-content-inner settings-shell-placeholder" v-else>
          {{ $t('personalization.loadingPersonalization') }}
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
/* ===== 全屏壳（实体面板：不透明底，无磨砂） ===== */
.settings-shell {
  position: fixed;
  inset: 0;
  z-index: 400;
  display: flex;
  flex-direction: column;
  background: var(--surface-base);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.5;
}

/* ===== 顶栏 ===== */
.settings-shell-topbar {
  height: 52px;
  flex: 0 0 52px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border-bottom: 1px solid var(--border-default);
  background: var(--surface-panel);
}

.settings-shell-back {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.settings-shell-back:hover {
  background: var(--hover-bg);
  color: var(--text-primary);
}

.settings-shell-title {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}

.settings-shell-spacer {
  flex: 1;
  min-width: 0;
}

.settings-shell-status {
  height: 52px;
  display: inline-flex;
  align-items: center;
  font-size: 12.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40%;
}

.settings-shell-status.success {
  color: var(--state-success);
}

.settings-shell-status.error {
  color: var(--state-danger);
}

/* ===== 布局 ===== */
.settings-shell-layout {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* ===== 左侧导航 ===== */
.settings-shell-nav {
  width: 232px;
  flex-shrink: 0;
  border-right: 1px solid var(--border-default);
  background: var(--surface-rail);
  padding: 12px 8px;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-shell-nav::-webkit-scrollbar {
  display: none;
}

.settings-shell-nav-group {
  margin-bottom: 16px;
  /* 项间 4px 间距：hover/选中背景块之间留出空隙，不再上下相贴 */
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-shell-nav-group-title {
  height: 24px;
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 0 10px;
  margin-bottom: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.settings-shell-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 13px;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
}

.settings-shell-nav-item:hover {
  background: var(--hover-bg);
}

.settings-shell-nav-item.active {
  background: var(--tab-active);
  color: var(--text-primary);
  font-weight: 500;
}

.settings-shell-nav-label {
  min-width: 0;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.settings-shell-admin-badge {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  border: 1px solid var(--border-default);
  border-radius: 4px;
  padding: 0 5px;
  line-height: 16px;
  height: 18px;
  display: inline-flex;
  align-items: center;
}

/* ===== 内容区 ===== */
.settings-shell-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.settings-shell-content::-webkit-scrollbar {
  display: none;
}

.settings-shell-content-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 36px 48px 80px;
}

.settings-shell-page-title {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.settings-shell-page-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  margin-bottom: 28px;
}

.settings-shell-placeholder {
  color: var(--text-tertiary);
  font-size: 13px;
}

.settings-shell-loaderror {
  display: block;
  color: var(--state-danger);
  margin-bottom: 12px;
}

/* ===== 窄屏（≤760px）：导航折叠为顶部横向滚动条 ===== */
@media (max-width: 760px) {
  .settings-shell {
    z-index: 1700;
  }

  .settings-shell-layout {
    flex-direction: column;
  }

  .settings-shell-nav {
    width: 100%;
    flex: 0 0 auto;
    display: flex;
    gap: 2px;
    border-right: 0;
    border-bottom: 1px solid var(--border-default);
    padding: 8px;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .settings-shell-nav-group {
    display: contents;
  }

  .settings-shell-nav-group-title {
    display: none;
  }

  .settings-shell-nav-item {
    width: auto;
    flex: 0 0 auto;
    padding: 0 12px;
  }

  .settings-shell-nav-label {
    flex: 0 0 auto;
  }

  .settings-shell-content-inner {
    padding: 24px 20px 64px;
  }
}
</style>
