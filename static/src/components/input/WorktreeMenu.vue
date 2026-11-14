<script setup lang="ts">
import { computed, ref } from 'vue';
import { useGitWorktreeStore } from '@/stores/gitWorktree';

/**
 * 输入栏项目/分支弹出菜单里的「切换工作树」入口。
 * 只负责触发：弹窗由 WorktreeDialog 单独常驻挂载，避免宿主菜单关闭时把弹窗一起卸载。
 */
const props = defineProps<{ busy: boolean }>();
const emit = defineEmits<{ (event: 'open'): void }>();
const store = useGitWorktreeStore();
const button = ref<HTMLButtonElement | null>(null);
const disabled = computed(() => props.busy || store.busy);

defineExpose({ focus: () => button.value?.focus() });
</script>

<template>
  <button
    v-if="store.enabled"
    ref="button"
    type="button"
    :disabled="disabled"
    @click.stop="emit('open')"
  >
    {{ $t('input.worktreeSwitch') }}
  </button>
</template>
