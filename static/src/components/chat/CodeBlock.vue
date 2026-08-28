<template>
  <div
    class="code-block-wrapper"
    :data-streaming="isStreaming ? '1' : undefined"
    data-md-code-block="1"
  >
    <div class="code-block-header">
      <span class="code-language">{{ displayLanguage }}</span>
      <button
        class="copy-code-btn"
        :class="{ copied }"
        :title="$t('chat.copyCode')"
        :aria-label="$t('chat.copyCode')"
        @click="handleCopy"
      ></button>
    </div>
    <pre><code ref="codeEl" :class="codeClass">{{ content }}</code></pre>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue';
import Prism from 'prismjs';

defineOptions({ name: 'CodeBlock' });

const props = defineProps<{
  content: string;
  language?: string;
  isStreaming?: boolean;
}>();

const codeEl = ref<HTMLElement | null>(null);
const copied = ref(false);

const displayLanguage = computed(() => props.language || 'text');

const codeClass = computed(() => {
  if (!props.language || props.language === 'text') {
    return 'language-plain';
  }
  return `language-${props.language}`;
});

function highlight() {
  nextTick(() => {
    if (!codeEl.value || typeof Prism === 'undefined') return;
    try {
      Prism.highlightElement(codeEl.value);
    } catch (error) {
      console.warn('代码高亮失败:', error);
    }
  });
}

watch(() => props.content, highlight, { immediate: true });
onMounted(highlight);

async function handleCopy(event: MouseEvent) {
  event.stopPropagation();
  if (copied.value || !props.content) return;

  try {
    await navigator.clipboard.writeText(props.content);
    copied.value = true;
    window.setTimeout(() => {
      copied.value = false;
    }, 5000);
  } catch (error) {
    console.warn('复制失败:', error);
  }
}
</script>
