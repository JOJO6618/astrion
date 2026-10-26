<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, ref, unref } from 'vue';
import { t } from '@/locales';

const props = withDefaults(defineProps<{
  modelValue: string | null;
  dropdownId: string;
  options: Array<{ id: string; value: string | null; labelKey: string; descKey?: string }>;
  disabled?: boolean;
}>(), { disabled: false });
const emit = defineEmits<{ select: [value: string | null] }>();
const ctx = inject<Record<string, any>>('personalizationDrawer')!;
const root = ref<HTMLElement>();
const open = computed(() => unref(ctx.activeDropdown) === props.dropdownId);
const menuStyle = computed(() => open.value ? unref(ctx.floatingMenuStyle) : undefined);
const dark = computed(() => unref(ctx.activeTheme) === 'dark');
const label = computed(() => {
  const selected = props.options.find((option) => option.value === props.modelValue);
  return selected ? t(selected.labelKey) : t('common.unset');
});
function choose(value: string | null) {
  if (props.disabled) return;
  ctx.closeDropdown();
  emit('select', value);
}
function closeOutside(event: Event) {
  if (open.value && !root.value?.contains(event.target as Node)) ctx.closeDropdown();
}
function close() {
  if (open.value) ctx.closeDropdown();
}
onMounted(() => document.addEventListener('pointerdown', closeOutside, true));
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', closeOutside, true);
  close();
});
</script>

<template>
  <div
    ref="root"
    class="settings-select-wrap"
    :class="{ open }"
    @click.stop
    @keydown.esc.stop.prevent="close"
  >
    <button
      type="button"
      class="settings-select-button"
      :disabled="disabled"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="ctx.toggleDropdown(dropdownId)"
    >
      {{ label }}
      <span class="select-chevron" aria-hidden="true"></span>
    </button>
    <div
      class="settings-floating-menu"
      :class="{ dark }"
      :style="menuStyle"
      role="listbox"
      :aria-label="label"
    >
      <button
        v-for="option in options"
        :key="option.id"
        type="button"
        role="option"
        class="settings-menu-option"
        :class="{ selected: modelValue === option.value }"
        :aria-selected="modelValue === option.value"
        :disabled="disabled"
        @click="choose(option.value)"
      >
        <strong>{{ $t(option.labelKey) }}</strong>
        <span v-if="option.descKey">{{ $t(option.descKey) }}</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5 9.5 17 19 7" /></svg>
      </button>
    </div>
  </div>
</template>
