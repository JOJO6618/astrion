<template>
  <div class="text-output">
    <div class="text-content" :class="{ 'streaming-text': action.streaming }">
      <MarkdownRenderer :content="action.content || ''" :is-streaming="action.streaming" />
    </div>
  </div>
</template>

<script setup lang="ts">
import MarkdownRenderer from '../MarkdownRenderer.vue';

defineOptions({ name: 'TextAction' });

defineProps<{
  action: any;
}>();
</script>
