<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useProjectMemoryMigrationStore } from '@/stores/projectMemoryMigration';
import { useSettingsStore } from '@/stores/settings';

const migration = useProjectMemoryMigrationStore();
const settings = useSettingsStore();
const dialog = ref<HTMLDialogElement | null>(null);
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
  if (mounted && settings.sessionHostMode && document.visibilityState === 'visible') {
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
watch(dialog, (element) => element?.showModal(), { flush: 'post' });
watch(
  () => migration.open,
  (open) => {
    if (!open) detailsOpen.value = false;
  }
);
</script>

<template>
  <Teleport to="body">
    <dialog
      v-if="migration.open"
      ref="dialog"
      class="memory-migration-dialog"
      aria-modal="true"
      aria-labelledby="memory-migration-heading"
      @cancel.prevent="migration.dismiss"
    >
      <h2 id="memory-migration-heading">{{ $t(heading) }}</h2>

      <div class="memory-migration-content">
        <p v-if="migration.loading" role="status">{{ $t('common.loading') }}</p>
        <p v-else-if="migration.error !== null" role="alert">
          {{ migration.error || $t('settings.memoryMigration.requestFailed') }}
        </p>
        <template v-else-if="migration.job">
          <template v-if="migration.running">
            <div class="memory-migration-status" role="status">
              <strong>{{ $t(`settings.memoryMigration.${migration.job.phase}`) }}</strong>
              <span>{{ progress }} / {{ total }}</span>
            </div>
            <div
              class="memory-migration-track"
              role="progressbar"
              :aria-label="$t(`settings.memoryMigration.${migration.job.phase}`)"
              :aria-valuenow="progress"
              :aria-valuemax="total"
            >
              <div :style="{ width: `${total ? (progress / total) * 100 : 0}%` }"></div>
            </div>
            <p v-if="migration.job.current" class="memory-migration-detail">
              {{ migration.job.current }}
            </p>
          </template>
          <template v-else>
            <p role="status">
              {{
                $t('settings.memoryMigration.summary', {
                  workspaces: migration.job.completed_workspaces,
                  memories: migration.job.migrated
                })
              }}
            </p>
            <ul v-if="migration.job.conflicts.length" class="memory-migration-list">
              <li
                v-for="entry in migration.job.conflicts"
                :key="`${entry.workspace}/${entry.target}`"
              >
                {{ $t('settings.memoryMigration.conflict', entry) }}
              </li>
            </ul>
          </template>
        </template>
        <template v-else-if="migration.overview?.total">
          <p>
            {{
              $t(
                migration.overview.mode === 'host'
                  ? 'settings.memoryMigration.hostIntro'
                  : 'settings.memoryMigration.dockerIntro',
                {
                  users: migration.overview.users,
                  workspaces: migration.overview.workspaces.length,
                  memories: migration.overview.total
                }
              )
            }}
          </p>
          <div v-if="migration.overview.data_root" class="memory-migration-path">
            <span>{{ $t('settings.memoryMigration.destination') }}</span>
            <code>{{
              migration.overview.workspaces.length === 1
                ? migration.overview.workspaces[0].target
                : migration.overview.data_root
            }}</code>
          </div>
          <template
            v-if="migration.overview.workspaces.length > 1 || migration.overview.mode === 'docker'"
          >
            <button
              type="button"
              class="memory-migration-details"
              :aria-expanded="detailsOpen"
              aria-controls="memory-migration-targets"
              @click="detailsOpen = !detailsOpen"
            >
              {{
                $t(
                  detailsOpen
                    ? 'settings.memoryMigration.hideTargets'
                    : 'settings.memoryMigration.showTargets'
                )
              }}
            </button>
            <ul v-if="detailsOpen" id="memory-migration-targets" class="memory-migration-list">
              <li v-for="workspace in migration.overview.workspaces" :key="workspace.target">
                <strong>{{ workspace.label }}</strong>
                <code>{{ workspace.target }}</code>
              </li>
            </ul>
          </template>
        </template>
        <p v-else>
          {{
            $t(errors.length ? 'settings.memoryMigration.failed' : 'settings.memoryMigration.empty')
          }}
        </p>
        <ul
          v-if="errors.length && !migration.running"
          class="memory-migration-list memory-migration-errors"
          role="alert"
        >
          <li v-for="(error, index) in errors" :key="index">
            <strong>{{ error.workspace }}</strong>
            <span>{{ error.reason }}</span>
          </li>
        </ul>
      </div>

      <div v-if="!migration.running && !migration.loading" class="memory-migration-footer">
        <div class="memory-migration-actions">
          <button type="button" class="memory-migration-button" @click="migration.dismiss">
            {{ $t(migration.overview?.total ? 'settings.memoryMigration.defer' : 'common.close') }}
          </button>
          <button
            v-if="migration.overview?.total"
            type="button"
            class="memory-migration-button memory-migration-button--primary"
            @click="migration.load('POST')"
          >
            {{ $t('settings.memoryMigration.start') }}
          </button>
          <button
            v-else-if="
              migration.job?.phase === 'failed' || migration.error !== null || errors.length
            "
            type="button"
            class="memory-migration-button memory-migration-button--primary"
            @click="migration.load(migration.job?.phase === 'failed' ? 'POST' : 'GET')"
          >
            {{ $t('settings.memoryMigration.retry') }}
          </button>
        </div>
        <p
          v-if="migration.overview?.total && migration.overview.mode === 'host'"
          class="memory-migration-detail"
        >
          {{ $t('settings.memoryMigration.deferHint') }}
        </p>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped lang="scss">
.memory-migration-dialog {
  width: min(480px, calc(100% - 32px));
  max-height: min(85dvh, calc(100dvh - 48px));
  margin: auto;
  padding: 20px;
  box-sizing: border-box;
  border: 1px solid var(--border-strong);
  border-radius: 18px;
  background: var(--surface-raised);
  box-shadow: var(--shadow-soft);
  color: var(--text-primary);

  &[open] {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  &::backdrop {
    background: var(--overlay-scrim);
    backdrop-filter: blur(12px);
  }
  h2 {
    margin: 0;
    font-size: 20px;
    line-height: 28px;
  }
  p {
    margin: 0;
  }
  code {
    display: block;
    overflow-wrap: anywhere;
    color: var(--text-primary);
  }
}

.memory-migration-content {
  overflow-y: auto;
  overscroll-behavior: contain;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 14px;
  line-height: 1.5;
}

.memory-migration-path,
.memory-migration-detail {
  color: var(--text-secondary);
  font-size: 13px;
}

.memory-migration-list {
  list-style: none;
  margin: 0;
  padding: 0;

  li {
    padding: 8px 0;
    border-top: 1px solid var(--border-default);
    overflow-wrap: anywhere;
  }
  strong,
  span {
    display: block;
  }
}

.memory-migration-errors span {
  color: var(--state-danger);
}

.memory-migration-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-variant-numeric: tabular-nums;
}

.memory-migration-track {
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--border-default);

  div {
    height: 100%;
    background: var(--accent);
    transition: width 140ms ease-out;
  }
}

.memory-migration-details {
  align-self: flex-start;
  height: 32px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}

.memory-migration-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.memory-migration-button {
  height: 34px;
  padding: 0 14px;
  border: 1px solid var(--border-default);
  border-radius: 7px;
  background: var(--surface-raised);
  color: var(--text-primary);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;

  &:hover {
    background: var(--surface-muted);
  }
  &:focus-visible {
    outline: 2px solid var(--text-secondary);
    outline-offset: 2px;
  }
  &--primary {
    background: var(--text-primary);
    color: var(--surface-base);
  }
  &--primary:hover {
    background: var(--text-secondary);
  }
}

.memory-migration-footer .memory-migration-detail {
  margin-top: 8px;
}

@media (prefers-reduced-motion: reduce) {
  .memory-migration-track div {
    transition: none;
  }
}
</style>
