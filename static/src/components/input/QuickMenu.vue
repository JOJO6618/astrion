<template>
  <transition name="qm-pop">
    <div
      v-if="open"
      ref="menuRef"
      class="quick-menu"
      :class="{ 'is-mobile': isMobile }"
      @click.stop
      @pointerleave="onMenuPointerLeave"
    >
      <!-- 一级菜单。手机端打开二级时隐藏，返回时从左侧推回 -->
      <transition name="qm-view-back">
        <div v-show="!mobileSubActive" class="quick-menu__view" @pointerover="onMainPointerOver">
          <button
            type="button"
            class="quick-menu__item"
            data-tutorial="quick-upload"
            :disabled="!isConnected || uploading"
            @click="$emit('quick-upload')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('file')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">
              {{ uploading ? $t('quickdock.uploading') : $t('quickdock.uploadFile') }}
            </span>
          </button>
          <button
            v-if="currentModelSupportsImage"
            type="button"
            class="quick-menu__item"
            data-tutorial="quick-send-image"
            :disabled="!isConnected || streamingMessage"
            @click="$emit('pick-images')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('camera')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.sendImage') }}</span>
          </button>
          <button
            v-if="currentModelSupportsVideo"
            type="button"
            class="quick-menu__item"
            data-tutorial="quick-send-video"
            :disabled="!isConnected || streamingMessage"
            @click="$emit('pick-video')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('play')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.sendVideo') }}</span>
          </button>

          <div class="quick-menu__sep" role="separator"></div>

          <button
            type="button"
            class="quick-menu__item"
            :disabled="!isConnected || (goalResumable && streamingMessage)"
            @click="$emit('toggle-goal-mode')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('flag')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.goalMode') }}</span>
            <span v-if="goalResumable" class="quick-menu__meta">{{ $t('goal.resume') }}</span>
            <span v-else-if="goalRunning" class="quick-menu__meta">
              <span class="quick-menu__dot" aria-hidden="true"></span>{{ $t('common.running') }}
            </span>
            <span v-else-if="goalCompleted" class="quick-menu__meta">{{
              $t('quickdock.goalDone')
            }}</span>
          </button>
          <button
            v-if="browserTools.supported"
            type="button"
            class="quick-menu__item"
            :aria-pressed="browserEffective"
            :disabled="!isConnected || browserTools.busy"
            @click="browserTools.set(currentConversationId, !browserEffective)"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('browser')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.browserTools') }}</span>
            <span v-if="browserTools.pending" class="quick-menu__meta">
              {{ $t('quickdock.browserPending') }}
            </span>
            <span
              class="quick-menu__switch"
              :class="{ 'is-on': browserEffective }"
              aria-hidden="true"
            ></span>
          </button>
          <button
            type="button"
            class="quick-menu__item"
            :class="{ 'is-open': openSub === 'tools' }"
            data-submenu="tools"
            data-tutorial="quick-tool-menu"
            :aria-expanded="openSub === 'tools'"
            :disabled="!isConnected"
            @click="onSubTriggerClick($event, 'tools')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('wrench')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.tools') }}</span>
            <span
              class="icon quick-menu__chevron"
              :style="getIconStyle('chevronDown')"
              aria-hidden="true"
            ></span>
          </button>

          <div class="quick-menu__sep" role="separator"></div>

          <button
            type="button"
            class="quick-menu__item"
            data-tutorial="quick-review"
            :disabled="!isConnected || streamingMessage"
            @click="$emit('open-review')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('notebookSearch')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.conversationReview') }}</span>
          </button>
          <button
            type="button"
            class="quick-menu__item"
            :disabled="compressing || !isConnected"
            @click="$emit('compress-conversation')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('layers')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">
              {{ compressing ? $t('quickdock.compressing') : $t('quickdock.compressConversation') }}
            </span>
          </button>
          <button
            type="button"
            class="quick-menu__item"
            :class="{ 'is-open': openSub === 'settings' }"
            data-submenu="settings"
            data-tutorial="quick-settings-menu"
            :aria-expanded="openSub === 'settings'"
            :disabled="!isConnected"
            @click="onSubTriggerClick($event, 'settings')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('settings')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.settingsMenu') }}</span>
            <span
              class="icon quick-menu__chevron"
              :style="getIconStyle('chevronDown')"
              aria-hidden="true"
            ></span>
          </button>
        </div>
      </transition>

      <!-- 二级：工具。桌面端在一级右侧展开；手机端替换一级（从右侧推入） -->
      <transition :name="submenuTransition">
        <div
          v-if="toolMenuOpen"
          class="quick-submenu"
          :style="submenuStyle()"
          @pointerenter="onSubmenuPointerEnter"
        >
          <template v-if="isMobile">
            <button
              type="button"
              class="quick-menu__item quick-menu__back"
              @click="requestSub(null)"
            >
              <span
                class="icon quick-menu__icon"
                :style="getIconStyle('arrowLeft')"
                aria-hidden="true"
              ></span>
              <span class="quick-menu__label">{{ $t('quickdock.tools') }}</span>
            </button>
            <div class="quick-menu__sep" role="separator"></div>
          </template>
          <div v-if="toolSettingsLoading && !toolSettings.length" class="quick-menu__status">
            {{ $t('quickdock.toolSettingsSyncing') }}
          </div>
          <div v-else-if="!toolSettings.length" class="quick-menu__status">
            {{ $t('quickdock.noControllableTools') }}
          </div>
          <template v-else>
            <button
              v-for="category in toolSettings"
              :key="category.id"
              type="button"
              class="quick-menu__item"
              :class="{ 'is-locked': category.locked }"
              :aria-pressed="category.enabled"
              :disabled="streamingMessage || !isConnected || toolSettingsLoading"
              @click="$emit('update-tool-category', category.id, !category.enabled)"
            >
              <span
                class="icon quick-menu__icon"
                :style="getIconStyle(toolCategoryIcon(category.id))"
                aria-hidden="true"
              ></span>
              <span class="quick-menu__label">{{ category.label }}</span>
              <span v-if="category.locked" class="quick-menu__meta">{{
                $t('quickdock.toolLocked')
              }}</span>
              <span
                class="quick-menu__switch"
                :class="{ 'is-on': category.enabled }"
                aria-hidden="true"
              ></span>
            </button>
          </template>
        </div>
      </transition>

      <!-- 二级：设置 -->
      <transition :name="submenuTransition">
        <div
          v-if="settingsOpen"
          class="quick-submenu"
          :style="submenuStyle()"
          @pointerenter="onSubmenuPointerEnter"
        >
          <template v-if="isMobile">
            <button
              type="button"
              class="quick-menu__item quick-menu__back"
              @click="requestSub(null)"
            >
              <span
                class="icon quick-menu__icon"
                :style="getIconStyle('arrowLeft')"
                aria-hidden="true"
              ></span>
              <span class="quick-menu__label">{{ $t('quickdock.settingsMenu') }}</span>
            </button>
            <div class="quick-menu__sep" role="separator"></div>
          </template>
          <button
            type="button"
            class="quick-menu__item"
            :disabled="!isConnected"
            @click="$emit('realtime-terminal')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('monitor')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.realtimeTerminal') }}</span>
          </button>
          <button
            type="button"
            class="quick-menu__item"
            data-tutorial="settings-token-panel"
            :disabled="!currentConversationId"
            @click="$emit('toggle-token-panel', true)"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('chartColumn')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.usageStats') }}</span>
          </button>
          <button
            type="button"
            class="quick-menu__item"
            :disabled="!currentConversationId"
            @click="$emit('toggle-approval-panel')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('clipboard')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.approvalPanel') }}</span>
          </button>
          <button
            v-if="executionModeEnabled"
            type="button"
            class="quick-menu__item"
            @click="$emit('open-path-authorization')"
          >
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('folderOpen')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.pathAuthorization') }}</span>
          </button>
          <!-- 指令拦截为个人级设置，与执行环境无关：Docker/web 也必须显示（不加 executionModeEnabled 限制） -->
          <button type="button" class="quick-menu__item" @click="$emit('open-command-blocking')">
            <span
              class="icon quick-menu__icon"
              :style="getIconStyle('octagon')"
              aria-hidden="true"
            ></span>
            <span class="quick-menu__label">{{ $t('quickdock.commandBlocking') }}</span>
          </button>
        </div>
      </transition>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useBrowserToolsStore } from '@/stores/browserTools';
import { useUiStore } from '@/stores/ui';

defineOptions({ name: 'QuickMenu' });

type SubKey = 'tools' | 'settings';

const props = defineProps<{
  open: boolean;
  isConnected: boolean;
  uploading: boolean;
  streamingMessage: boolean;
  toolMenuOpen: boolean;
  toolSettings: Array<{ id: string; label: string; enabled: boolean; locked?: boolean }>;
  toolSettingsLoading: boolean;
  settingsOpen: boolean;
  compressing: boolean;
  currentConversationId: string | null;
  iconStyle?: (key: string) => Record<string, string>;
  toolCategoryIcon: (categoryId: string) => string;
  modelOptions: Array<{
    key: string;
    label: string;
    description: string;
    disabled?: boolean;
    supportsImage?: boolean;
    supportsVideo?: boolean;
  }>;
  currentModelKey: string;
  blockToolToggle?: boolean;
  executionModeEnabled?: boolean;
  goalRunning?: boolean;
  goalProgress?: Record<string, any> | null;
}>();

const emit = defineEmits<{
  (event: 'quick-upload'): void;
  (event: 'pick-images'): void;
  (event: 'pick-video'): void;
  (event: 'toggle-tool-menu'): void;
  (event: 'toggle-settings'): void;
  (event: 'update-tool-category', id: string, enabled: boolean): void;
  (event: 'realtime-terminal'): void;
  (event: 'toggle-token-panel', fromSettingsMenu?: boolean): void;
  (event: 'compress-conversation'): void;
  (event: 'toggle-approval-panel'): void;
  (event: 'open-review'): void;
  (event: 'open-path-authorization'): void;
  (event: 'open-command-blocking'): void;
  (event: 'toggle-goal-mode'): void;
}>();

const browserTools = useBrowserToolsStore();
const browserEffective = computed(() =>
  props.currentConversationId ? browserTools.effective : browserTools.draft
);
watch(
  () => [props.open, props.currentConversationId] as const,
  ([open, conversationId]) => {
    if (open) void browserTools.refresh(conversationId);
  },
  { immediate: true }
);

const uiStore = useUiStore();
const isMobile = computed(() => uiStore.isMobileViewport);

const getIconStyle = (key: string) => (props.iconStyle ? props.iconStyle(key) : {});

const currentModel = computed(() =>
  props.modelOptions?.find((m) => m.key === props.currentModelKey)
);
const currentModelSupportsImage = computed(() => !!currentModel.value?.supportsImage);
const currentModelSupportsVideo = computed(() => !!currentModel.value?.supportsVideo);

const goalCompleted = computed(
  () => String(props.goalProgress?.status || '').toLowerCase() === 'done'
);
const goalResumable = computed(() => ['paused', 'blocked'].includes(props.goalProgress?.status));

/* ---------------- 二级菜单开合 ----------------
 * 开合状态仍由上层 store 持有（toggle-tool-menu / toggle-settings 两个切换事件，
 * 打开其一会自动关闭另一个），这里只把「想要的目标」翻译成对应的切换事件。 */
const openSub = computed<SubKey | null>(() =>
  props.toolMenuOpen ? 'tools' : props.settingsOpen ? 'settings' : null
);
const mobileSubActive = computed(() => isMobile.value && openSub.value !== null);
const submenuTransition = computed(() => (isMobile.value ? 'qm-view-fwd' : 'qm-sub'));

function requestSub(target: SubKey | null) {
  clearHoverTimer();
  const current = openSub.value;
  if (target === current) return;
  if (target === 'tools') emit('toggle-tool-menu');
  else if (target === 'settings') emit('toggle-settings');
  else if (current === 'tools') emit('toggle-tool-menu');
  else if (current === 'settings') emit('toggle-settings');
}

function onSubTriggerClick(event: MouseEvent, target: SubKey) {
  // 鼠标点击：悬停可能已先展开，点击只负责「确保展开」，避免刚展开就被点收起；
  // 触屏 / 键盘 / 引导脚本的程序化点击：保持切换语义。
  const pointerType = (event as PointerEvent).pointerType;
  if (pointerType === 'mouse' && !isMobile.value) {
    requestSub(target);
    return;
  }
  requestSub(openSub.value === target ? null : target);
}

/* ---------------- 桌面端悬停展开 ---------------- */
const HOVER_OPEN_DELAY = 90;
const HOVER_CLOSE_DELAY = 200; // 略长：给斜向移入二级菜单留时间
let hoverTimer: number | undefined;
let hoveredRow: HTMLElement | null = null;

function clearHoverTimer() {
  if (hoverTimer !== undefined) {
    window.clearTimeout(hoverTimer);
    hoverTimer = undefined;
  }
}

function onMainPointerOver(event: PointerEvent) {
  if (event.pointerType !== 'mouse' || isMobile.value) return;
  const row = (event.target as HTMLElement | null)?.closest<HTMLButtonElement>('.quick-menu__item');
  if (!row || row === hoveredRow) return;
  hoveredRow = row;
  clearHoverTimer();
  if (row.disabled) return;
  const target = (row.dataset.submenu as SubKey | undefined) ?? null;
  if (target === openSub.value) return;
  // 工具开关被策略禁用时，悬停不触发（点击仍会走上层的提示）
  if (target === 'tools' && props.blockToolToggle) return;
  hoverTimer = window.setTimeout(
    () => requestSub(target),
    target ? HOVER_OPEN_DELAY : HOVER_CLOSE_DELAY
  );
}

function onSubmenuPointerEnter() {
  clearHoverTimer();
  hoveredRow = null;
}

function onMenuPointerLeave() {
  clearHoverTimer();
  hoveredRow = null;
}

/* ---------------- 桌面端二级尺寸 ----------------
 * 二级与一级下边对齐（CSS bottom:-1px 抵消一级边框），向上生长；
 * 最大高度为一级高度的 150%，且不超过一级底边到视口顶部的可用空间，超出部分在二级内部滚动。 */
const SUBMENU_MAX_RATIO = 1.5;
const VIEWPORT_GAP = 8;
const menuRef = ref<HTMLElement | null>(null);
const submenuMaxHeight = ref(0);

function submenuStyle() {
  if (isMobile.value || !submenuMaxHeight.value) return undefined;
  return { maxHeight: `${submenuMaxHeight.value}px` };
}

function measureSubmenu() {
  const menu = menuRef.value;
  if (!menu || isMobile.value) return;
  const room = menu.getBoundingClientRect().bottom - VIEWPORT_GAP;
  submenuMaxHeight.value = Math.floor(Math.min(menu.offsetHeight * SUBMENU_MAX_RATIO, room));
}

// 一级高度会随模型能力 / 浏览器支持等条目增减变化；二级为绝对定位，不影响一级尺寸
let menuResizeObserver: ResizeObserver | null = null;
watch(
  menuRef,
  (el) => {
    menuResizeObserver?.disconnect();
    menuResizeObserver = null;
    if (!el || typeof ResizeObserver === 'undefined') return;
    menuResizeObserver = new ResizeObserver(() => measureSubmenu());
    menuResizeObserver.observe(el);
  },
  { flush: 'post' }
);
watch(isMobile, () => measureSubmenu(), { flush: 'post' });

watch(
  () => props.open,
  (open) => {
    if (!open) onMenuPointerLeave();
  }
);

onMounted(() => window.addEventListener('resize', measureSubmenu));
onBeforeUnmount(() => {
  window.removeEventListener('resize', measureSubmenu);
  menuResizeObserver?.disconnect();
  clearHoverTimer();
});
</script>

<style scoped>
/* ===== 容器：一级 / 二级同一套浮层外观 ===== */
.quick-menu,
.quick-submenu {
  display: flex;
  flex-direction: column;
  gap: 2px; /* 行间留缝，相邻 hover 底色不粘连 */
  width: 224px;
  padding: 4px;
  background: var(--popover-surface);
  border: 1px solid var(--popover-border);
  border-radius: 12px;
  box-shadow: var(--popover-shadow);
}

.quick-menu {
  position: absolute;
  left: 0;
  bottom: calc(100% + 8px);
  z-index: 30;
  pointer-events: auto;
  transform-origin: left bottom;
}

.quick-submenu {
  position: absolute;
  left: calc(100% + 6px);
  bottom: -1px; /* 与一级下边对齐（抵消一级边框），向上生长 */
  z-index: 31;
  max-height: calc(100vh - 16px); /* 兜底；实际上限由脚本按一级高度 150% 写入 */
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.quick-menu__view {
  display: flex;
  flex-direction: column;
  gap: 2px;
  /* 正常高度下不会触发；仅极矮窗口兜底 */
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  scrollbar-width: none;
}

.quick-submenu::-webkit-scrollbar,
.quick-menu__view::-webkit-scrollbar {
  display: none;
}

/* ===== 行 ===== */
.quick-menu__item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  width: 100%;
  height: 32px;
  padding: 0 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  -webkit-appearance: none;
  appearance: none;
  -webkit-tap-highlight-color: transparent;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease;
}

.quick-menu__item:focus {
  outline: none;
}

.quick-menu__item:focus-visible,
.quick-menu__item:hover:not(:disabled),
.quick-menu__item.is-open {
  background: var(--hover-bg);
}

.quick-menu__item:disabled {
  cursor: not-allowed;
}

.quick-menu__item:disabled > * {
  opacity: 0.4;
}

.quick-menu__icon {
  --icon-size: 16px;
  flex: none;
  color: var(--text-secondary);
}

.quick-menu__item:hover:not(:disabled) .quick-menu__icon,
.quick-menu__item.is-open .quick-menu__icon {
  color: var(--text-primary);
}

.quick-menu__label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.quick-menu__meta {
  flex: 0 1 auto;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--text-tertiary);
}

.quick-menu__dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--state-success);
}

.quick-menu__chevron {
  --icon-size: 14px;
  flex: none;
  color: var(--text-tertiary);
  transform: rotate(-90deg);
}

.quick-menu__sep {
  flex: none;
  height: 1px;
  margin: 2px 8px;
  background: var(--popover-border);
}

.quick-menu__status {
  display: flex;
  align-items: center;
  height: 32px;
  padding: 0 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

/* ===== 开关（纯展示，点击整行切换） ===== */
.quick-menu__switch {
  position: relative;
  flex: none;
  width: 26px;
  height: 16px;
  border-radius: 999px;
  background: var(--popover-switch-track);
  transition: background 0.15s ease;
}

.quick-menu__switch::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--popover-switch-knob);
  box-shadow: 0 1px 2px var(--shadow-color);
  transition: transform 0.15s ease;
}

.quick-menu__switch.is-on {
  background: var(--popover-switch-on);
}

.quick-menu__switch.is-on::after {
  transform: translateX(10px);
}

.quick-menu__item.is-locked .quick-menu__switch {
  opacity: 0.45;
}

/* ===== 手机端：二级替换一级，返回行 ===== */
.quick-menu.is-mobile {
  width: min(260px, calc(100vw - 32px));
}

.quick-menu.is-mobile .quick-menu__item {
  height: 36px; /* 触屏热区略放大，仍固定高度 */
}

.quick-menu.is-mobile .quick-menu__view,
.quick-menu.is-mobile .quick-submenu {
  max-height: calc(100vh - 160px);
}

.quick-menu.is-mobile .quick-submenu {
  position: static;
  width: auto;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.quick-menu__back {
  color: var(--text-secondary);
}

.quick-menu__back .quick-menu__label {
  color: var(--text-primary);
  font-weight: 600;
}

/* ===== 动画：出现 / 收起互为反向 ===== */
/* 一级：从 + 按钮方向升起，收起时落回 */
.qm-pop-enter-active {
  transition:
    opacity 0.14s cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 0.14s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.qm-pop-leave-active {
  transition:
    opacity 0.1s cubic-bezier(0.4, 0, 1, 1),
    transform 0.1s cubic-bezier(0.4, 0, 1, 1);
}

.qm-pop-enter-from,
.qm-pop-leave-to {
  opacity: 0;
  transform: translateY(4px) scale(0.98);
}

/* 二级（桌面）：从一级一侧滑出，收起时滑回一级 */
.qm-sub-enter-active {
  transition:
    opacity 0.12s cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 0.12s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.qm-sub-leave-active,
.quick-menu:not(.is-mobile).qm-pop-leave-active .quick-submenu {
  transition:
    opacity 0.1s cubic-bezier(0.4, 0, 1, 1),
    transform 0.1s cubic-bezier(0.4, 0, 1, 1);
}

.qm-sub-enter-from,
.qm-sub-leave-to,
.quick-menu:not(.is-mobile).qm-pop-leave-active .quick-submenu {
  opacity: 0;
  transform: translateX(-6px);
}

/* 手机端视图切换：进入二级从右侧推入，返回一级从左侧推回（离开的视图立即移除） */
.qm-view-fwd-enter-active,
.qm-view-back-enter-active {
  transition:
    opacity 0.16s cubic-bezier(0.2, 0.8, 0.2, 1),
    transform 0.16s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.qm-view-fwd-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.qm-view-back-enter-from {
  opacity: 0;
  transform: translateX(-12px);
}
</style>
