<script setup lang="ts">
/**
 * 模型选择（DESIGN.md §6.5 模型模板）：UiSelectTrigger + 弹层（顶部常驻搜索 + 提供商分组）。
 * 弹层尺寸照定时任务页模型选择：内边距 6、宽度下限 240、搜索框 32 高、选项与搜索 12px；
 * 组头吸顶、点击折叠，搜索时全部展开。
 * 选项缺少提供商信息时按 key 从模型库反查补齐分组（模型库未加载时先拉取）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { t, currentLocale } from '@/locales';
import { useModelStore } from '@/stores/model';
import { filterModelGroups, groupModelOptions, type ModelGroupOption } from '@/utils/modelGroups';
import UiIcon from './UiIcon.vue';
import UiPopover from './UiPopover.vue';
import UiSelectTrigger from './UiSelectTrigger.vue';

const props = withDefaults(
  defineProps<{
    modelValue: string;
    options: ModelGroupOption[];
    /** 列表顶部不分组的额外项（如「默认」），参与搜索 */
    extraOptions?: Array<{ key: string; label: string }>;
    placeholder?: string;
    label?: string;
    alignEnd?: boolean;
  }>(),
  { extraOptions: () => [], placeholder: '', label: '', alignEnd: false }
);
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>();

const modelStore = useModelStore();
const trigger = ref<InstanceType<typeof UiSelectTrigger> | null>(null);
const list = ref<HTMLElement | null>(null);
const open = ref(false);
const query = ref('');
const collapsed = ref<string[]>([]);

const enriched = computed<ModelGroupOption[]>(() => {
  if (props.options.every((item) => item.providerId)) return props.options;
  const info = new Map<string, { providerId: string; providerName: string }>();
  for (const model of modelStore.allModels || []) {
    if (model.providerId)
      info.set(model.key, {
        providerId: String(model.providerId),
        providerName: String(model.providerName || model.providerId)
      });
  }
  return props.options.map((item) =>
    item.providerId ? item : { ...item, ...(info.get(item.key) || {}) }
  );
});
const groups = computed(() => {
  void currentLocale.value;
  return filterModelGroups(
    groupModelOptions(enriched.value, { custom: t('appCore.modelGroupCustom') }),
    query.value
  );
});
const searching = computed(() => !!query.value.trim());
const extras = computed(() => {
  const q = query.value.trim().toLowerCase();
  return q
    ? props.extraOptions.filter((item) => item.label.toLowerCase().includes(q))
    : props.extraOptions;
});
const selectedLabel = computed(
  () =>
    props.options.find((item) => item.key === props.modelValue)?.label ||
    props.extraOptions.find((item) => item.key === props.modelValue)?.label ||
    ''
);

const isCollapsed = (id: string) => !searching.value && collapsed.value.includes(id);
function toggleGroup(id: string) {
  const index = collapsed.value.indexOf(id);
  if (index >= 0) collapsed.value.splice(index, 1);
  else collapsed.value.push(id);
}

function toggle() {
  if (!open.value) {
    query.value = '';
    collapsed.value = [];
  }
  open.value = !open.value;
}
function close(restoreFocus: boolean) {
  open.value = false;
  if (restoreFocus) trigger.value?.el?.focus({ preventScroll: true });
}
function pick(key: string) {
  close(true);
  emit('update:modelValue', key);
}

onMounted(() => {
  if (!modelStore.allModels?.length) modelStore.fetchModels().catch(() => undefined);
});

// 打开时把已选项滚到列表中部（焦点由弹层放在已选项上，不进搜索框）
watch(open, async (value) => {
  if (!value) return;
  await nextTick();
  requestAnimationFrame(() => {
    const box = list.value;
    const item = box?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!box || !item) return;
    box.scrollTop = item.offsetTop - box.clientHeight / 2 + item.offsetHeight / 2;
  });
});
</script>

<template>
  <UiSelectTrigger
    ref="trigger"
    :text="selectedLabel || placeholder || t('common.unset')"
    :placeholder="!selectedLabel"
    :open="open"
    :label="label"
    haspopup="dialog"
    @click="toggle"
  />
  <UiPopover
    :open="open"
    :anchor="trigger?.el"
    role="dialog"
    :label="label"
    :min-width="240"
    :padding="6"
    :align-end="alignEnd"
    @close="close"
  >
    <div class="ui-model-search">
      <UiIcon name="search" />
      <input
        v-model="query"
        type="text"
        autocomplete="off"
        spellcheck="false"
        :placeholder="t('appCore.searchModel')"
        :aria-label="t('appCore.searchModel')"
      />
    </div>
    <div ref="list" class="ui-model-list">
      <button
        v-for="extra in extras"
        :key="`extra:${extra.key}`"
        type="button"
        class="ui-model-option"
        role="option"
        :aria-selected="extra.key === modelValue"
        data-popover-item
        @click="pick(extra.key)"
      >
        <span class="ui-model-label">{{ extra.label }}</span>
        <UiIcon v-if="extra.key === modelValue" name="check" class="ui-model-check" />
      </button>
      <section v-for="group in groups" :key="group.id" class="ui-model-group">
        <button
          type="button"
          class="ui-model-group-head"
          :aria-expanded="!isCollapsed(group.id)"
          @click="toggleGroup(group.id)"
        >
          <span class="ui-model-label">{{ group.name }}</span>
          <UiIcon name="chevron-down" class="ui-model-group-chevron" />
        </button>
        <div
          class="ui-model-group-body"
          :class="{ 'is-collapsed': isCollapsed(group.id) }"
          :inert="isCollapsed(group.id) || undefined"
        >
          <div class="ui-model-group-clip">
            <div class="ui-model-group-options">
              <button
                v-for="option in group.options"
                :key="option.key"
                type="button"
                class="ui-model-option"
                role="option"
                :aria-selected="option.key === modelValue"
                :disabled="option.disabled"
                data-popover-item
                @click="pick(option.key)"
              >
                <span class="ui-model-label">{{ option.label }}</span>
                <UiIcon v-if="option.key === modelValue" name="check" class="ui-model-check" />
              </button>
            </div>
          </div>
        </div>
      </section>
      <div v-if="!extras.length && !groups.length" class="ui-model-empty">
        {{ t('appCore.noModelsMatched') }}
      </div>
    </div>
  </UiPopover>
</template>

<style scoped>
/* 搜索框：32 高、R8、12px；样式同卡内输入框——无边框、--group-card-control 底，
   hover 叠 --hover-bg，聚焦时边框变 --border-strong */
.ui-model-search {
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
  transition: border-color 140ms ease;
}

.ui-model-search:hover {
  background-image: linear-gradient(var(--hover-bg), var(--hover-bg));
}

.ui-model-search:focus-within {
  background-image: none;
  border-color: var(--border-strong);
}

.ui-model-search > .ui-icon {
  width: 14px;
  height: 14px;
  color: var(--text-tertiary);
}

.ui-model-search input {
  min-width: 0;
  width: 100%;
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

.ui-model-search input::placeholder {
  color: var(--text-tertiary);
}

.ui-model-list {
  flex: 1;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.ui-model-list::-webkit-scrollbar {
  width: 4px;
}

.ui-model-list::-webkit-scrollbar-thumb {
  background: var(--border-strong);
  border-radius: 5px;
}

/* 组头：吸顶（约束盒为组容器，滚到下一组时被推走），不透明底色遮住滚过的选项；
   hover 只变文字色 */
.ui-model-group-head {
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

.ui-model-group-head:hover,
.ui-model-group-head:focus-visible {
  outline: none;
  color: var(--text-primary);
}

.ui-model-group-chevron {
  width: 11px;
  height: 11px;
  color: var(--text-tertiary);
  transition: transform 160ms ease;
}

.ui-model-group-head[aria-expanded='false'] .ui-model-group-chevron {
  transform: rotate(-90deg);
}

/* 折叠：高度与透明度同时过渡 */
.ui-model-group-body {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  transition:
    grid-template-rows 200ms cubic-bezier(0.2, 0.8, 0.2, 1),
    opacity 160ms ease;
}

.ui-model-group-body.is-collapsed {
  grid-template-rows: 0fr;
  opacity: 0;
}

.ui-model-group-clip {
  min-height: 0;
  overflow: hidden;
}

.ui-model-group-options {
  display: grid;
  gap: 2px;
  padding: 2px 0 4px;
}

.ui-model-option {
  width: 100%;
  height: 32px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font: inherit;
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

/* 组外的额外项（如「默认」）之间同样留 2px */
.ui-model-option + .ui-model-option {
  margin-top: 2px;
}

.ui-model-option:not(:disabled):hover,
.ui-model-option:focus-visible {
  outline: none;
  background: var(--hover-bg);
}

.ui-model-option:disabled {
  cursor: default;
}

.ui-model-option:disabled > * {
  opacity: 0.4;
}

.ui-model-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-model-check {
  width: 16px;
  height: 16px;
  flex: none;
  margin-left: auto;
  color: var(--text-secondary);
}

.ui-model-empty {
  padding: 18px 8px;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}

@media (prefers-reduced-motion: reduce) {
  .ui-model-group-body,
  .ui-model-group-chevron {
    transition: none;
  }
}
</style>
