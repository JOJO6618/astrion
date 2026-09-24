<script setup lang="ts">
import { computed, ref } from 'vue';

/**
 * 提供商图标（36px 方盒 + 1px 边框，对齐设计稿 .provider-icon）。
 * 图标加载失败时降级为首字母占位；mono 图标（currentColor 黑）在深色主题下反色。
 */
const props = withDefaults(
  defineProps<{
    name: string;
    iconUrl?: string | null;
    mono?: boolean;
    /** 弹窗标题等场景用小号 */
    size?: 'md' | 'sm';
  }>(),
  {
    iconUrl: null,
    mono: false,
    size: 'md'
  }
);

const loadFailed = ref(false);
const showImage = computed(() => !!props.iconUrl && !loadFailed.value);
const fallbackLetter = computed(() => (props.name || '?').trim().charAt(0).toUpperCase() || '?');
</script>

<template>
  <span class="provider-icon" :class="[`provider-icon--${size}`]" aria-hidden="true">
    <img
      v-if="showImage"
      :src="iconUrl || ''"
      :alt="name"
      :class="{ mono }"
      @error="loadFailed = true"
    />
    <span v-else class="provider-icon__letter">{{ fallbackLetter }}</span>
  </span>
</template>

<style scoped>
.provider-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  flex-shrink: 0;
  background: var(--surface-card);
  border: 1px solid var(--border-default);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.provider-icon--sm {
  width: 28px;
  height: 28px;
  border-radius: 6px;
}

.provider-icon img {
  width: 20px;
  height: 20px;
  display: block;
}

.provider-icon--sm img {
  width: 16px;
  height: 16px;
}

.provider-icon__letter {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
  line-height: 1;
}

.provider-icon--sm .provider-icon__letter {
  font-size: 12px;
}

/* mono 图标为纯黑，img 隔离文档无法继承 currentColor，深色下反转（组件内主题变体同文件） */
html[data-theme='dark'] .provider-icon img.mono {
  filter: invert(1);
}
</style>
