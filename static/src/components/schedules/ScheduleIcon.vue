<script setup lang="ts">
import { computed } from 'vue';
import { iconVar } from '@/utils/icons';
const props = defineProps<{ name: string }>();
const style = computed(() => ({ '--icon-src': iconVar(props.name) }));
</script>
<template><span class="sch-icon" :style="style" aria-hidden="true" /></template>
