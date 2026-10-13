import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { t } from '@/locales';
import { useUiStore } from '@/stores/ui';
import { comparisonQuery, gitRequest, openReview } from './api';
import type { Branch, Commit, Comparison, Diff, Discovery, Repository, Review } from './api';

export function useGitHistory(workspaceSource: () => string) {
  const discovery = ref<Discovery>({ repositories: [], workspaces: [], warnings: [] });
  const repository = ref<Repository | null>(null);
  const workspaceId = ref('');
  const branches = ref<Branch[]>([]);
  const currentBranch = ref('');
  const branch = ref('');
  const baseBranch = ref('');
  const headBranch = ref('HEAD');
  const search = ref('');
  const commits = ref<Commit[]>([]);
  const hasMore = ref(false);
  const compare = ref<Comparison | null>(null);
  const selectedFile = ref('');
  const diff = ref<Diff | null>(null);
  const reviews = ref<Review[]>([]);
  const scanning = ref(false);
  const loading = ref(false);
  const comparing = ref(false);
  const loadingDiff = ref(false);
  const reviewing = ref(false);
  const error = ref('');
  const diffError = ref('');
  let live = true;
  let scanOwner = 0;
  let repoOwner = 0;
  let historyOwner = 0;
  let compareOwner = 0;
  let diffOwner = 0;
  const ui = useUiStore();
  onBeforeUnmount(() => {
    live = false;
  });
  const associations = computed(() =>
    discovery.value.workspaces.map((workspace) => ({
      ...workspace,
      repositories: discovery.value.repositories.filter((repo) =>
        repo.workspaces.some((w) => w.workspace_id === workspace.workspace_id)
      )
    }))
  );
  const matchingReviews = computed(() =>
    reviews.value.filter(
      (row) => row.head_sha === compare.value?.head_sha && row.base_sha === compare.value?.base_sha
    )
  );
  const message = (exc: unknown) => (exc instanceof Error ? exc.message : String(exc));
  const scoped = (extra: Record<string, string> = {}) => ({
    workspace_id: workspaceId.value,
    ...extra
  });

  async function scan(refresh = false) {
    if (!workspaceId.value) return;
    const owner = ++scanOwner;
    scanning.value = true;
    error.value = '';
    try {
      const result = await gitRequest<Discovery>(
        'repositories',
        scoped(refresh ? { refresh: '1' } : {})
      );
      if (!live || owner !== scanOwner) return;
      discovery.value = result;
      if (repository.value) {
        const selected = result.repositories.find(
          (r) =>
            r.id === repository.value?.id &&
            r.workspaces.some((w) => w.workspace_id === workspaceId.value)
        );
        if (selected) await selectRepository(selected, workspaceId.value);
        else {
          repository.value = null;
          commits.value = [];
          compare.value = null;
          diff.value = null;
          branches.value = [];
          currentBranch.value = '';
          reviews.value = [];
          selectedFile.value = '';
          hasMore.value = false;
          loading.value = false;
          comparing.value = false;
          loadingDiff.value = false;
          ++repoOwner;
          ++historyOwner;
          ++compareOwner;
          ++diffOwner;
        }
      } else {
        const first = result.repositories[0];
        if (first) await selectRepository(first, workspaceId.value);
      }
    } catch (exc) {
      if (live && owner === scanOwner) error.value = message(exc);
    } finally {
      if (live && owner === scanOwner) scanning.value = false;
    }
  }

  async function selectRepository(repo: Repository, ws: string) {
    if (ws !== workspaceId.value || !repo.workspaces.some((w) => w.workspace_id === ws)) return;
    const owner = ++repoOwner;
    ++historyOwner;
    ++compareOwner;
    ++diffOwner;
    repository.value = repo;
    workspaceId.value = ws;
    branches.value = [];
    currentBranch.value = '';
    commits.value = [];
    compare.value = null;
    diff.value = null;
    reviews.value = [];
    selectedFile.value = '';
    branch.value = '';
    search.value = '';
    baseBranch.value = '';
    headBranch.value = 'HEAD';
    error.value = '';
    hasMore.value = false;
    comparing.value = false;
    loadingDiff.value = false;
    diffError.value = '';
    loading.value = true;
    const query = scoped();
    try {
      const [branchData, reviewData] = await Promise.all([
        gitRequest<{ branches: Branch[]; current_branch: string }>(`${repo.id}/branches`, query),
        gitRequest<Review[]>(`${repo.id}/reviews`, query)
      ]);
      if (!live || owner !== repoOwner) return;
      branches.value = branchData.branches;
      currentBranch.value = branchData.current_branch;
      reviews.value = reviewData;
      baseBranch.value =
        branchData.branches.find((b) => b.name === 'main')?.ref ||
        branchData.branches.find((b) => b.name === 'master')?.ref ||
        branchData.branches[0]?.ref ||
        '';
      await loadHistory();
    } catch (exc) {
      if (live && owner === repoOwner) error.value = message(exc);
    } finally {
      if (live && owner === repoOwner) loading.value = false;
    }
  }

  async function loadHistory(append = false) {
    if (!repository.value) return;
    const owner = ++historyOwner;
    const scope = repoOwner;
    loading.value = true;
    error.value = '';
    if (!append) {
      ++compareOwner;
      ++diffOwner;
      commits.value = [];
      hasMore.value = false;
      compare.value = null;
      diff.value = null;
      selectedFile.value = '';
      comparing.value = false;
      loadingDiff.value = false;
    }
    const offset = append ? commits.value.length : 0;
    try {
      const result = await gitRequest<{ commits: Commit[]; has_more: boolean }>(
        `${repository.value.id}/history`,
        scoped({ ref: branch.value, search: search.value, offset: String(offset) })
      );
      if (!live || owner !== historyOwner || scope !== repoOwner) return;
      commits.value = append ? [...commits.value, ...result.commits] : result.commits;
      hasMore.value = result.has_more;
      if (!append && result.commits[0]) await selectCommit(result.commits[0]);
    } catch (exc) {
      if (live && owner === historyOwner && scope === repoOwner) error.value = message(exc);
    } finally {
      if (live && owner === historyOwner && scope === repoOwner) loading.value = false;
    }
  }

  async function loadComparison(query: Record<string, string>) {
    if (!repository.value) return;
    const owner = ++compareOwner;
    const scope = repoOwner;
    ++diffOwner;
    diff.value = null;
    selectedFile.value = '';
    diffError.value = '';
    compare.value = null;
    comparing.value = true;
    loadingDiff.value = false;
    error.value = '';
    try {
      const result = await gitRequest<Comparison>(
        `${repository.value.id}/comparison`,
        scoped(query)
      );
      if (!live || owner !== compareOwner || scope !== repoOwner) return;
      compare.value = result;
      if (result.files[0]) await selectFile(result.files[0].path);
    } catch (exc) {
      if (live && owner === compareOwner && scope === repoOwner) error.value = message(exc);
    } finally {
      if (live && owner === compareOwner && scope === repoOwner) comparing.value = false;
    }
  }

  function selectCommit(commit: Commit, parent = '') {
    return loadComparison({ sha: commit.sha, ...(parent ? { parent } : {}) });
  }
  function compareBranches() {
    return loadComparison({ base: baseBranch.value, head: headBranch.value });
  }

  async function selectFile(path: string, append = false) {
    if (!repository.value || !compare.value) return;
    const owner = ++diffOwner;
    const scope = compareOwner;
    const offset = append ? diff.value?.next_offset || 0 : 0;
    selectedFile.value = path;
    loadingDiff.value = true;
    diffError.value = '';
    if (!append) diff.value = null;
    try {
      const result = await gitRequest<Diff>(
        `${repository.value.id}/diff`,
        scoped({ ...comparisonQuery(compare.value), path, offset: String(offset) })
      );
      if (!live || owner !== diffOwner || scope !== compareOwner) return;
      diff.value =
        append && diff.value
          ? { ...result, lines: [...diff.value.lines, ...result.lines] }
          : result;
    } catch (exc) {
      if (live && owner === diffOwner && scope === compareOwner) diffError.value = message(exc);
    } finally {
      if (live && owner === diffOwner && scope === compareOwner) loadingDiff.value = false;
    }
  }

  async function startReview() {
    if (!repository.value || !compare.value || reviewing.value) return;
    const repo = repository.value;
    const ws = workspaceId.value;
    const payload = comparisonQuery(compare.value);
    reviewing.value = true;
    try {
      const result = await gitRequest<Review>(`${repo.id}/reviews`, { workspace_id: ws }, payload);
      if (!live) return;
      if (result.warning) ui.pushToast({ message: result.warning, type: 'warning' });
      openReview(result);
    } catch (exc) {
      if (live)
        ui.pushToast({ title: t('git.reviewFailed'), message: message(exc), type: 'error' });
    } finally {
      if (live) reviewing.value = false;
    }
  }

  // The app commits this identity only after route validation and workspace selection.
  watch(
    workspaceSource,
    (id) => {
      if (!id || workspaceId.value) return;
      workspaceId.value = id;
      const url = new URL(window.location.href);
      url.searchParams.set('workspace_id', id);
      window.history.replaceState(window.history.state, '', url);
      void scan();
    },
    { immediate: true }
  );

  return {
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
    associations,
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
  };
}
