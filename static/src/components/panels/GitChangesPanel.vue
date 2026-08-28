<template>
  <aside class="git-changes-panel" :style="{ width: width + 'px' }">
    <header class="git-changes-panel__header">
      <div class="git-changes-panel__title">
        <span class="git-changes-panel__branch">{{ branchLabel }}</span>
        <span class="git-changes-panel__arrow">→</span>
        <span class="git-changes-panel__upstream">{{ upstreamLabel }}</span>
      </div>
      <div class="git-changes-panel__summary">
        <span class="git-changes-panel__add">+{{ additions }}</span>
        <span class="git-changes-panel__del">-{{ deletions }}</span>
        <button type="button" class="git-changes-panel__close" :aria-label="$t('shell.closeGitPanel')" @click="$emit('close')">
          ×
        </button>
      </div>
    </header>

    <div class="git-changes-panel__body">
      <div v-if="loading && !diff" class="git-changes-panel__empty">{{ $t('shell.loadingGitChanges') }}</div>
      <div v-else-if="error && !files.length" class="git-changes-panel__empty git-changes-panel__empty--error">{{ error }}</div>
      <div v-else-if="!files.length" class="git-changes-panel__empty">
        <svg class="git-changes-panel__empty-svg" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <path d="M9 20H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="13" cy="12" r="2" stroke="currentColor" stroke-width="2"/>
          <path d="M18 19c-2.8 0-5-2.2-5-5v8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="20" cy="19" r="2" stroke="currentColor" stroke-width="2"/>
        </svg>
        <span>{{ $t('shell.noUncommittedChanges') }}</span>
      </div>
      <section v-for="file in files" v-else :key="file.path" class="git-change-file">
        <div class="git-change-file__header">
          <span class="git-change-file__title">
            <span class="git-change-file__open-wrap">
              <button
                type="button"
                class="git-change-file__open-btn"
                :class="{ open: openMenuPath === file.path || appsLoadingPath === file.path }"
                :disabled="!hostMode"
                :title="hostMode ? $t('shell.openFileWithApp') : $t('shell.dockerModeUnavailable')"
                @click.stop.prevent="toggleOpenMenu(file.path)"
              >
                <img class="git-change-file__open-icon" :src="filePenIcon" alt="" aria-hidden="true" />
                <span class="git-change-file__open-caret" aria-hidden="true"></span>
              </button>
              <div
                v-if="openMenuPath === file.path"
                class="git-change-file__open-menu"
                @click.stop
              >
                <div v-if="openAppsError" class="git-change-file__open-empty">{{ openAppsError }}</div>
                <button
                  v-for="app in openApps"
                  v-else
                  :key="app.id"
                  type="button"
                  class="git-change-file__open-option"
                  @click.stop.prevent="openFileWithApp(file.path, app.id)"
                >
                  <img
                    class="git-change-file__open-option-icon"
                    :src="app.icon_url || filePenIcon"
                    alt=""
                    aria-hidden="true"
                  />
                  <span>{{ app.label }}</span>
                </button>
                <div v-if="!appsLoadingPath && !openAppsError && !openApps.length" class="git-change-file__open-empty">
                  {{ $t('shell.noAppsDetected') }}
                </div>
              </div>
            </span>
            <span class="git-change-file__path">{{ file.path }}</span>
            <button
              v-if="canResetContext(file.path)"
              type="button"
              class="git-change-file__reset"
              @click.stop.prevent="handleResetContext(file.path)"
            >
              {{ $t('shell.restore') }}
            </button>
          </span>
          <span class="git-change-file__stats">
            <span class="git-changes-panel__add">+{{ file.additions || 0 }}</span>
            <span class="git-changes-panel__del">-{{ file.deletions || 0 }}</span>
          </span>
        </div>

        <div class="git-change-file__diff">
          <div class="git-change-file__diff-inner">
            <template v-for="(hunk, hunkIndex) in file.hunks" :key="`${file.path}-${hunkIndex}`">
              <button
                v-if="hunk.before_hidden > 0"
                type="button"
                class="git-change-file__fold"
                @click.stop.prevent="handleExpandContext(file.path, hunk.before_fold_key)"
              >
                <span class="git-change-file__fold-content">
                  <span class="git-change-file__fold-icon git-change-file__fold-icon--up" aria-hidden="true"></span>
                  <span>{{ $t('shell.hiddenLines', { n: hunk.before_hidden }) }}</span>
                </span>
              </button>
              <div
                v-for="(line, lineIndex) in hunk.lines"
                :key="`${file.path}-${hunkIndex}-${lineIndex}`"
                class="git-change-line"
                :class="{
                  'git-change-line--add': line.kind === 'add',
                  'git-change-line--delete': line.kind === 'delete'
                }"
              >
                <span class="git-change-line__num">{{ formatLineNumber(line) }}</span>
                <span class="git-change-line__marker">{{ lineMarker(line.kind) }}</span>
                <span class="git-change-line__text">{{ line.text }}</span>
              </div>
            </template>
            <button
              v-if="file.after_hidden > 0"
              type="button"
              class="git-change-file__fold"
              @click.stop.prevent="handleExpandContext(file.path, file.after_fold_key || 'after')"
            >
              <span class="git-change-file__fold-content">
                <span class="git-change-file__fold-icon git-change-file__fold-icon--down" aria-hidden="true"></span>
                <span>{{ $t('shell.hiddenLines', { n: file.after_hidden }) }}</span>
              </span>
            </button>
          </div>
        </div>
      </section>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ref } from 'vue';
import { t, currentLocale } from '@/locales';

defineOptions({ name: 'GitChangesPanel' });

const props = defineProps<{
  width: number;
  loading?: boolean;
  error?: string;
  hostMode?: boolean;
  foldContexts?: Record<string, Record<string, number>>;
  diff: Record<string, any> | null;
}>();

const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'expand-context', payload: { path: string; foldKey: string }): void;
  (event: 'reset-context', path: string): void;
}>();

const branchLabel = computed(() => String(props.diff?.branch || 'HEAD'));
const upstreamLabel = computed(() => {
  void currentLocale.value;
  return String(props.diff?.upstream || t('shell.unsetUpstream'));
});
const additions = computed(() => Math.max(0, Number(props.diff?.additions || 0)));
const deletions = computed(() => Math.max(0, Number(props.diff?.deletions || 0)));
const files = computed(() => (Array.isArray(props.diff?.files) ? props.diff.files : []));
const canResetContext = (path: string) => {
  const fileMap = props.foldContexts?.[path];
  return !!fileMap && Object.values(fileMap).some((value) => Number(value || 0) > 0);
};
const filePenIcon = new URL('../../../icons/file-pen.svg', import.meta.url).href;
const openMenuPath = ref('');
const openApps = ref<Array<{ id: string; label: string; icon_url?: string }>>([]);
const openAppsError = ref('');
const appsLoadingPath = ref('');

const formatLineNumber = (line: Record<string, any>) => {
  const rawValue = line?.kind === 'delete' ? line.old_line : line?.new_line;
  const num = Number(rawValue);
  return Number.isFinite(num) && num > 0 ? String(num) : '';
};

const lineMarker = (kind: string) => {
  if (kind === 'add') return '+';
  if (kind === 'delete') return '-';
  return ' ';
};

const handleExpandContext = (path: string, foldKey: string) => {
  emit('expand-context', { path, foldKey });
};

const handleResetContext = (path: string) => {
  emit('reset-context', path);
};

const toggleOpenMenu = async (path: string) => {
  if (!props.hostMode) return;
  if (openMenuPath.value === path) {
    openMenuPath.value = '';
    return;
  }
  openMenuPath.value = '';
  openApps.value = [];
  openAppsError.value = '';
  appsLoadingPath.value = path;
  try {
    const resp = await fetch(`/api/project/file-open-apps?path=${encodeURIComponent(path)}`);
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || t('shell.detectAppsFailed'));
    }
    openApps.value = Array.isArray(payload?.data?.apps) ? payload.data.apps : [];
    openMenuPath.value = path;
  } catch (error: any) {
    openAppsError.value = error?.message || t('shell.detectAppsFailed');
    openMenuPath.value = path;
  } finally {
    appsLoadingPath.value = '';
  }
};

const openFileWithApp = async (path: string, appId: string) => {
  try {
    const resp = await fetch('/api/project/open-file-with-app', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, app_id: appId })
    });
    const payload = await resp.json().catch(() => ({}));
    if (!resp.ok || !payload?.success) {
      throw new Error(payload?.error || t('shell.openFileFailed'));
    }
    openMenuPath.value = '';
  } catch (error) {
    openAppsError.value = error instanceof Error ? error.message : t('shell.openFileFailed');
  }
};
</script>
