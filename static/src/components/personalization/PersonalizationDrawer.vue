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
                  <span class="settings-mobile-bar-btn">
                    <CloseButton
                      :label="$t('personalization.closePersonalSpaceAriaLabel')"
                      @click="personalization.closeDrawer()"
                    />
                  </span>
                </div>
                <div class="settings-nav-head">
                  <CloseButton
                    data-tutorial="personal-close"
                    :label="$t('personalization.closePersonalSpaceAriaLabel')"
                    @click="personalization.closeDrawer()"
                  />
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
                    <svg
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    >
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
                  <transition
                    name="personal-page-vertical"
                    :mode="isMobileLayout() ? undefined : 'out-in'"
                  >
                    <PreferencesTab v-if="activeTab === 'preferences'" key="preferences" />

                    <AccountTab v-else-if="activeTab === 'account'" key="account" />
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
        <div class="personalization-loading" v-else>
          {{ $t('personalization.loadingPersonalization') }}
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';
import { storeToRefs } from 'pinia';
import CloseButton from '@/components/common/CloseButton.vue';
import PreferencesTab from './tabs/PreferencesTab.vue';
import AccountTab from './tabs/AccountTab.vue';
import { usePersonalizationStore } from '@/stores/personalization';
import type { PersonalDrawerTab } from '@/stores/personalization';
import { ICONS } from '@/utils/icons';
import { t, currentLocale } from '@/locales';
import { usePersonalizationContext } from './usePersonalizationContext';
import './styles/settings-shared.css';

defineOptions({ name: 'PersonalizationDrawer' });

/**
 * 个人空间抽屉（精简版）：只保留「个性化 / 账户」两个页签，
 * 其余设置项已迁入全屏设置页（components/settings/SettingsShell.vue）。
 * 共享上下文（表单、下拉、各 tab 数据加载）由 usePersonalizationContext 提供。
 */
const personalization = usePersonalizationStore();
const { visible, loading, activeTab, status, error } = storeToRefs(personalization);

// 向本抽屉子树提供共享上下文（与 SettingsShell 同一份逻辑）
const drawerContext = usePersonalizationContext({ activeTab, hostVisible: visible });
const { closeDropdown } = drawerContext;

type IconKey = keyof typeof ICONS;

const personalTabs = [
  { id: 'preferences', labelKey: 'personalization.tabPreferences', icon: 'userPen' },
  { id: 'account', labelKey: 'personalization.tabAccount', icon: 'user' }
] as const satisfies ReadonlyArray<{ id: PersonalDrawerTab; labelKey: string; icon: IconKey }>;

const activeTabLabel = computed(() => {
  void currentLocale.value;
  const tab = personalTabs.find((tab) => tab.id === activeTab.value);
  return tab ? t(tab.labelKey) : t('personalization.tabPreferences');
});

const settingsTabIconStyle = (icon: IconKey) => ({
  '--icon-src': `url(${ICONS[icon]})`
});

// ---- 窄屏（≤760px）iOS 设置式「列表 → 子页」导航 ----
const mobileInSubPage = ref(false);
const isMobileLayout = () =>
  typeof window !== 'undefined' && window.matchMedia('(max-width: 760px)').matches;

const setActiveTab = (tab: PersonalDrawerTab) => {
  personalization.setActiveTab(tab);
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
</script>
