<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import GitSelect from './GitSelect.vue';
import GitDiff from './GitDiff.vue';
import GitIcon from './GitIcon.vue';
import GitCommitMessage from './GitCommitMessage.vue';
import GitReviewMenu from './GitReviewMenu.vue';
import GitFileNavigation from './GitFileNavigation.vue';
import GitHistoryPanel from './GitHistoryPanel.vue';
import { useGitHistory } from './useGitHistory';
import type { Commit } from './api';
const props = defineProps<{ workspaceId: string }>();
const emit = defineEmits<{ (event: 'close'): void }>();
const {
  discovery,
  repository,
  workspaceId,
  branches,
  currentBranch,
  branch,
  baseBranch,
  headBranch,
  search,
  commits,
  hasMore,
  compare,
  selectedFile,
  diff,
  matchingReviews,
  scanning,
  loading,
  comparing,
  loadingDiff,
  reviewing,
  error,
  diffError,
  scan,
  selectRepository,
  loadHistory,
  selectCommit,
  compareBranches,
  selectFile,
  startReview
} = useGitHistory(() => props.workspaceId);
const historyOpen = ref(false);
const historyLeaving = ref(false);
const historyPending = ref(false);
const repositoryOpen = ref(false);
const repositoryPending = ref(false);
const historyTrigger = ref<HTMLButtonElement>();
const branchMode = ref(false);
const filesOpen = ref(true);
const noticesOpen = ref(false);
const mode = ref<'unified' | 'split'>('unified');
const repositoryOptions = computed(() =>
  discovery.value.repositories.map((repo) => ({
    value: repo.id,
    label: repo.name,
    secondary:
      repo.workspaces.find((w) => w.workspace_id === workspaceId.value)?.relative_path || '.'
  }))
);
const workspaceLabel = computed(() => discovery.value.workspaces[0]?.label || props.workspaceId);
const branchOptions = computed(() => [
  { value: '', label: currentBranch.value ? `${currentBranch.value} · HEAD` : 'HEAD' },
  ...branches.value.map((b) => ({ value: b.ref, label: b.name }))
]);
const refOptions = computed(() => branches.value.map((b) => ({ value: b.ref, label: b.name })));
const headOptions = computed(() => [{ value: 'HEAD', label: 'HEAD' }, ...refOptions.value]);
const parentOptions = computed(() =>
  (compare.value?.commit?.parents || []).map((sha) => ({ value: sha, label: sha.slice(0, 8) }))
);
const subject = computed(() => {
  const selected = compare.value;
  if (!selected) return '';
  if (selected.commit) return selected.commit.subject || selected.head_sha.slice(0, 8);
  return `${selected.base_ref} → ${selected.head_ref}`;
});
const authorInitial = computed(
  () => compare.value?.commit?.author.trim().slice(0, 1).toUpperCase() || ''
);
const ui = useUiStore();
function date(value: string) {
  return new Date(value).toLocaleString();
}
function closeHistory(restoreFocus = false) {
  historyPending.value = false;
  if (historyOpen.value) historyLeaving.value = true;
  historyOpen.value = false;
  if (restoreFocus) historyTrigger.value?.focus();
}
function toggleHistory() {
  repositoryPending.value = false;
  repositoryOpen.value = false;
  if (historyLeaving.value) {
    historyPending.value = !historyPending.value;
    return;
  }
  if (historyOpen.value) closeHistory();
  else historyOpen.value = true;
}
function requestRepositories(open: boolean) {
  historyPending.value = false;
  repositoryPending.value = false;
  if (open && (historyOpen.value || historyLeaving.value)) {
    repositoryPending.value = true;
    closeHistory();
  } else repositoryOpen.value = open;
}
function historyClosed() {
  historyLeaving.value = false;
  if (historyPending.value) historyOpen.value = true;
  else if (repositoryPending.value) repositoryOpen.value = true;
  historyPending.value = false;
  repositoryPending.value = false;
}
function pickRepository(id: string) {
  repositoryOpen.value = false;
  const selected = discovery.value.repositories.find((repo) => repo.id === id);
  if (selected) void selectRepository(selected, workspaceId.value);
}
function pickCommit(commit: Commit) {
  closeHistory(true);
  void selectCommit(commit);
}
async function pickComparison() {
  await compareBranches();
  if (compare.value) closeHistory(true);
}
async function copySha() {
  if (!compare.value) return;
  try {
    await navigator.clipboard.writeText(compare.value.head_sha);
    ui.pushToast({ message: t('git.copied'), type: 'success' });
  } catch (exc) {
    ui.pushToast({ message: String(exc), type: 'error' });
  }
}
function escape(event: KeyboardEvent) {
  if (event.key !== 'Escape') return;
  repositoryOpen.value = false;
  repositoryPending.value = false;
  closeHistory(true);
}
onMounted(() => document.addEventListener('keydown', escape));
onBeforeUnmount(() => document.removeEventListener('keydown', escape));
</script>

<template>
  <section class="git-shell" :aria-label="$t('git.title')">
    <header class="git-shell__top">
      <button
        type="button"
        class="git-icon-button"
        :aria-label="$t('common.back')"
        @click="emit('close')"
      >
        <GitIcon name="back" />
      </button>
      <span class="git-shell__workspace-label">{{ workspaceLabel }}</span>
      <span class="git-shell__separator" aria-hidden="true">/</span>
      <div class="git-shell__repository-picker">
        <GitIcon name="folder" />
        <GitSelect
          :model-value="repository?.id || ''"
          :open="repositoryOpen"
          :label="$t('git.repositories')"
          :options="repositoryOptions"
          :disabled="scanning || reviewing"
          @update:open="requestRepositories"
          @update:model-value="pickRepository"
        />
      </div>
      <button
        ref="historyTrigger"
        type="button"
        class="git-button git-button--quiet"
        :disabled="!repository || reviewing"
        :aria-expanded="historyOpen"
        aria-controls="git-history-panel"
        @click="toggleHistory"
      >
        <GitIcon name="history" /><span>{{ $t('git.chooseCommit') }}</span
        ><GitIcon name="chevron" />
      </button>
      <div class="git-shell__top-actions">
        <button
          v-if="discovery.warnings.length"
          type="button"
          class="git-button git-button--quiet"
          :aria-expanded="noticesOpen"
          @click="noticesOpen = !noticesOpen"
        >
          {{ $t('git.discoveryIssues') }}
        </button>
        <button
          type="button"
          class="git-icon-button"
          :disabled="scanning || reviewing || !workspaceId"
          :aria-label="$t('git.refresh')"
          @click="scan(true)"
        >
          <GitIcon name="refresh" />
        </button>
      </div>
    </header>
    <div v-if="noticesOpen" class="git-shell__notices">
      <p v-for="(notice, index) in discovery.warnings" :key="index">{{ notice.error }}</p>
    </div>
    <div v-if="error" class="git-shell__error" role="alert">
      {{ error
      }}<button type="button" class="git-button" @click="repository ? loadHistory() : scan(true)">
        {{ $t('common.retry') }}
      </button>
    </div>
    <main class="git-shell__main">
      <Transition
        name="git-history"
        @after-leave="historyClosed"
        @leave-cancelled="historyLeaving = false"
      >
        <GitHistoryPanel
          v-if="historyOpen"
          v-model:branch="branch"
          v-model:base-branch="baseBranch"
          v-model:head-branch="headBranch"
          v-model:search="search"
          v-model:branch-mode="branchMode"
          :commits="commits"
          :selected-sha="compare?.kind === 'commit' ? compare.head_sha : ''"
          :loading="loading"
          :comparing="comparing"
          :has-more="hasMore"
          :reviewing="reviewing"
          :branch-options="branchOptions"
          :ref-options="refOptions"
          :head-options="headOptions"
          @close="closeHistory(true)"
          @select="pickCommit"
          @history="loadHistory($event)"
          @compare="pickComparison"
        />
      </Transition>
      <div v-if="!compare" class="git-empty git-shell__empty">
        {{
          scanning || loading || comparing || !workspaceId
            ? $t('common.loading')
            : !repository
              ? $t('git.noRepositories')
              : $t('git.noCommits')
        }}
      </div>
      <template v-else>
        <header class="git-shell__selection">
          <div class="git-shell__selection-content">
            <GitCommitMessage :subject="subject" :message="compare.commit?.message" />
            <div class="git-shell__byline">
              <template v-if="compare.commit"
                ><span class="git-shell__avatar" aria-hidden="true">{{ authorInitial }}</span
                ><span class="git-shell__author">{{ compare.commit.author }}</span
                ><time :datetime="compare.commit.date">{{
                  date(compare.commit.date)
                }}</time></template
              >
              <GitIcon v-else name="branch" />
              <button
                type="button"
                class="git-shell__sha"
                :aria-label="$t('git.copySha')"
                @click="copySha"
              >
                <code>{{ compare.head_sha.slice(0, 8) }}</code
                ><GitIcon name="copy" />
              </button>
              <span class="git-shell__base"
                ><span>{{ $t('git.baseline') }}</span
                ><code>{{ compare.base_sha.slice(0, 8) }}</code></span
              >
              <GitSelect
                v-if="parentOptions.length > 1"
                :model-value="compare.base_sha"
                :label="$t('git.parent')"
                :options="parentOptions"
                @update:model-value="selectCommit(compare!.commit!, $event)"
              />
            </div>
          </div>
          <div class="git-shell__review-actions">
            <button
              type="button"
              class="git-button git-button--primary"
              :disabled="comparing || reviewing"
              @click="startReview()"
            >
              {{ reviewing ? $t('git.reviewing') : $t('git.review') }}
            </button>
          </div>
        </header>
        <div class="git-shell__changes-toolbar">
          <div class="git-shell__file-summary">
            <button
              type="button"
              class="git-icon-button git-shell__files-toggle"
              :aria-label="$t('git.toggleFiles')"
              :aria-expanded="filesOpen"
              aria-controls="git-file-navigation"
              @click="filesOpen = !filesOpen"
            >
              <GitIcon name="folder" /></button
            ><span>{{ $t('git.files', { count: compare.files.length }) }}</span
            ><span class="git-added">+{{ compare.additions }}</span
            ><span class="git-deleted">−{{ compare.deletions }}</span>
          </div>
          <GitReviewMenu
            :key="`${compare.head_sha}:${compare.base_sha}`"
            :reviews="matchingReviews"
          />
          <div class="git-shell__modes" role="group" :aria-label="$t('git.diffMode')">
            <button type="button" :aria-pressed="mode === 'unified'" @click="mode = 'unified'">
              {{ $t('git.unified') }}</button
            ><button type="button" :aria-pressed="mode === 'split'" @click="mode = 'split'">
              {{ $t('git.split') }}
            </button>
          </div>
        </div>
        <div v-if="!compare.files.length" class="git-empty">{{ $t('git.noChanges') }}</div>
        <div v-else class="git-shell__reader">
          <GitFileNavigation
            v-if="filesOpen"
            :files="compare.files"
            :selected="selectedFile"
            @select="selectFile($event)"
          />
          <section class="git-shell__diff">
            <div v-if="diffError" class="git-shell__error" role="alert">
              {{ diffError
              }}<button type="button" class="git-button" @click="selectFile(selectedFile)">
                {{ $t('common.retry') }}
              </button>
            </div>
            <div v-if="loadingDiff && !diff" class="git-empty">{{ $t('common.loading') }}</div>
            <GitDiff
              v-if="diff"
              :key="`${compare.head_sha}:${compare.base_sha}:${selectedFile}`"
              :diff="diff"
              :mode="mode"
              :loading="loadingDiff"
              @more="selectFile(selectedFile, true)"
            />
          </section>
        </div>
      </template>
    </main>
  </section>
</template>

<style src="./git.scss" lang="scss"></style>
