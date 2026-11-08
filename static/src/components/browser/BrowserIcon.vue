<template>
  <span class="nb-icon" :style="{ '--nb-icon': `url(${ICONS[name]})` }" aria-hidden="true"></span>
</template>
<script setup lang="ts">
import { ICONS } from '@/utils/icons';
defineProps<{ name: keyof typeof ICONS }>();
</script>
<style scoped>
.nb-icon {
  display: inline-block;
  width: 17px;
  height: 17px;
  flex: none;
  background: currentColor;
  mask: var(--nb-icon) center / contain no-repeat;
}
</style>
