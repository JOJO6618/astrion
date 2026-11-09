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
  <span v-else-if="initial" class="nb-site-icon nb-site-initial" aria-hidden="true">{{
    initial
  }}</span>
  <BrowserIcon v-else class="nb-site-icon" name="globe" />
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import BrowserIcon from './BrowserIcon.vue';
/** initial: when no icon loads, show the host's first character instead of the globe. */
const props = defineProps<{ src?: string; url?: string; initial?: boolean }>();
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
const initial = computed(() => {
  if (!props.initial) return '';
  try {
    const host = new URL(props.url || '').hostname.replace(/^www\./, '').replace(/^\[/, '');
    return host ? [...host][0].toUpperCase() : '';
  } catch {
    return '';
  }
});
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
.nb-site-initial {
  display: grid;
  place-items: center;
  font-size: 15px;
  font-weight: 600;
  line-height: 1;
  color: var(--text-secondary);
}
</style>
