<script setup lang="ts">
import { ICONS } from '@/utils/icons';
const props = defineProps<{ name: keyof typeof ICONS }>();
</script>

<template>
  <span
    class="quick-icon"
    aria-hidden="true"
    :style="{ '--quick-icon-url': `url(${ICONS[props.name]})` }"
  />
</template>

<style scoped>
.quick-icon {
  display: block;
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  background: currentColor;
  mask: var(--quick-icon-url) center / contain no-repeat;
}
</style>
