<script setup lang="ts">
/**
 * 项目 Git 页（DESIGN.md §7「项目 Git 页变体」，设计稿 _experiments/2026-11-16_设计统一demo/git-page.html）：
 * 左侧卡片 = 返回 + 标题 + 刷新 → 仓库下拉 → 变更文件；右侧卡片 = 比较范围与操作 → 提交摘要 → 当前文件 → diff。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import UiIcon from '@/components/ui/UiIcon.vue';
import UiIconButton from '@/components/ui/UiIconButton.vue';
import UiMenu from '@/components/ui/UiMenu.vue';
import UiSelect from '@/components/ui/UiSelect.vue';
import UiSelectTrigger from '@/components/ui/UiSelectTrigger.vue';
import GitDiff from './GitDiff.vue';
import GitCommitMessage from './GitCommitMessage.vue';
import GitReviewMenu from './GitReviewMenu.vue';
import GitFileNavigation from './GitFileNavigation.vue';
import GitHistoryPanel from './GitHistoryPanel.vue';
import { useGitHistory } from './useGitHistory';
import { shortDate, type Commit } from './api';
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

const ui = useUiStore();
const repositoryOpen = ref(false);
const repositoryTrigger = ref<InstanceType<typeof UiSelectTrigger> | null>(null);
const historyOpen = ref(false);
const historyTrigger = ref<InstanceType<typeof UiSelectTrigger> | null>(null);
const branchMode = ref(false);
const noticesOpen = ref(false);
const mode = ref<'unified' | 'split'>('unified');

// 窄屏单列：文件列表默认收起，选中文件后自动收起，避免挤占 diff
const narrowQuery = window.matchMedia('(max-width: 760px)');
const narrow = ref(narrowQuery.matches);
const filesOpen = ref(!narrow.value);
function onNarrowChange(event: MediaQueryListEvent) {
  narrow.value = event.matches;
  filesOpen.value = !event.matches;
}
onMounted(() => narrowQuery.addEventListener('change', onNarrowChange));
onBeforeUnmount(() => narrowQuery.removeEventListener('change', onNarrowChange));

const relativePath = (id: string) =>
  discovery.value.repositories
    .find((repo) => repo.id === id)
    ?.workspaces.find((w) => w.workspace_id === workspaceId.value)?.relative_path || '.';
const repositoryOptions = computed(() =>
  discovery.value.repositories.map((repo) => ({
    value: repo.id,
    label: repo.name,
    hint: relativePath(repo.id)
  }))
);
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
const currentFile = computed(
  () => diff.value?.file || compare.value?.files.find((file) => file.path === selectedFile.value)
);
const emptyText = computed(() =>
  scanning.value || loading.value || comparing.value || !workspaceId.value
    ? t('common.loading')
    : !repository.value
      ? t('git.noRepositories')
      : t('git.noCommits')
);

function toggleRepositories() {
  repositoryOpen.value = !repositoryOpen.value;
}
function closeRepositories(restoreFocus: boolean) {
  repositoryOpen.value = false;
  if (restoreFocus) repositoryTrigger.value?.el?.focus({ preventScroll: true });
}
function pickRepository(id: string) {
  const selected = discovery.value.repositories.find((repo) => repo.id === id);
  if (selected && selected.id !== repository.value?.id)
    void selectRepository(selected, workspaceId.value);
}
function toggleHistory() {
  if (!historyOpen.value) branchMode.value = false;
  historyOpen.value = !historyOpen.value;
}
function closeHistory(restoreFocus: boolean) {
  historyOpen.value = false;
  if (restoreFocus) historyTrigger.value?.el?.focus({ preventScroll: true });
}
function pickCommit(commit: Commit) {
  closeHistory(true);
  void selectCommit(commit);
}
async function pickComparison() {
  await compareBranches();
  if (compare.value) closeHistory(true);
}
function pickFile(path: string) {
  void selectFile(path);
  if (narrow.value) filesOpen.value = false;
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
</script>

<template>
  <main class="git-page" :class="{ 'is-files-closed': !filesOpen }" :aria-label="$t('git.title')">
    <!-- 左：返回 + 标题 + 刷新 → 仓库 → 变更文件 -->
    <aside class="git-card git-side" :inert="(!filesOpen && !narrow) || undefined">
      <header class="git-side__head">
        <div class="git-side__title">
          <UiIconButton icon="arrow-left" :label="$t('common.back')" @click="emit('close')" />
          <h1>{{ $t('git.title') }}</h1>
        </div>
        <UiIconButton
          icon="refresh-cw"
          :label="$t('git.refresh')"
          :disabled="scanning || reviewing || !workspaceId"
          @click="scan(true)"
        />
      </header>
      <div class="git-side__repository">
        <UiSelectTrigger
          ref="repositoryTrigger"
          icon="folder"
          :text="repository?.name || $t('git.repositories')"
          :placeholder="!repository"
          :hint="repository ? relativePath(repository.id) : ''"
          :open="repositoryOpen"
          :label="$t('git.repositories')"
          :disabled="scanning || reviewing || !repositoryOptions.length"
          @click="toggleRepositories"
        />
        <UiMenu
          :open="repositoryOpen"
          :anchor="repositoryTrigger?.el"
          :items="repositoryOptions"
          :value="repository?.id"
          :label="$t('git.repositories')"
          @select="pickRepository"
          @close="closeRepositories"
        />
      </div>
      <div v-if="discovery.warnings.length" class="git-side__notices">
        <button type="button" :aria-expanded="noticesOpen" @click="noticesOpen = !noticesOpen">
          <UiIcon name="triangle-alert" />{{ $t('git.discoveryIssues') }}
          <UiIcon name="chevron-down" class="git-side__notices-chevron" />
        </button>
        <p v-for="(notice, index) in noticesOpen ? discovery.warnings : []" :key="index">
          {{ notice.error }}
        </p>
      </div>
      <GitFileNavigation
        v-if="compare?.files.length"
        :files="compare.files"
        :selected="selectedFile"
        :additions="compare.additions"
        :deletions="compare.deletions"
        @select="pickFile"
      />
    </aside>

    <!-- 右：比较范围 + 操作 → 提交摘要 → 当前文件 → diff -->
    <section class="git-card git-main">
      <header class="git-main__head">
        <UiIconButton
          icon="panel-left"
          :label="$t('git.toggleFiles')"
          :aria-expanded="filesOpen"
          aria-controls="git-file-navigation"
          @click="filesOpen = !filesOpen"
        />
        <UiSelectTrigger
          ref="historyTrigger"
          class="git-commit-trigger"
          icon="git-commit-vertical"
          haspopup="dialog"
          :text="subject || $t('git.chooseCommit')"
          :open="historyOpen"
          :label="$t('git.chooseCommit')"
          :disabled="!repository || reviewing"
          @click="toggleHistory"
        >
          <template v-if="compare?.kind === 'commit'"
            ><code>{{ compare.head_sha.slice(0, 8) }}</code
            >&nbsp; {{ subject }}</template
          >
          <template v-else-if="compare"
            ><code>{{ compare.base_ref }}</code> → <code>{{ compare.head_ref }}</code></template
          >
          <template v-else>{{ $t('git.chooseCommit') }}</template>
        </UiSelectTrigger>
        <span class="git-main__grow"></span>
        <GitReviewMenu
          v-if="compare"
          :key="`${compare.head_sha}:${compare.base_sha}`"
          :reviews="matchingReviews"
        />
        <button
          v-if="compare"
          type="button"
          class="git-button git-button--primary"
          :disabled="comparing || reviewing"
          @click="startReview()"
        >
          {{ reviewing ? $t('git.reviewing') : $t('git.review') }}
        </button>
      </header>
      <GitHistoryPanel
        v-model:branch="branch"
        v-model:base-branch="baseBranch"
        v-model:head-branch="headBranch"
        v-model:search="search"
        v-model:branch-mode="branchMode"
        :open="historyOpen"
        :anchor="historyTrigger?.el"
        :commits="commits"
        :selected-sha="compare?.kind === 'commit' ? compare.head_sha : ''"
        :loading="loading"
        :comparing="comparing"
        :has-more="hasMore"
        :reviewing="reviewing"
        :branch-options="branchOptions"
        :ref-options="refOptions"
        :head-options="headOptions"
        @close="closeHistory"
        @select="pickCommit"
        @history="loadHistory($event)"
        @compare="pickComparison"
      />
      <div v-if="error" class="git-alert" role="alert">
        <span>{{ error }}</span>
        <button
          type="button"
          class="git-button git-button--quiet"
          @click="repository ? loadHistory() : scan(true)"
        >
          {{ $t('common.retry') }}
        </button>
      </div>
      <div v-if="!compare" class="git-empty git-main__empty">{{ emptyText }}</div>
      <template v-else>
        <div class="git-summary">
          <GitCommitMessage :subject="subject" :message="compare.commit?.message" />
          <div class="git-byline">
            <template v-if="compare.commit"
              ><span class="git-byline__avatar" aria-hidden="true">{{ authorInitial }}</span
              ><span class="git-byline__author">{{ compare.commit.author }}</span
              ><time :datetime="compare.commit.date">{{
                shortDate(compare.commit.date)
              }}</time></template
            >
            <UiIcon v-else name="git-branch" class="git-byline__icon" />
            <button
              type="button"
              class="git-byline__sha"
              :aria-label="$t('git.copySha')"
              @click="copySha"
            >
              <code>{{ compare.head_sha.slice(0, 8) }}</code
              ><UiIcon name="copy" />
            </button>
            <span class="git-byline__base"
              >{{ $t('git.baseline') }}
              <code v-if="parentOptions.length <= 1">{{ compare.base_sha.slice(0, 8) }}</code></span
            >
            <UiSelect
              v-if="parentOptions.length > 1"
              :model-value="compare.base_sha"
              :label="$t('git.parent')"
              :options="parentOptions"
              @update:model-value="selectCommit(compare!.commit!, $event)"
            />
          </div>
        </div>
        <div v-if="!compare.files.length" class="git-empty git-main__empty">
          {{ $t('git.noChanges') }}
        </div>
        <template v-else>
          <div class="git-file-bar">
            <span class="git-file-bar__path"
              ><template v-if="currentFile?.status === 'R'">{{ currentFile.old_path }} → </template
              >{{ currentFile?.path || selectedFile }}</span
            >
            <template v-if="currentFile"
              ><span class="git-added">+{{ currentFile.additions }}</span
              ><span class="git-deleted">−{{ currentFile.deletions }}</span></template
            >
            <div class="git-file-bar__modes" role="group" :aria-label="$t('git.diffMode')">
              <UiIconButton
                icon="diff-unified"
                :label="$t('git.unified')"
                :aria-pressed="mode === 'unified'"
                @click="mode = 'unified'"
              />
              <UiIconButton
                icon="diff-split"
                :label="$t('git.split')"
                :aria-pressed="mode === 'split'"
                @click="mode = 'split'"
              />
            </div>
          </div>
          <div class="git-diff">
            <div v-if="diffError" class="git-alert" role="alert">
              <span>{{ diffError }}</span>
              <button
                type="button"
                class="git-button git-button--quiet"
                @click="selectFile(selectedFile)"
              >
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
          </div>
        </template>
      </template>
    </section>
  </main>
</template>

<style src="./git.scss" lang="scss"></style>
