<script setup lang="ts">
/**
 * 顶栏模型弹窗内容（DESIGN.md §7「主对话页 · 顶栏模型选择」，2026-11-18）：
 * 单列——上方模型选择（搜索 + 提供商分组），下方推理强度滑块（最左「无」= 快速模式，
 * 其余为思考强度），底部一左一右「默认推理」「高速模式」开关。
 * Agent 页（桌面 / 手机）与 Chat 模式共用；外层定位与进出动画由 .model-mode-dropdown 负责。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import EffortSlider from '@/components/input/EffortSlider.vue';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiSwitch from '@/components/ui/UiSwitch.vue';
import type { ReasoningEffort } from '@/stores/personalization';
import {
  currentReasoningStop,
  fallbackLevel,
  reasoningStops,
  type ReasoningCapability,
  type ReasoningChange,
  type ReasoningStop
} from '@/utils/reasoningStops';

const props = defineProps<{
  mobile?: boolean;
  isConnected: boolean;
  streamingMessage: boolean;
  modelMenuSearchQuery: string;
  filteredGroupedModelOptions: any[];
  isModelGroupCollapsed: (id: string) => boolean;
  toggleModelGroup: (id: string) => void;
  currentModelKey: string;
  handleHeaderModelSelect: (key: string, disabled?: boolean) => void;
  hasAnyModels: boolean;
  openSettingsProviders: () => void;
  openManageModels: () => void;
  reasoning: ReasoningCapability;
  thinking: boolean;
  reasoningEffort: ReasoningEffort | null;
  setReasoning: (change: ReasoningChange) => unknown;
  isChatgptModel: boolean;
  supportsChatgptFastMode: boolean;
  chatgptFastMode: boolean;
  chatgptFastModeSaving?: boolean;
  toggleChatgptFastMode: () => void;
}>();
const emit = defineEmits<{ (event: 'update:modelMenuSearchQuery', value: string): void }>();

const root = ref<HTMLElement | null>(null);
const list = ref<HTMLElement | null>(null);
const slider = ref<InstanceType<typeof EffortSlider> | null>(null);

const query = computed({
  get: () => props.modelMenuSearchQuery,
  set: (value: string) => emit('update:modelMenuSearchQuery', value)
});
const searching = computed(() => !!props.modelMenuSearchQuery.trim());
const isCollapsed = (id: string) => !searching.value && props.isModelGroupCollapsed(id);

// ---- 推理 ----
// 「默认推理」打开时滑块停在记忆档位（置灰），关闭后回到该档
const memory = ref<ReasoningEffort | null>(
  fallbackLevel(props.reasoning.levels, props.reasoningEffort)
);
watch(
  () => props.reasoningEffort,
  (value) => {
    if (value && props.reasoning.levels.includes(value)) memory.value = value;
  }
);
watch(
  () => props.reasoning.levels.join(','),
  () => {
    memory.value = fallbackLevel(props.reasoning.levels, memory.value);
  }
);

const stops = computed(() => reasoningStops(props.reasoning));
const showSlider = computed(() => stops.value.length >= 2);
const currentStop = computed(() =>
  currentReasoningStop(props.reasoning, props.thinking, props.reasoningEffort, memory.value)
);
const reasoningLocked = computed(() => !props.isConnected || props.streamingMessage);
const showDefaultToggle = computed(
  () => props.reasoning.canThink && props.reasoning.levels.length > 0
);
const isDefault = computed(
  () => showDefaultToggle.value && props.thinking && props.reasoningEffort === null
);
const fastLocked = computed(
  () => reasoningLocked.value || !!props.chatgptFastModeSaving || !props.supportsChatgptFastMode
);
const showToggles = computed(() => showDefaultToggle.value || props.isChatgptModel);

async function applyReasoning(change: ReasoningChange) {
  try {
    await props.setReasoning(change);
  } finally {
    // 保存失败或被拒（如 thinkingOnly）时，滑块回到真实状态
    await nextTick();
    slider.value?.sync();
  }
}
function onStop(stop: ReasoningStop) {
  if (stop === 'none') return applyReasoning({ thinking: false });
  if (stop === 'on') return applyReasoning({ thinking: true });
  memory.value = stop;
  return applyReasoning({ thinking: true, effort: stop });
}
function toggleDefault() {
  if (reasoningLocked.value) return;
  return applyReasoning(
    isDefault.value ? { thinking: true, effort: memory.value } : { thinking: true, effort: null }
  );
}
function toggleFast() {
  if (fastLocked.value) return;
  props.toggleChatgptFastMode();
}

// 打开时把已选模型滚到列表中部（不聚焦搜索框）；变换原点对准触发器
onMounted(() => {
  const box = list.value;
  const item = box?.querySelector<HTMLElement>('[aria-selected="true"]');
  if (box && item) box.scrollTop = item.offsetTop - box.clientHeight / 2 + item.offsetHeight / 2;
  const shell = root.value?.parentElement;
  const trigger = Array.from(
    document.querySelectorAll<HTMLElement>('[data-model-picker-trigger][aria-expanded="true"]')
  ).find((el) => el.getClientRects().length > 0);
  if (shell && trigger) {
    const a = trigger.getBoundingClientRect();
    const b = shell.getBoundingClientRect();
    shell.style.transformOrigin = `${Math.round(a.left + a.width / 2 - b.left)}px top`;
  }
});
</script>

<template>
  <div ref="root" class="mep" :class="{ 'mep--mobile': mobile }" @click.stop>
    <div class="mep-models" data-tutorial="header-model-options">
      <label class="mep-search">
        <UiIcon name="search" />
        <input
          v-model="query"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :placeholder="$t('appCore.searchModel')"
          :aria-label="$t('appCore.searchModel')"
        />
      </label>
      <div ref="list" class="mep-list" role="listbox" :aria-label="$t('appCore.searchModel')">
        <section v-for="group in filteredGroupedModelOptions" :key="group.id" class="mep-group">
          <button
            type="button"
            class="mep-group-head"
            :aria-expanded="!isCollapsed(group.id)"
            @click="toggleModelGroup(group.id)"
          >
            <span class="mep-label">{{ group.name }}</span>
            <UiIcon name="chevron-down" class="mep-group-chevron" />
          </button>
          <div
            class="mep-group-body"
            :class="{ 'is-collapsed': isCollapsed(group.id) }"
            :inert="isCollapsed(group.id) || undefined"
          >
            <div class="mep-group-clip">
              <div class="mep-group-options">
                <button
                  v-for="option in group.options"
                  :key="option.key"
                  type="button"
                  class="mep-option"
                  role="option"
                  :aria-selected="option.key === currentModelKey"
                  :disabled="streamingMessage || !isConnected || option.disabled"
                  @click="handleHeaderModelSelect(option.key, option.disabled)"
                >
                  <span class="mep-label">{{ option.label }}</span>
                  <UiIcon v-if="option.key === currentModelKey" name="check" class="mep-check" />
                </button>
              </div>
            </div>
          </div>
        </section>
        <div v-if="!filteredGroupedModelOptions.length" class="mep-empty">
          <template v-if="hasAnyModels">{{ $t('appCore.noModelsMatched') }}</template>
          <template v-else>
            <div>{{ $t('appCore.noModelsConfigured') }}</div>
            <button type="button" class="mep-empty-action" @click="openSettingsProviders">
              {{ $t('appCore.goAddProvider') }}
            </button>
          </template>
        </div>
      </div>
      <button type="button" class="mep-option mep-manage" @click="openManageModels">
        <svg class="mep-manage-icon" viewBox="0 0 18 18" aria-hidden="true">
          <g fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round">
            <line x1="2" y1="5" x2="16" y2="5" />
            <line x1="2" y1="9" x2="16" y2="9" />
            <line x1="2" y1="13" x2="16" y2="13" />
            <circle cx="6.5" cy="5" r="1.8" fill="var(--popover-surface)" />
            <circle cx="11.5" cy="9" r="1.8" fill="var(--popover-surface)" />
            <circle cx="7.5" cy="13" r="1.8" fill="var(--popover-surface)" />
          </g>
        </svg>
        <span class="mep-label">{{ $t('appCore.manageModels') }}</span>
      </button>
    </div>

    <div class="mep-reasoning" data-tutorial="header-runmode-options">
      <div v-if="showSlider || showToggles" class="mep-sep" aria-hidden="true"></div>
      <div v-if="showSlider" class="mep-effort">
        <EffortSlider
          ref="slider"
          :stops="stops"
          :model-value="currentStop"
          :dimmed="isDefault"
          :disabled="reasoningLocked"
          @update:model-value="onStop"
        />
      </div>
      <div v-if="showToggles" class="mep-toggles">
        <div
          v-if="showDefaultToggle"
          class="mep-toggle"
          :class="{ 'is-disabled': reasoningLocked }"
        >
          <span class="mep-toggle-label" @click="toggleDefault">{{
            $t('appCore.defaultReasoning')
          }}</span>
          <UiSwitch
            :checked="isDefault"
            :disabled="reasoningLocked"
            :label="$t('appCore.defaultReasoning')"
            @toggle="toggleDefault"
          />
        </div>
        <div
          v-if="isChatgptModel"
          class="mep-toggle mep-toggle--end"
          :class="{ 'is-disabled': fastLocked }"
        >
          <span class="mep-toggle-label" @click="toggleFast">{{
            $t('appCore.chatgptFastModeShort')
          }}</span>
          <UiSwitch
            :checked="chatgptFastMode && supportsChatgptFastMode"
            :disabled="fastLocked"
            :label="$t('appCore.chatgptFastMode')"
            @toggle="toggleFast"
          />
          <span class="mep-tip" aria-hidden="true">{{ $t('appCore.chatgptFastModeDesc') }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 弹窗本体：单列，模型列表是唯一滚动区（外层 .model-mode-dropdown 给出浮层表面与最大高度） */
.mep {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  font-family:
    -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  color: var(--text-primary);
}

.mep-models {
  display: flex;
  flex-direction: column;
  flex: 0 1 auto;
  min-height: 0;
}

.mep-reasoning {
  flex: none;
}

/* ---- 搜索：同 UiModelSelect（32 高、R8、12px、卡内输入框底） ---- */
.mep-search {
  height: 32px;
  margin-bottom: 4px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  background-color: var(--group-card-control);
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: text;
  transition: border-color 140ms ease;
}

.mep-search:hover {
  background-image: linear-gradient(var(--hover-bg), var(--hover-bg));
}

.mep-search:focus-within {
  background-image: none;
  border-color: var(--border-strong);
}

.mep-search > .ui-icon {
  width: 14px;
  height: 14px;
  color: var(--text-tertiary);
}

.mep-search input {
  flex: 1;
  min-width: 0;
  height: 28px;
  padding: 0;
  border: 0;
  outline: none;
  background: transparent;
  box-shadow: none;
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
}

.mep-search input::placeholder {
  color: var(--text-tertiary);
}

/* ---- 模型列表 ---- */
.mep-list {
  flex: 0 1 auto;
  min-height: 64px;
  max-height: 300px;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.mep--mobile .mep-list {
  max-height: 260px;
}

.mep-list::-webkit-scrollbar {
  width: 4px;
}

.mep-list::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 4px;
}

/* 组头吸顶（约束盒为组容器，滚到下一组时被推走），不透明底色遮住滚过的选项；hover 只变文字色 */
.mep-group-head {
  position: sticky;
  top: 0;
  z-index: 1;
  width: 100%;
  height: 28px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: 7px;
  background: var(--popover-surface);
  color: var(--text-secondary);
  font: inherit;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
  transition: color 120ms ease;
}

.mep-group-head:hover,
.mep-group-head:focus-visible {
  outline: none;
  color: var(--text-primary);
}

.mep-group-chevron {
  width: 11px;
  height: 11px;
  color: var(--text-tertiary);
  transition: transform 160ms ease;
}

.mep-group-head[aria-expanded='false'] .mep-group-chevron {
  transform: rotate(-90deg);
}

/* 折叠：高度与透明度同时过渡 */
.mep-group-body {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  transition:
    grid-template-rows 200ms cubic-bezier(0.2, 0.8, 0.2, 1),
    opacity 160ms ease;
}

.mep-group-body.is-collapsed {
  grid-template-rows: 0fr;
  opacity: 0;
}

.mep-group-clip {
  min-height: 0;
  overflow: hidden;
}

.mep-group-options {
  display: grid;
  gap: 2px;
  padding: 2px 0 4px;
}

/* 选项 §6.4：32 高、R8（= 外框 12 − 内边距 4）、行间 2 */
.mep-option {
  width: 100%;
  height: 32px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 120ms ease;
}

.mep-option:not(:disabled):hover,
.mep-option:focus-visible {
  outline: none;
  background: var(--hover-bg);
}

.mep-option:disabled {
  opacity: 0.4;
  cursor: default;
}

.mep-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mep-check {
  width: 14px;
  height: 14px;
  color: var(--text-primary);
}

.mep-manage {
  margin-top: 2px;
  color: var(--text-secondary);
}

.mep-manage-icon {
  width: 16px;
  height: 16px;
  flex: none;
  color: var(--text-tertiary);
}

.mep-manage:hover,
.mep-manage:hover .mep-manage-icon {
  color: var(--text-primary);
}

.mep-empty {
  padding: 14px 8px;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

.mep-empty-action {
  height: 28px;
  margin-top: 8px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: var(--group-card-control);
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.mep-empty-action:hover {
  background-image: linear-gradient(var(--hover-bg), var(--hover-bg));
}

/* 分隔线 §6.4：1px，上下 2、左右 8 */
.mep-sep {
  height: 1px;
  margin: 2px 8px;
  background: var(--popover-border);
}

/* ---- 推理强度 ---- */
.mep-effort {
  padding: 2px 8px 0;
}

/* ---- 底部开关：一左一右 ----
   UiSwitch 关闭轨道在浮层上改用 --popover-switch-track（经典/浅色同值，深色 #2a2a2a 落在
   #262626 浮层上几乎不可见）；开启仍为系统蓝 */
.mep-toggles {
  --switch-track: var(--popover-switch-track);
  height: 36px;
  margin-top: 6px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.mep-toggle {
  position: relative;
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.mep-toggle--end {
  margin-left: auto;
}

.mep-toggle-label {
  color: var(--text-primary);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
}

.mep-toggle.is-disabled .mep-toggle-label {
  opacity: 0.4;
  cursor: default;
}

/* 高速模式说明：悬停 400ms 后出现的浮层提示（不用原生 tooltip） */
.mep-tip {
  position: absolute;
  right: -8px;
  bottom: calc(100% + 6px);
  z-index: 3;
  width: 200px;
  padding: 8px 10px;
  border: 1px solid var(--popover-border);
  border-radius: 8px;
  background: var(--popover-surface);
  box-shadow: var(--popover-shadow);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
  opacity: 0;
  transform: translateY(4px);
  pointer-events: none;
  transition:
    opacity 100ms cubic-bezier(0.4, 0, 1, 1),
    transform 100ms cubic-bezier(0.4, 0, 1, 1);
}

.mep-toggle:hover .mep-tip {
  opacity: 1;
  transform: translateY(0);
  transition:
    opacity 140ms cubic-bezier(0.2, 0.8, 0.2, 1) 400ms,
    transform 140ms cubic-bezier(0.2, 0.8, 0.2, 1) 400ms;
}

@media (hover: none) {
  .mep-tip {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mep-group-body,
  .mep-group-chevron,
  .mep-tip,
  .mep-toggle:hover .mep-tip {
    transition: none;
  }
}
</style>
