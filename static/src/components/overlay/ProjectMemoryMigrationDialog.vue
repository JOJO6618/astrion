<template>
  <UiDialog
    :open="migration.open"
    :title="$t(heading)"
    :busy="migration.running || migration.loading"
    @close="migration.dismiss"
  >
    <p v-if="migration.loading" class="ui-dialog-text" role="status">
      {{ $t('common.loading') }}
    </p>
    <p v-else-if="migration.error !== null" class="ui-dialog-error" role="alert">
      {{ migration.error || $t('settings.memoryMigration.requestFailed') }}
    </p>
    <template v-else-if="migration.job">
      <template v-if="migration.running">
        <div class="memory-migration-status" role="status">
          <span>{{ $t(`settings.memoryMigration.${migration.job.phase}`) }}</span>
          <span class="memory-migration-count">{{ progress }} / {{ total }}</span>
        </div>
        <div
          class="ui-dialog-progress memory-migration-progress"
          role="progressbar"
          :aria-label="$t(`settings.memoryMigration.${migration.job.phase}`)"
          :aria-valuenow="progress"
          :aria-valuemax="total"
        >
          <span :style="{ width: `${total ? (progress / total) * 100 : 0}%` }" />
        </div>
        <p v-if="migration.job.current" class="ui-dialog-note memory-migration-current">
          {{ migration.job.current }}
        </p>
      </template>
      <template v-else>
        <p class="ui-dialog-text" role="status">
          {{
            $t('settings.memoryMigration.summary', {
              workspaces: migration.job.completed_workspaces,
              memories: migration.job.migrated
            })
          }}
        </p>
        <ul v-if="migration.job.conflicts.length" class="memory-migration-notes">
          <li v-for="entry in migration.job.conflicts" :key="`${entry.workspace}/${entry.target}`">
            {{ $t('settings.memoryMigration.conflict', entry) }}
          </li>
        </ul>
      </template>
    </template>
    <template v-else-if="migration.overview?.total">
      <p class="ui-dialog-text">
        {{
          $t('settings.memoryMigration.hostIntro', {
            users: migration.overview.users,
            workspaces: migration.overview.workspaces.length,
            memories: migration.overview.total
          })
        }}
      </p>
      <div v-if="migration.overview.data_root" class="ui-dialog-field">
        <span class="ui-dialog-label">{{ $t('settings.memoryMigration.destination') }}</span>
        <code class="ui-dialog-code">{{
          migration.overview.workspaces.length === 1
            ? migration.overview.workspaces[0].target
            : migration.overview.data_root
        }}</code>
      </div>
      <div v-if="migration.overview.workspaces.length > 1" class="memory-migration-targets">
        <button
          type="button"
          class="ui-dialog-link memory-migration-toggle"
          :aria-expanded="detailsOpen"
          aria-controls="memory-migration-targets"
          @click="detailsOpen = !detailsOpen"
        >
          <span>{{
            $t(
              detailsOpen
                ? 'settings.memoryMigration.hideTargets'
                : 'settings.memoryMigration.showTargets'
            )
          }}</span>
          <UiIcon name="chevron-down" />
        </button>
        <div class="memory-migration-collapse" :class="{ open: detailsOpen }">
          <ul id="memory-migration-targets" class="memory-migration-list" :inert="!detailsOpen">
            <li v-for="workspace in migration.overview.workspaces" :key="workspace.target">
              <strong>{{ workspace.label }}</strong>
              <code>{{ workspace.target }}</code>
            </li>
          </ul>
        </div>
      </div>
      <p class="ui-dialog-note">
        {{ $t('settings.memoryMigration.deferHint') }}
      </p>
    </template>
    <p v-else class="ui-dialog-text">
      {{ $t(errors.length ? 'settings.memoryMigration.failed' : 'settings.memoryMigration.empty') }}
    </p>
    <ul v-if="errors.length && !migration.running" class="memory-migration-list" role="alert">
      <li v-for="(error, index) in errors" :key="index">
        <strong>{{ error.workspace }}</strong>
        <span class="memory-migration-error">{{ error.reason }}</span>
      </li>
    </ul>

    <template v-if="!migration.running && !migration.loading" #actions>
      <UiDialogButton @click="migration.dismiss">
        {{ $t(migration.overview?.total ? 'settings.memoryMigration.defer' : 'common.close') }}
      </UiDialogButton>
      <UiDialogButton
        v-if="migration.overview?.total"
        variant="primary"
        @click="migration.load('POST')"
      >
        {{ $t('settings.memoryMigration.start') }}
      </UiDialogButton>
      <UiDialogButton
        v-else-if="migration.job?.phase === 'failed' || migration.error !== null || errors.length"
        variant="primary"
        @click="migration.load(migration.job?.phase === 'failed' ? 'POST' : 'GET')"
      >
        {{ $t('settings.memoryMigration.retry') }}
      </UiDialogButton>
    </template>
  </UiDialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useProjectMemoryMigrationStore } from '@/stores/projectMemoryMigration';
import { useSettingsStore } from '@/stores/settings';
import UiDialog from '@/components/ui/UiDialog.vue';
import UiDialogButton from '@/components/ui/UiDialogButton.vue';
import UiIcon from '@/components/ui/UiIcon.vue';

const migration = useProjectMemoryMigrationStore();
const settings = useSettingsStore();
const detailsOpen = ref(false);
const heading = computed(() => {
  if (migration.job?.phase === 'done') return 'settings.memoryMigration.done';
  if (migration.job?.phase === 'failed') return 'settings.memoryMigration.failed';
  return 'settings.memoryMigration.title';
});
const total = computed(() => migration.job?.total || 0);
const progress = computed(() => {
  const job = migration.job;
  return job?.phase === 'verifying' ? job.verified : (job?.copied ?? 0);
});
const errors = computed(() => migration.job?.errors ?? migration.overview?.errors ?? []);

let mounted = false;
async function checkStartup() {
  if (document.visibilityState !== 'visible' || migration.startupChecked) return;
  await settings.fetchSessionStatus();
  if (mounted && document.visibilityState === 'visible') {
    void migration.checkStartup();
  }
}

onMounted(() => {
  mounted = true;
  document.addEventListener('visibilitychange', checkStartup);
  void checkStartup();
});
onBeforeUnmount(() => {
  mounted = false;
  document.removeEventListener('visibilitychange', checkStartup);
});
watch(
  () => migration.open,
  (open) => {
    if (!open) detailsOpen.value = false;
  }
);
</script>
<style scoped>
.memory-migration-status {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 14px;
  line-height: 22px;
}

.memory-migration-count {
  flex: none;
  color: var(--text-tertiary);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

/* 进度条与当前路径紧贴状态行：同一组信息，间距 8 */
.ui-dialog-body > .memory-migration-progress,
.ui-dialog-body > .memory-migration-current {
  margin-top: 8px;
}

.memory-migration-current {
  overflow-wrap: anywhere;
}

.memory-migration-notes {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: var(--text-tertiary);
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

/* 文字操作与正文左缘对齐：热区向左外伸 8 */
.memory-migration-toggle {
  margin: -4px 0 -4px -8px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.memory-migration-toggle .ui-icon {
  width: 12px;
  height: 12px;
  transition: transform 200ms ease;
}

.memory-migration-toggle[aria-expanded='true'] .ui-icon {
  transform: rotate(180deg);
}

.memory-migration-collapse {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  transition:
    grid-template-rows 200ms ease,
    opacity 200ms ease;
}

.memory-migration-collapse.open {
  grid-template-rows: 1fr;
  opacity: 1;
}

.memory-migration-collapse .memory-migration-list {
  min-height: 0;
  overflow: hidden;
}

/* 列表不画分隔线，靠留白分行 */
.memory-migration-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.memory-migration-list li {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.memory-migration-list li + li,
.memory-migration-collapse .memory-migration-list li:first-child {
  padding-top: 12px;
}

.memory-migration-list strong {
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
}

.memory-migration-list code {
  color: var(--text-tertiary);
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}

.memory-migration-error {
  color: var(--state-danger);
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}

@media (prefers-reduced-motion: reduce) {
  .memory-migration-collapse,
  .memory-migration-toggle .ui-icon {
    transition: none;
  }
}
</style>
