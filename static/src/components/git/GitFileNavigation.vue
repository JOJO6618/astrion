<script setup lang="ts">
import { computed } from 'vue';
import { t } from '@/locales';
import GitIcon from './GitIcon.vue';
import type { ChangedFile } from './api';
const props = defineProps<{ files: ChangedFile[]; selected: string }>();
const emit = defineEmits<{ (event: 'select', path: string): void }>();
const groups = computed(() => {
  const directories = new Map<string, ChangedFile[]>();
  for (const file of props.files) {
    const directory = file.path.includes('/') ? file.path.slice(0, file.path.lastIndexOf('/')) : '';
    const entries = directories.get(directory) || [];
    entries.push(file);
    directories.set(directory, entries);
  }
  return Array.from(directories, ([directory, files]) => ({ directory, files }));
});
function name(path: string) {
  return path.slice(path.lastIndexOf('/') + 1);
}
function label(file: ChangedFile) {
  return `${file.path}, ${t(`git.fileStatus.${file.status}`)}`;
}
</script>

<template>
  <aside id="git-file-navigation" class="git-shell__files" :aria-label="$t('git.changedFiles')">
    <div class="git-files__heading">{{ $t('git.changedFiles') }}</div>
    <div v-for="group in groups" :key="group.directory" class="git-files__group">
      <div class="git-files__directory">
        <GitIcon name="chevron" /><span>{{ group.directory || $t('git.rootDirectory') }}</span>
      </div>
      <button
        v-for="file in group.files"
        :key="file.path"
        type="button"
        class="git-files__file"
        :aria-pressed="selected === file.path"
        :aria-label="label(file)"
        @click="emit('select', file.path)"
      >
        <GitIcon name="file" /><span class="git-files__name">{{ name(file.path) }}</span
        ><span class="git-files__status" :class="`git-files__status--${file.status}`">{{
          file.status
        }}</span>
      </button>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.git-files__heading {
  height: 38px;
  display: flex;
  align-items: center;
  padding: 0 16px;
  color: var(--text-muted);
  font-size: 11px;
}
.git-files__directory {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 13px;
  color: var(--text-muted);
  font-size: 10px;
}
.git-files__directory .git-icon {
  width: 12px;
  height: 12px;
}
.git-files__directory span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.git-files__file {
  width: 100%;
  height: 34px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px 0 17px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
  font-size: 11px;
}
.git-files__file:hover,
.git-files__file[aria-pressed='true'] {
  background: var(--hover-bg);
}
.git-files__file .git-icon {
  width: 13px;
  height: 13px;
  color: var(--text-muted);
}
.git-files__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-files__status {
  font-family: var(--font-mono, monospace);
  font-size: 10px;
  color: var(--text-muted);
}
.git-files__status--A {
  color: var(--state-success);
}
.git-files__status--D {
  color: var(--state-danger);
}
.git-files__status--R {
  color: var(--text-secondary);
}
</style>
