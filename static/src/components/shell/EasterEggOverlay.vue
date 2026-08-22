<template>
  <div
    class="easter-egg-overlay"
    :class="{ active: state.active }"
    aria-hidden="true"
    ref="overlayRoot"
  ></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useUiStore } from '@/stores/ui';
import { useEasterEgg } from '@/composables/useEasterEgg';

const uiStore = useUiStore();
const { easterEgg: state } = storeToRefs(uiStore);
const overlayRoot = ref<HTMLElement | null>(null);
const controller = useEasterEgg();

onMounted(() => {
  controller.registerOverlayRoot(overlayRoot.value);
});

onBeforeUnmount(() => {
  controller.registerOverlayRoot(null);
});
</script>
