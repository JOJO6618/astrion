<script setup lang="ts">
/**
 * 统一模型选择下拉（设置页 / 个人空间共用）。
 * 形态对齐输入栏模型菜单（opencode 式）：顶部搜索框 + 提供商分组 sticky 组头 + 单行模型行。
 * 浮层复用全局 .settings-floating-menu 视觉（背景/边框/阴影/dark 覆盖/细滚动条），
 * 组件自管开合与 fixed 定位，不占用 context 的 activeDropdown。
 */
import { ref, computed, inject, nextTick, onBeforeUnmount } from 'vue';
import { t, currentLocale } from '@/locales';
import { useModelStore } from '@/stores/model';
import { groupModelOptions, filterModelGroups, type ModelGroupOption } from '@/utils/modelGroups';

const props = withDefaults(
  defineProps<{
    /** 当前选中的模型 key（'' 表示未设置/默认） */
    modelValue: string;
    /** 候选模型（需带 key/label，可带 providerId/providerName/disabled） */
    options: ModelGroupOption[];
    /** 列表顶部额外项（如「默认」），key 通常为空串；参与搜索过滤，不参与分组 */
    extraOptions?: Array<{ key: string; label: string }>;
    /** modelValue 为空且无 extraOptions 命中时按钮显示的文字 */
    placeholder?: string;
  }>(),
  { extraOptions: () => [], placeholder: '' }
);

const emit = defineEmits<{ select: [value: string] }>();

// 可选拿到共享上下文：打开本下拉时关闭其它旧式下拉，保证同时只开一个
const ctx = inject<Record<string, any> | null>('personalizationDrawer', null);

const open = ref(false);
const query = ref('');
const collapsedGroups = ref<string[]>([]);
const rootRef = ref<HTMLElement | null>(null);
const menuRef = ref<HTMLElement | null>(null);
const menuStyle = ref<Record<string, string>>({});

// 组折叠：与输入栏模型菜单同语义——点击组头折叠/展开，搜索时强制展开
const isGroupCollapsed = (groupId: string) => collapsedGroups.value.includes(groupId);
const toggleGroup = (groupId: string) => {
  const idx = collapsedGroups.value.indexOf(groupId);
  if (idx >= 0) {
    collapsedGroups.value.splice(idx, 1);
  } else {
    collapsedGroups.value.push(groupId);
  }
};

// 辅助数据源（子智能体/审核/标题模型）的选项不带 provider 分组信息，
// 按 key 从主库全量列表反查补齐（allModels 不受用户 hidden_models 影响，保证分组完整）
const modelStore = useModelStore();
const enrichedOptions = computed(() => {
  const needEnrich = props.options.some((o) => !o.providerId);
  if (!needEnrich) return props.options;
  const infoByKey = new Map<string, { providerId: string; providerName: string }>();
  for (const m of modelStore.allModels || []) {
    if (m.providerId) {
      infoByKey.set(m.key, {
        providerId: String(m.providerId),
        providerName: String(m.providerName || m.providerId)
      });
    }
  }
  return props.options.map((o) => {
    if (o.providerId) return o;
    const info = infoByKey.get(o.key);
    return info ? { ...o, ...info } : o;
  });
});

const groups = computed(() => {
  void currentLocale.value;
  return groupModelOptions(enrichedOptions.value, {
    custom: t('appCore.modelGroupCustom')
  });
});

const filteredGroups = computed(() => filterModelGroups(groups.value, query.value));

const filteredExtras = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.extraOptions;
  return props.extraOptions.filter((o) => o.label.toLowerCase().includes(q));
});

const isEmpty = computed(() => !filteredExtras.value.length && !filteredGroups.value.length);

const buttonLabel = computed(() => {
  const v = props.modelValue;
  if (v) {
    const found = props.options.find((o) => o.key === v);
    return found?.label || v;
  }
  const extra = props.extraOptions.find((o) => o.key === '');
  return extra?.label || props.placeholder || t('common.unset');
});

const BASE_MENU_HEIGHT = 320;

const updatePosition = async () => {
  if (!open.value || typeof window === 'undefined') return;
  await nextTick();
  const button = rootRef.value?.querySelector<HTMLElement>('.settings-select-button');
  if (!button) return;
  const rect = button.getBoundingClientRect();
  const menuWidth = Math.min(300, Math.max(240, window.innerWidth - 32));
  const left = Math.max(16, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 16));

  const padding = 16;
  const gap = 6;
  // 菜单已渲染（默认 max-height 320），量实际高度做翻转判断
  const menuHeight = menuRef.value?.getBoundingClientRect().height || BASE_MENU_HEIGHT;
  const spaceBelow = window.innerHeight - rect.bottom - padding;
  const spaceAbove = rect.top - padding;

  let top: number;
  let maxHeight: number | undefined;
  if (spaceBelow >= menuHeight + gap) {
    top = rect.bottom + gap;
  } else if (spaceAbove >= menuHeight + gap) {
    top = rect.top - menuHeight - gap;
  } else if (spaceBelow >= spaceAbove) {
    maxHeight = Math.max(120, spaceBelow - gap);
    top = rect.bottom + gap;
  } else {
    maxHeight = Math.max(120, spaceAbove - gap);
    top = Math.max(padding, rect.top - maxHeight - gap);
  }

  menuStyle.value = {
    top: `${Math.round(top)}px`,
    left: `${Math.round(left)}px`,
    width: `${Math.round(menuWidth)}px`,
    ...(maxHeight !== undefined ? { maxHeight: `${Math.round(maxHeight)}px` } : {})
  };
};

const onDocumentPointerDown = (event: MouseEvent) => {
  const target = event.target as Node | null;
  if (!target) return;
  if (rootRef.value?.contains(target) || menuRef.value?.contains(target)) return;
  close();
};

const onDocumentKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape') close();
};

const attachListeners = () => {
  document.addEventListener('mousedown', onDocumentPointerDown, true);
  document.addEventListener('keydown', onDocumentKeydown, true);
  window.addEventListener('resize', updatePosition);
  window.addEventListener('scroll', updatePosition, true);
};

const detachListeners = () => {
  document.removeEventListener('mousedown', onDocumentPointerDown, true);
  document.removeEventListener('keydown', onDocumentKeydown, true);
  window.removeEventListener('resize', updatePosition);
  window.removeEventListener('scroll', updatePosition, true);
};

const scrollToSelected = async () => {
  await nextTick();
  const selected = menuRef.value?.querySelector<HTMLElement>('.model-select-option.selected');
  selected?.scrollIntoView({ block: 'nearest' });
};

const toggle = async () => {
  if (open.value) {
    close();
    return;
  }
  open.value = true;
  query.value = '';
  collapsedGroups.value = [];
  menuStyle.value = {};
  // 关闭其它旧式下拉，保证同时只开一个
  ctx?.closeDropdown?.();
  attachListeners();
  await updatePosition();
  await scrollToSelected();
};

const close = () => {
  if (!open.value) return;
  open.value = false;
  detachListeners();
};

const pick = (value: string, disabled?: boolean) => {
  if (disabled) return;
  emit('select', value);
  close();
};

onBeforeUnmount(detachListeners);
</script>

<template>
  <div ref="rootRef" class="settings-select-wrap model-select" :class="{ open }" @click.stop>
    <button type="button" class="settings-select-button" @click="toggle">
      <span class="model-select-button-label">{{ buttonLabel }}</span>
      <span class="select-chevron" aria-hidden="true"></span>
    </button>
    <div
      v-if="open"
      ref="menuRef"
      class="settings-floating-menu model-select-menu"
      :style="menuStyle"
      @click.stop
    >
      <div class="model-select-search">
        <svg class="model-select-search-icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          v-model="query"
          type="text"
          class="model-select-search-input"
          :placeholder="$t('appCore.searchModel')"
        />
      </div>
      <div class="model-select-list">
        <button
          v-for="extra in filteredExtras"
          :key="`extra:${extra.key}`"
          type="button"
          class="model-select-option"
          :class="{ selected: modelValue === extra.key }"
          @click="pick(extra.key)"
        >
          <span class="model-select-option-label">{{ extra.label }}</span>
          <svg v-if="modelValue === extra.key" class="model-select-check" viewBox="0 0 24 24">
            <path d="M5 12.5 9.5 17 19 7" />
          </svg>
        </button>
        <div v-for="group in filteredGroups" :key="group.id" class="model-select-group">
          <button
            type="button"
            class="model-select-group-header"
            :aria-expanded="!isGroupCollapsed(group.id) || !!query.trim()"
            @click.stop="toggleGroup(group.id)"
          >
            <span class="model-select-group-name">{{ group.name }}</span>
            <svg
              class="model-select-group-chevron"
              :class="{ collapsed: isGroupCollapsed(group.id) && !query.trim() }"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path
                d="M7 10l5 5 5-5"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <div
            class="model-select-group-body"
            :class="{ collapsed: isGroupCollapsed(group.id) && !query.trim() }"
          >
            <div class="model-select-group-body__inner">
              <button
                v-for="option in group.options"
                :key="option.key"
                type="button"
                class="model-select-option"
                :class="{ selected: modelValue === option.key, disabled: option.disabled }"
                :disabled="option.disabled"
                @click="pick(option.key, option.disabled)"
              >
                <span class="model-select-option-label">{{ option.label }}</span>
                <svg
                  v-if="modelValue === option.key"
                  class="model-select-check"
                  viewBox="0 0 24 24"
                >
                  <path d="M5 12.5 9.5 17 19 7" />
                </svg>
              </button>
            </div>
          </div>
        </div>
        <div v-if="isEmpty" class="model-select-empty">{{ $t('appCore.noModelsMatched') }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.model-select {
  position: relative;
}

.model-select-button-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 浮层骨架：背景/边框/阴影/深色覆盖/滚动条全部继承全局 .settings-floating-menu，
   这里只覆写布局（flex 列：搜索固定 + 列表滚动）与默认高度 */
.model-select-menu {
  display: flex;
  flex-direction: column;
  max-height: 320px;
  padding: 6px;
}

.model-select-search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
  height: 30px;
  margin-bottom: 4px;
  padding: 0 10px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--text-primary) 5%, transparent);
}

.model-select-search-icon {
  width: 13px;
  height: 13px;
  flex: none;
  fill: none;
  stroke: var(--text-muted);
  stroke-width: 2;
  stroke-linecap: round;
}

.model-select-search-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  line-height: 1;
  outline: none;
  padding: 0;
}

.model-select-search-input::placeholder {
  color: var(--text-muted);
}

.model-select-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* 常显细滚动条（对齐输入栏模型菜单）；滚动发生在此内层容器，
     不继承全局浮层「hover 才显现」的滚动条语义 */
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--text-secondary) 32%, transparent) transparent;
}

.model-select-list::-webkit-scrollbar {
  width: 6px;
}

.model-select-list::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text-secondary) 32%, transparent);
  border-radius: 3px;
}

.model-select-list::-webkit-scrollbar-track {
  background: transparent;
}

/* 每组一个容器：sticky 组头的约束盒 = 组容器，滚动到下一组时自然被推走（挤压），
   与输入栏模型菜单同一结构 */
.model-select-group-header {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  height: 26px;
  padding: 0 10px;
  border: 0;
  /* 与浮层背景一致（sticky 需不透明遮住滚动内容）；深色下浮层被全局 !important
     覆盖为 --surface-panel，下方同步覆盖 */
  background: var(--settings-floating-menu-bg);
  font-family: inherit;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  user-select: none;
}

html[data-theme='dark'] .model-select-group-header,
body[data-theme='dark'] .model-select-group-header {
  background: var(--surface-panel);
}

.model-select-group-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select-group-chevron {
  width: 12px;
  height: 12px;
  flex: none;
  color: var(--text-muted);
  transition: transform 0.16s ease;
}

.model-select-group-chevron.collapsed {
  transform: rotate(-90deg);
}

/* 折叠动画：grid 1fr↔0fr 过渡可动画 auto 高度；visibility 随行动画，
   收起结束后隐藏（防 Tab 聚焦进折叠区），展开立即可见 */
.model-select-group-body {
  display: grid;
  grid-template-rows: 1fr;
  transition:
    grid-template-rows 0.2s ease,
    visibility 0.2s ease;
}

.model-select-group-body.collapsed {
  grid-template-rows: 0fr;
  visibility: hidden;
}

.model-select-group-body__inner {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
  min-height: 0;
}

.model-select-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
}

@media (hover: hover) {
  .model-select-option:hover {
    background: var(--settings-floating-menu-hover);
  }
}

.model-select-option.selected {
  color: var(--accent);
}

.model-select-option.disabled,
.model-select-option:disabled {
  opacity: 0.48;
  cursor: not-allowed;
}

.model-select-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-select-check {
  width: 14px;
  height: 14px;
  flex: none;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.model-select-empty {
  padding: 18px 10px;
  color: var(--text-muted);
  font-size: 12px;
  text-align: center;
}
</style>
