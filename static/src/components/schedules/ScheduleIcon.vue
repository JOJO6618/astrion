<script setup lang="ts">
import { computed } from 'vue';
const props = defineProps<{ name: string }>();
const style = computed(() => ({ '--icon-src': `url('/static/icons/${props.name}.svg')` }));
</script>
<template><span class="sch-icon" :style="style" aria-hidden="true" /></template>
