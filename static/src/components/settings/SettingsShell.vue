<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { Component } from 'vue';
import { storeToRefs } from 'pinia';
import SettingsNavIcon from './SettingsNavIcon.vue';
import ProvidersTab from './tabs/ProvidersTab.vue';
import ModelsTab from './tabs/ModelsTab.vue';
import ModelPrefTab from './tabs/ModelPrefTab.vue';
import CodexTab from './tabs/CodexTab.vue';
import GeneralTab from './tabs/GeneralTab.vue';
import ChatTab from './tabs/ChatTab.vue';
import QuickEntrySettings from './tabs/QuickEntrySettings.vue';
import WorkspaceTab from './tabs/WorkspaceTab.vue';
import ToolsTab from './tabs/ToolsTab.vue';
import McpTab from './tabs/McpTab.vue';
import SearchTab from './tabs/SearchTab.vue';
import ContextTab from './tabs/ContextTab.vue';
import FilesTab from './tabs/FilesTab.vue';
import BrowserTab from './tabs/BrowserTab.vue';
import SubAgentsTab from './tabs/SubAgentsTab.vue';
import ReviewAgentsTab from './tabs/ReviewAgentsTab.vue';
import AppearanceTab from './tabs/AppearanceTab.vue';
import UsageStatsTab from './tabs/UsageStatsTab.vue';
import StorageTab from './tabs/StorageTab.vue';
import UiIconButton from '@/components/ui/UiIconButton.vue';
import UiSearchField from '@/components/ui/UiSearchField.vue';
import { isDesktopShell } from '@/utils/desktopPlatform';
import { t } from '@/locales';
import { usePersonalizationStore } from '@/stores/personalization';
import { useSettingsStore, SETTINGS_ADMIN_SECTIONS } from '@/stores/settings';
import type { SettingsSection } from '@/stores/settings';
import { usePersonalizationContext } from '@/components/personalization/usePersonalizationContext';
import {
  NAV_GROUPS,
  SECTION_TITLE_KEYS,
  findSettingRow,
  searchSettings,
  type SearchResult
} from './settingsNav';
import './settings.scss';

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
const { loaded, loading, error, status } = storeToRefs(personalization);

// 向设置页子树提供与个人空间抽屉同一份上下文（tabs 零改动复用）。
// 设置页由 App.vue v-if 挂载，存在即可见，hostVisible 恒为 true。
const hostVisible = ref(true);
usePersonalizationContext({ activeTab: activeSection, hostVisible });

// ── 导航 ──
const isAdmin = computed(() => settingsStore.isAdmin);
const quickChatSupported = isDesktopShell() && Boolean(window.astrionQuickSettings);
const canShowSection = (section: SettingsSection) =>
  (section !== 'quick-chat' || quickChatSupported) &&
  (!SETTINGS_ADMIN_SECTIONS.has(section) || isAdmin.value);

const navLabelKey = (_id: SettingsSection, labelKey: string) => labelKey;

/** 过滤管理员项后的可见导航（整组为空时隐藏组标题） */
const visibleNavGroups = computed(() =>
  NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items
      .filter((item) => canShowSection(item.id))
      .map((item) => ({ ...item, labelKey: navLabelKey(item.id, item.labelKey) }))
  })).filter((group) => group.items.length > 0)
);

const NAV_ITEMS = NAV_GROUPS.flatMap((group) => group.items);
const navIconOf = (section: SettingsSection) =>
  NAV_ITEMS.find((item) => item.id === section)?.icon || 'general';
const sectionLabel = (section: SettingsSection) => {
  const item = NAV_ITEMS.find((entry) => entry.id === section);
  return item ? t(navLabelKey(section, item.labelKey)) : section;
};

// 管理员身份变化导致当前分区不可见时，回退到默认分区
watch(isAdmin, () => {
  if (!isAdmin.value && SETTINGS_ADMIN_SECTIONS.has(activeSection.value)) {
    settingsStore.setActiveSection('general');
  }
});

// ── 分区内容 ──
const SECTION_COMPONENTS: Record<SettingsSection, Component> = {
  usage: UsageStatsTab,
  storage: StorageTab,
  providers: ProvidersTab,
  models: ModelsTab,
  'model-pref': ModelPrefTab,
  codex: CodexTab,
  general: GeneralTab,
  chat: ChatTab,
  'quick-chat': QuickEntrySettings,
  workspace: WorkspaceTab,
  search: SearchTab,
  tools: ToolsTab,
  mcp: McpTab,
  context: ContextTab,
  files: FilesTab,
  browser: BrowserTab,
  'sub-agents': SubAgentsTab,
  'review-agents': ReviewAgentsTab,
  appearance: AppearanceTab
};

const activeTitleKey = computed(
  () => SECTION_TITLE_KEYS[activeSection.value] || SECTION_TITLE_KEYS.general
);
const activeComponent = computed(
  () => SECTION_COMPONENTS[activeSection.value] || SECTION_COMPONENTS.general
);
const isWide = computed(() => activeSection.value === 'usage' || activeSection.value === 'storage');

const selectSection = (section: SettingsSection) => {
  settingsStore.setActiveSection(section);
};

// ── 顶部大标题：内容滚到即将与标题重叠时淡出 ──
const scrollEl = ref<HTMLElement | null>(null);
const innerEl = ref<HTMLElement | null>(null);
const titleEl = ref<HTMLElement | null>(null);
const titleOpacity = ref(1);
const TITLE_FADE_DISTANCE = 28;
let titleFrame = 0;

const updateTitleOpacity = () => {
  titleFrame = 0;
  const scroller = scrollEl.value;
  const inner = innerEl.value;
  const title = titleEl.value;
  if (!scroller || !inner || !title) return;
  // 内容起点（内层上内边距）与标题底边之间的空隙滚完后开始淡出
  const contentStart = parseFloat(getComputedStyle(inner).paddingTop) || 0;
  const titleBottom = title.offsetTop + title.offsetHeight;
  const overlap = scroller.scrollTop - (contentStart - titleBottom);
  titleOpacity.value = Math.min(1, Math.max(0, 1 - overlap / TITLE_FADE_DISTANCE));
};

const onContentScroll = () => {
  if (!titleFrame) titleFrame = requestAnimationFrame(updateTitleOpacity);
};

// 切换分区时内容区回到顶部（各分区语义独立，不继承上一页滚动位置）
watch(activeSection, () => {
  scrollEl.value?.scrollTo({ top: 0 });
  titleOpacity.value = 1;
});

// ── 搜索 ──
const query = ref('');
const focusIndex = ref(-1);
const activeResultKey = ref('');
const searching = computed(() => query.value.trim().length > 0);
const results = computed<SearchResult[]>(() =>
  searching.value ? searchSettings(query.value, sectionLabel, canShowSection) : []
);
const resultButtons = ref<HTMLElement[]>([]);

watch(query, () => {
  focusIndex.value = -1;
  activeResultKey.value = '';
});

let highlightTimer = 0;
let locateTimer = 0;

const highlightRow = (anchor: string, attempt = 0) => {
  const root = innerEl.value;
  const row = root ? findSettingRow(root, anchor) : null;
  if (!row) {
    // 分区可能在异步加载数据后才渲染出目标行；最多等约 1 秒
    if (attempt < 10) {
      locateTimer = window.setTimeout(() => highlightRow(anchor, attempt + 1), 100);
    }
    return;
  }
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  row.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
  row.classList.remove('stg-search-hit');
  void row.offsetWidth;
  row.classList.add('stg-search-hit');
  window.clearTimeout(highlightTimer);
  highlightTimer = window.setTimeout(() => row.classList.remove('stg-search-hit'), 1700);
};

const openResult = async (result: SearchResult) => {
  activeResultKey.value = result.key;
  window.clearTimeout(locateTimer);
  if (activeSection.value !== result.section) {
    selectSection(result.section);
  }
  if (!result.anchor) return;
  await nextTick();
  highlightRow(result.anchor);
};

const moveFocus = (delta: number) => {
  const count = results.value.length;
  if (!count) return;
  focusIndex.value =
    focusIndex.value < 0 ? (delta > 0 ? 0 : count - 1) : (focusIndex.value + delta + count) % count;
  void nextTick(() => resultButtons.value[focusIndex.value]?.scrollIntoView({ block: 'nearest' }));
};

const onSearchKeydown = (event: KeyboardEvent) => {
  if (!searching.value) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    moveFocus(event.key === 'ArrowDown' ? 1 : -1);
  } else if (event.key === 'Enter') {
    const target = results.value[focusIndex.value < 0 ? 0 : focusIndex.value];
    if (target) {
      event.preventDefault();
      void openResult(target);
    }
  }
};

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
    if (NAV_ITEMS.some((item) => item.id === target) && canShowSection(target)) {
      settingsStore.setActiveSection(target);
    }
  })();
});

onBeforeUnmount(() => {
  if (titleFrame) cancelAnimationFrame(titleFrame);
  window.clearTimeout(highlightTimer);
  window.clearTimeout(locateTimer);
});
</script>

<template>
  <!-- settings-redesign-card 是设置页迁移 tab 的宿主契约，position: fixed 由 settings.scss 保持。 -->
  <div class="stg-shell settings-redesign-card">
    <!-- 左侧悬浮面板：返回 + 标题 + 搜索 + 导航 -->
    <nav class="stg-nav-pane" :aria-label="$t('settings.navAriaLabel')">
      <div class="stg-nav-heading">
        <UiIconButton
          icon="arrow-left"
          :label="$t('settings.backAriaLabel')"
          @click="emit('close')"
        />
        <h1>{{ $t('settings.title') }}</h1>
        <span v-if="status" class="stg-nav-status" role="status">{{ status }}</span>
        <span
          v-else-if="error && loaded"
          class="stg-nav-status is-error"
          role="alert"
          :title="error"
          >{{ error }}</span
        >
      </div>
      <div class="stg-nav-search">
        <UiSearchField
          v-model="query"
          :placeholder="$t('settings.searchPlaceholder')"
          :clear-label="$t('settings.searchClear')"
          @keydown="onSearchKeydown"
        />
      </div>
      <div class="stg-nav-list" :class="{ 'is-searching': searching }">
        <template v-if="searching">
          <div v-if="results.length" class="stg-nav-items">
            <button
              v-for="(result, index) in results"
              :key="result.key"
              ref="resultButtons"
              type="button"
              class="stg-nav-item"
              :class="{
                'is-focused': index === focusIndex,
                'is-active': result.key === activeResultKey
              }"
              @click="openResult(result)"
            >
              <SettingsNavIcon :name="navIconOf(result.section)" />
              <span class="stg-nav-label">{{ result.title }}</span>
              <span v-if="result.meta" class="stg-nav-item-meta">{{ result.meta }}</span>
            </button>
          </div>
          <p v-else class="stg-nav-empty">{{ $t('settings.searchEmpty') }}</p>
        </template>
        <template v-else>
          <section v-for="group in visibleNavGroups" :key="group.id" class="stg-nav-group">
            <div class="stg-nav-group-title">{{ $t(group.titleKey) }}</div>
            <div class="stg-nav-items">
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="stg-nav-item"
                :class="{ 'is-active': activeSection === item.id }"
                :aria-current="activeSection === item.id ? 'page' : undefined"
                @click="selectSection(item.id)"
              >
                <SettingsNavIcon :name="item.icon" />
                <span class="stg-nav-label">{{ $t(item.labelKey) }}</span>
              </button>
            </div>
          </section>
        </template>
      </div>
    </nav>

    <!-- 右侧平铺内容：大标题固定在顶部，内容滚到标题处时标题淡出 -->
    <main class="stg-main" :class="{ 'is-wide': isWide }">
      <div class="stg-page-heading" :style="{ opacity: titleOpacity }">
        <div class="stg-page-heading-inner">
          <h2 ref="titleEl" class="stg-page-title">{{ $t(activeTitleKey) }}</h2>
        </div>
      </div>
      <div ref="scrollEl" class="stg-scroll" @scroll.passive="onContentScroll">
        <div ref="innerEl" class="stg-scroll-inner">
          <template v-if="loaded">
            <QuickEntrySettings v-if="quickChatSupported" v-show="activeSection === 'quick-chat'" />
            <component
              v-if="activeSection !== 'quick-chat'"
              :is="activeComponent"
              :key="activeSection"
            />
          </template>
          <div v-else-if="error" class="stg-placeholder">
            <span class="stg-load-error">{{ error }}</span>
            <button
              type="button"
              class="stg-secondary-button"
              @click="personalization.fetchPersonalization()"
            >
              {{ $t('common.retry') }}
            </button>
          </div>
          <div v-else class="stg-placeholder">
            {{ $t('personalization.loadingPersonalization') }}
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
