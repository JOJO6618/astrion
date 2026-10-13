<script setup lang="ts">
import { computed } from 'vue';
import type { Diff, DiffLine } from './api';
const props = defineProps<{ diff: Diff; loading: boolean; mode: 'unified' | 'split' }>();
const emit = defineEmits<{ (event: 'more'): void }>();
interface SplitRow {
  left: DiffLine | null;
  right: DiffLine | null;
  wide?: DiffLine;
}
const splitRows = computed(() => {
  const rows: SplitRow[] = [];
  let deleted: DiffLine[] = [];
  let added: DiffLine[] = [];
  function flush() {
    for (let i = 0; i < Math.max(deleted.length, added.length); i++)
      rows.push({ left: deleted[i] || null, right: added[i] || null });
    deleted = [];
    added = [];
  }
  for (const line of props.diff.lines) {
    if (line.kind === 'delete') {
      if (added.length) flush();
      deleted.push(line);
    } else if (line.kind === 'add') added.push(line);
    else {
      flush();
      rows.push(
        line.kind === 'context'
          ? { left: line, right: line }
          : { left: null, right: null, wide: line }
      );
    }
  }
  flush();
  return rows;
});
</script>

<template>
  <section class="git-patch">
    <header class="git-patch__header">
      <span class="git-patch__name"
        ><span v-if="diff.file.status === 'R'">{{ diff.file.old_path }} → </span
        >{{ diff.file.path }}</span
      >
      <span class="git-patch__stat"
        ><span class="git-added">+{{ diff.file.additions }}</span
        ><span class="git-deleted">−{{ diff.file.deletions }}</span></span
      >
    </header>
    <div v-if="diff.file.binary" class="git-empty">{{ $t('git.binary') }}</div>
    <div v-else-if="!diff.lines.length" class="git-empty">{{ $t('git.noPatch') }}</div>
    <div v-else class="git-patch__scroll">
      <table v-if="mode === 'unified'" class="git-patch__table">
        <tbody>
          <tr v-for="(line, index) in diff.lines" :key="index" :class="`git-patch__${line.kind}`">
            <td class="git-patch__number">{{ line.old_line }}</td>
            <td class="git-patch__number">{{ line.new_line }}</td>
            <td class="git-patch__marker">
              {{ line.kind === 'add' ? '+' : line.kind === 'delete' ? '−' : '' }}
            </td>
            <td class="git-patch__code">
              <pre>{{ line.text }}</pre>
            </td>
          </tr>
        </tbody>
      </table>
      <table v-else class="git-patch__table git-patch__split">
        <colgroup>
          <col class="git-patch__number-col" />
          <col />
          <col class="git-patch__number-col" />
          <col />
        </colgroup>
        <tbody>
          <tr v-for="(row, index) in splitRows" :key="index">
            <td v-if="row.wide" colspan="4" :class="`git-patch__code git-patch__${row.wide.kind}`">
              <pre>{{ row.wide.text }}</pre>
            </td>
            <template v-else>
              <td class="git-patch__number" :class="row.left ? `git-patch__${row.left.kind}` : ''">
                {{ row.left?.old_line }}
              </td>
              <td class="git-patch__code" :class="row.left ? `git-patch__${row.left.kind}` : ''">
                <pre>{{ row.left?.kind === 'delete' ? '− ' : '  ' }}{{ row.left?.text }}</pre>
              </td>
              <td
                class="git-patch__number git-patch__divider"
                :class="row.right ? `git-patch__${row.right.kind}` : ''"
              >
                {{ row.right?.new_line }}
              </td>
              <td class="git-patch__code" :class="row.right ? `git-patch__${row.right.kind}` : ''">
                <pre>{{ row.right?.kind === 'add' ? '+ ' : '  ' }}{{ row.right?.text }}</pre>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
    </div>
    <button
      v-if="diff.has_more"
      type="button"
      class="git-button git-patch__more"
      :disabled="loading"
      @click="emit('more')"
    >
      {{ loading ? $t('common.loading') : $t('git.more') }}
    </button>
  </section>
</template>

<style scoped lang="scss">
.git-patch {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
}
.git-patch__header {
  height: 38px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 16px;
  background: var(--surface-soft);
}
.git-patch__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}
.git-patch__stat {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
  font-size: 11px;
}
.git-patch__scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  scrollbar-width: thin;
}
.git-patch__table {
  width: 100%;
  border-spacing: 0;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  line-height: 22px;
}
.git-patch__table td {
  vertical-align: top;
}
.git-patch__number {
  min-width: 40px;
  width: 40px;
  padding: 0 8px;
  text-align: right;
  user-select: none;
  color: var(--text-muted);
}
.git-patch__marker {
  width: 18px;
  user-select: none;
  text-align: center;
}
.git-patch__code {
  padding: 0 12px;
}
.git-patch__code pre {
  margin: 0;
  font: inherit;
  white-space: pre;
  min-height: 22px;
}
.git-patch__add {
  background: var(--diff-add-bg);
}
.git-patch__delete {
  background: var(--diff-del-bg);
}
.git-patch__hunk {
  background: var(--surface-muted);
  color: var(--text-secondary);
}
.git-patch__meta {
  color: var(--text-muted);
}
.git-patch__split {
  table-layout: fixed;
}
.git-patch__split .git-patch__code pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.git-patch__number-col {
  width: 48px;
}
.git-patch__divider {
  border-left: 1px solid var(--border-default);
}
.git-patch__more {
  margin: 12px 16px;
}
@media (width <= 700px) {
  .git-patch__header {
    padding: 0 10px;
  }
  .git-patch__number {
    min-width: 30px;
    width: 30px;
    padding: 0 4px;
  }
}
</style>
