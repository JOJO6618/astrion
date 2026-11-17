<script setup lang="ts">
/**
 * 变更文件导航（左侧卡片下半部）：标题行「变更文件 N 个 +a −d」→ 按目录分组的文件行（32 高、R8、行间 2）。
 */
import { computed } from 'vue';
import { t } from '@/locales';
import UiIcon from '@/components/ui/UiIcon.vue';
import type { ChangedFile } from './api';
const props = defineProps<{
  files: ChangedFile[];
  selected: string;
  additions: number;
  deletions: number;
}>();
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
  <div class="git-files">
    <div class="git-files__head">
      <span class="git-files__title">{{ $t('git.changedFiles') }}</span
      ><span>{{ $t('git.fileCount', { count: files.length }) }}</span
      ><span class="git-added">+{{ additions }}</span
      ><span class="git-deleted">−{{ deletions }}</span>
    </div>
    <nav id="git-file-navigation" class="git-files__list" :aria-label="$t('git.changedFiles')">
      <template v-for="group in groups" :key="group.directory">
        <div class="git-files__directory" :title="group.directory || $t('git.rootDirectory')">
          <UiIcon name="folder-closed" /><span>{{
            group.directory || $t('git.rootDirectory')
          }}</span>
        </div>
        <button
          v-for="file in group.files"
          :key="file.path"
          type="button"
          class="git-files__file"
          :aria-current="selected === file.path ? 'true' : undefined"
          :aria-label="label(file)"
          @click="emit('select', file.path)"
        >
          <span class="git-files__name">{{ name(file.path) }}</span
          ><span class="git-files__status" :class="`git-files__status--${file.status}`">{{
            file.status
          }}</span>
        </button>
      </template>
    </nav>
  </div>
</template>
