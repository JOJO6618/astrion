<template>
  <img
    v-if="source"
    :key="source"
    class="nb-site-icon"
    :src="source"
    alt=""
    aria-hidden="true"
    referrerpolicy="no-referrer"
    loading="lazy"
    decoding="async"
    draggable="false"
    @error="failed++"
  />
  <BrowserIcon v-else class="nb-site-icon" name="globe" />
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import BrowserIcon from './BrowserIcon.vue';
const props = defineProps<{ src?: string; url?: string }>();
const failed = ref(0);
function imageURL(value?: string) {
  if (!value || value.length > 262144) return '';
  if (/^data:image\/(?:png|jpeg|gif|webp|x-icon|vnd\.microsoft\.icon|svg\+xml)[;,]/i.test(value))
    return value;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : '';
  } catch {
    return '';
  }
}
const candidates = computed(() => {
  let fallback = '';
  try {
    const page = new URL(props.url || '');
    if (['http:', 'https:'].includes(page.protocol) && !page.username && !page.password)
      fallback = `${page.origin}/favicon.ico`;
  } catch {
    /* Blank tabs have no site icon. */
  }
  return [...new Set([imageURL(props.src), fallback].filter(Boolean))];
});
const source = computed(() => candidates.value[failed.value] || '');
watch([() => props.src, () => props.url], () => {
  failed.value = 0;
});
</script>
<style scoped>
.nb-site-icon {
  width: 15px;
  height: 15px;
  flex: none;
  object-fit: contain;
}
</style>
