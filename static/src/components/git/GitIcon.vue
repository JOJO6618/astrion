<script setup lang="ts">
defineProps<{
  name:
    | 'back'
    | 'folder'
    | 'history'
    | 'refresh'
    | 'chevron'
    | 'close'
    | 'search'
    | 'copy'
    | 'branch'
    | 'file';
}>();
const paths = {
  back: 'M19 12H5m6-6-6 6 6 6',
  folder: 'M3 7V5h6l2 2h10v12H3V7Z',
  history: 'M3 4v5h5M3.6 9a9 9 0 1 1-.3 6M12 7v5l3 2',
  refresh: 'M20 4v5h-5M4 20v-5h5M4.5 9a8 8 0 0 1 13-4L20 9M4 15l2.5 4a8 8 0 0 0 13-4',
  chevron: 'm8 10 4 4 4-4',
  close: 'm6 6 12 12M18 6 6 18',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  copy: 'M9 9h11v11H9V9ZM15 5V3H3v12h2',
  branch:
    'M6 7v10m12-9c0 5-12 1-12 9M8 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM8 19a2 2 0 1 1-4 0 2 2 0 0 1 4 0ZM20 6a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  file: 'M14 3H5v18h14V8l-5-5ZM14 3v5h5'
};
</script>

<template>
  <svg
    class="git-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name]" />
  </svg>
</template>

<style scoped lang="scss">
.git-icon {
  display: block;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}
</style>
