<script setup lang="ts">
/**
 * 模型选择：结构沿用设置页 ModelSelectDropdown（顶部搜索 + 提供商分组可折叠 + 单行模型），
 * 外观使用定时任务页面的弹层样式。
 */
import { computed, onMounted, ref } from 'vue';
import { t, currentLocale } from '@/locales';
import { useModelStore } from '@/stores/model';
import { filterModelGroups, groupModelOptions, type ModelGroupOption } from '@/utils/modelGroups';
import ScheduleIcon from './ScheduleIcon.vue';
import SchedulePopover from './SchedulePopover.vue';

const props = defineProps<{
  modelValue: string;
  models: { value: string; label: string }[];
  label: string;
}>();
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void }>();
const modelStore = useModelStore();
const trigger = ref<HTMLButtonElement>();
const open = ref(false);
const query = ref('');
const collapsed = ref<string[]>([]);

// 计划接口只给出 key/label；提供商分组从主模型库按 key 反查补齐。
const options = computed<ModelGroupOption[]>(() => {
  const info = new Map<string, { providerId: string; providerName: string }>();
  for (const model of modelStore.allModels || []) {
    if (model.providerId)
      info.set(model.key, {
        providerId: String(model.providerId),
        providerName: String(model.providerName || model.providerId)
      });
  }
  return props.models.map((item) => ({
    key: item.value,
    label: item.label,
    ...(info.get(item.value) || {})
  }));
});
const groups = computed(() => {
  void currentLocale.value;
  return filterModelGroups(
    groupModelOptions(options.value, { custom: t('appCore.modelGroupCustom') }),
    query.value
  );
});
const searching = computed(() => !!query.value.trim());
const text = computed(
  () => props.models.find((item) => item.value === props.modelValue)?.label || props.modelValue
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
  if (restoreFocus) trigger.value?.focus({ preventScroll: true });
}
function pick(key: string) {
  close(true);
  emit('update:modelValue', key);
}
onMounted(() => {
  if (!modelStore.allModels?.length) modelStore.fetchModels().catch(() => undefined);
});
</script>
<template>
  <button
    ref="trigger"
    type="button"
    class="sch-select-trigger"
    aria-haspopup="dialog"
    :aria-expanded="open"
    :aria-label="label"
    @click="toggle"
  >
    <span class="sch-select-label">{{ text }}</span>
    <ScheduleIcon name="chevron-down" class="sch-select-chevron" />
  </button>
  <SchedulePopover
    :open="open"
    :anchor="trigger"
    role="dialog"
    :label="label"
    :min-width="240"
    focus-selector="input"
    panel-class="sch-model-popover"
    @close="close"
  >
    <div class="sch-model-search">
      <ScheduleIcon name="search" />
      <input
        v-model="query"
        type="text"
        :placeholder="t('appCore.searchModel')"
        :aria-label="t('appCore.searchModel')"
        spellcheck="false"
      />
    </div>
    <div class="sch-model-list">
      <section v-for="group in groups" :key="group.id" class="sch-model-group">
        <button
          type="button"
          class="sch-model-group-heading"
          :aria-expanded="!isCollapsed(group.id)"
          @click="toggleGroup(group.id)"
        >
          <span class="sch-model-group-name">{{ group.name }}</span>
          <ScheduleIcon name="chevron-down" class="sch-group-chevron" />
        </button>
        <div
          class="sch-group-items"
          :class="{ 'is-collapsed': isCollapsed(group.id) }"
          :inert="isCollapsed(group.id) || undefined"
        >
          <div class="sch-group-items-clip">
            <div class="sch-model-options">
              <button
                v-for="option in group.options"
                :key="option.key"
                type="button"
                class="sch-popover-option"
                role="option"
                :aria-selected="option.key === modelValue"
                data-popover-item
                @click="pick(option.key)"
              >
                <span class="sch-popover-label">{{ option.label }}</span>
                <ScheduleIcon
                  v-if="option.key === modelValue"
                  name="check"
                  class="sch-option-check"
                />
              </button>
            </div>
          </div>
        </div>
      </section>
      <div v-if="!groups.length" class="sch-model-empty">{{ t('appCore.noModelsMatched') }}</div>
    </div>
  </SchedulePopover>
</template>
