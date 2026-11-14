const assert = require('node:assert/strict');
const { compile, deferred, pinia, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const tests = [];
const test = (name, run) => tests.push([name, run]);
const plain = (value) => JSON.parse(JSON.stringify(value));
const response = (data, ok = true) => ({ ok, json: async () => data });
const state = (directory = '/workspace/tree', branch = 'task') => ({ directory, branch, active: true });
function fixture() {
  const requests = [];
  const fetch = (url, options) => {
    const pending = deferred();
    requests.push({ url, options, ...pending });
    return pending.promise;
  };
  const { useGitWorktreeStore } = compile('static/src/stores/gitWorktree.ts', { pinia }, { fetch });
  const store = useGitWorktreeStore();
  store.hydrate({ git_worktree_enabled: true, git_worktree: state() }, 'A', 'ws');
  return { requests, store, fetch, useGitWorktreeStore };
}

test('disabled and old conversations issue no worktree query', async () => {
  const f = fixture();
  f.store.hydrate({}, 'old', 'ws');
  await f.store.refresh('old', 'ws');
  assert.equal(f.requests.length, 0);
  assert.equal(f.store.enabled, false);
});

test('new page selection is captured without creating a conversation or worktree', async () => {
  const f = fixture();
  f.store.hydrate({ git_worktree_enabled: true }, null, 'ws');
  const target = { action: 'create', branch_name: 'feature/task', start_ref: 'v1', include_uncommitted: false };
  assert.equal(await f.store.select(target, null, 'ws'), true);
  assert.equal(f.requests.length, 0);
  const module = compile('static/src/app/methods/ui/newConversation.ts', {
    '@/locales': locale, '../conversation/session': {},
    '@/stores/browserTools': { useBrowserToolsStore: () => ({ draft: false }) },
    '@/stores/gitWorktree': { useGitWorktreeStore: f.useGitWorktreeStore }
  }, { fetch: f.fetch });
  const captured = module.captureNewConversationSettings({ newConversationRestoreModes: {} });
  assert.deepEqual(plain(captured.git_worktree_target), target);
});

test('late query cannot overwrite a different conversation', async () => {
  const f = fixture();
  const pending = f.store.refresh('A', 'ws');
  f.store.hydrate({ git_worktree_enabled: true, git_worktree: state('/other', 'other') }, 'B', 'ws');
  f.requests[0].resolve(response({ success: true, data: { ...state('/old', 'old'), worktrees: [], refs: [] } }));
  await pending;
  assert.equal(f.store.directory, '/other');
  assert.equal(f.store.scope, 'ws:B');
});

test('late selection cannot change the newly selected workspace', async () => {
  const f = fixture();
  const pending = f.store.select({ action: 'enter', branch_name: 'next' }, 'A', 'ws');
  f.store.hydrate({ git_worktree_enabled: true }, 'B', 'other-ws');
  f.requests[0].resolve(response({ success: true, state: state('/old', 'old') }));
  assert.equal(await pending, false);
  assert.equal(f.store.scope, 'other-ws:B');
  assert.equal(f.store.directory, '');
  assert.equal(f.store.busy, false);
});

test('query during selection does not invalidate its generation or busy state', async () => {
  const f = fixture();
  const pending = f.store.select({ action: 'enter', branch_name: 'next' }, 'A', 'ws');
  const generation = f.store.generation;
  await f.store.refresh('A', 'ws');
  assert.equal(f.requests.length, 1);
  assert.equal(f.store.generation, generation);
  f.requests[0].resolve(response({ success: true, state: state('/next', 'next') }));
  assert.equal(await pending, true);
  assert.equal(f.store.busy, false);
  assert.equal(f.store.directory, '/next');
});

test('failed switch retains actual root and exposes the server error', async () => {
  const f = fixture();
  const pending = f.store.select(null, 'A', 'ws');
  f.requests[0].resolve(response({ success: false, error: 'main is running' }, false));
  assert.equal(await pending, false);
  assert.equal(f.store.directory, '/workspace/tree');
  assert.equal(f.store.error, 'main is running');
  assert.equal(f.store.busy, false);
});

test('missing binding is displayed while the valid exit choice remains available', async () => {
  const f = fixture();
  const pending = f.store.refresh('A', 'ws');
  f.requests[0].resolve(response({ success: true, data: { ...state('/missing'), unavailable: 'missing directory',
    worktrees: [{ directory: '/workspace', branch: 'main', is_main: true }], refs: ['main'] } }));
  await pending;
  assert.equal(f.store.error, 'missing directory');
  assert.equal(f.store.directory, '/missing');
  assert.equal(f.store.worktrees[0].is_main, true);
});

for (const kind of ['summary', 'diff']) {
  test(`late Git ${kind} cannot overwrite a newer worktree`, async () => {
    const f = fixture();
    const { gitMethods } = compile('static/src/app/methods/ui/git.ts', {
      '@/locales': locale,
      '../conversation/session': { conversationViewEpoch: (host) => host.epoch },
      '@/stores/gitWorktree': { useGitWorktreeStore: f.useGitWorktreeStore }
    }, { fetch: f.fetch, URLSearchParams });
    const host = { ...gitMethods, epoch: 1, currentConversationId: 'A', currentHostWorkspaceId: 'ws',
      gitChangesPanelOpen: kind === 'diff', projectGitSummary: { branch: 'initial' },
      gitChangesDiff: { has_git: true, marker: 'initial' } };
    const pending = kind === 'summary' ? host.refreshProjectGitSummary() : host.loadGitChangesDiff();
    assert.match(f.requests[0].url, /conversation_id=A/);
    f.store.generation++;
    f.requests[0].resolve(response({ success: true, data: { branch: 'late', marker: 'late', has_git: true } }));
    await pending;
    assert.equal(host.projectGitSummary.branch, 'initial');
    assert.equal(host.gitChangesDiff.marker, 'initial');
  });
}

test('new worktree generation supersedes an in-flight summary and owns its finalizer', async () => {
  const f = fixture();
  const { gitMethods } = compile('static/src/app/methods/ui/git.ts', {
    '@/locales': locale,
    '../conversation/session': { conversationViewEpoch: (host) => host.epoch },
    '@/stores/gitWorktree': { useGitWorktreeStore: f.useGitWorktreeStore }
  }, { fetch: f.fetch, URLSearchParams });
  const host = { ...gitMethods, epoch: 1, currentConversationId: 'A', currentHostWorkspaceId: 'ws' };
  const old = host.refreshProjectGitSummary();
  f.store.generation++;
  const current = host.refreshProjectGitSummary();
  assert.equal(f.requests.length, 2);
  f.requests[0].resolve(response({ success: true, data: state('/old', 'old') }));
  await old;
  assert.equal(host.projectGitSummaryRefreshing, true);
  assert.equal(host.projectGitSummary, undefined);
  f.requests[1].resolve(response({ success: true, data: { has_git: true, branch: 'task',
    project_path: '/missing', git_worktree: state('/missing'), binding_error: 'missing' } }));
  await current;
  assert.equal(host.projectGitSummaryRefreshing, false);
  assert.equal(host.projectGitSummary.has_git, true);
  assert.equal(host.projectGitSummary.binding_error, 'missing');
  assert.equal(f.store.directory, '/missing');
  assert.equal(f.store.active, true);
});

test('draft branch selection survives an earlier worktree query', async () => {
  const f = fixture();
  f.store.hydrate({ git_worktree_enabled: true }, null, 'ws');
  const pending = f.store.refresh(null, 'ws');
  await f.store.select({ action: 'create', branch_name: 'draft/task', start_ref: 'HEAD', include_uncommitted: false }, null, 'ws');
  f.requests[0].resolve(response({ success: true, data: { ...state('/workspace', 'main'), refs: ['main'], worktrees: [] } }));
  await pending;
  assert.equal(f.store.branch, 'draft/task');
  assert.equal(f.store.draftTarget.branch_name, 'draft/task');
});

(async () => {
  for (const [name, run] of tests) {
    await run();
    console.log(`PASS ${name}`);
  }
  console.log(`${tests.length} frontend worktree tests passed`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
