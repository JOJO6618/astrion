const assert = require('node:assert/strict');
const { compile, locale, deferred } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const base = 'static/src/app/methods/ui/';
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture(search) {
  const requests = [];
  const commits = [];
  const host = { currentHostWorkspaceId: '', inputMessage: '' };
  const fetch = (url, options) => {
    const pending = deferred(); requests.push({ url, options, pending }); return pending.promise;
  };
  const globals = { fetch, URLSearchParams, window: { location: { search } } };
  const selection = compile(base + 'workspaceSelection.ts', {}, globals);
  const startup = compile(base + 'workspaceStartup.ts', {
    '@/locales': locale, './workspaceSelection': selection,
    './workspaceNavigation': { commitWorkspaceIdentity: (h, id) => { commits.push(id); h.currentHostWorkspaceId = id; } }
  }, globals);
  const respond = (index, result, ok = true) => requests[index].pending.resolve({ ok, json: async () => result });
  return { host, requests, commits, startup, respond };
}
async function run() {
  const f = fixture('?workspace_id=target');
  const pending = f.startup.prepareInitialWorkspaceIdentity(f.host, () => true);
  await flush();
  assert.equal(f.requests[0].url, '/api/workspace-view?workspace_id=target');
  f.respond(0, { success: true, data: { host_mode: true, workspace: { workspace_id: 'target' } } });
  await flush();
  assert.equal(f.commits.length, 0);
  assert.equal(f.requests[1].url, '/api/host/workspaces/select?workspace_id=target');
  assert.equal(f.requests[1].options.headers['X-Astrion-Workspace-Id'], 'target');
  f.respond(1, { success: true, data: { current_workspace_id: 'target' } });
  await pending;
  assert.equal(f.host.currentHostWorkspaceId, 'target');
  console.log('PASS review deep link validates and selects target before committing identity');

  const failed = fixture('?workspace_id=unknown');
  const failure = failed.startup.prepareInitialWorkspaceIdentity(failed.host, () => true);
  failed.respond(0, { success: false, error: 'Unknown workspace' }, false);
  await assert.rejects(failure, /Unknown workspace/);
  assert.equal(failed.requests.length, 1);
  assert.equal(failed.commits.length, 0);
  console.log('PASS invalid review scope does not load a fallback workspace');

  const stale = fixture('?workspace_id=old');
  let owns = true;
  const resolving = stale.startup.prepareInitialWorkspaceIdentity(stale.host, () => owns);
  owns = false;
  stale.respond(0, { success: true, data: { host_mode: true, workspace: { workspace_id: 'old' } } });
  assert.equal((await resolving).superseded, true);
  assert.equal(stale.requests.length, 1);
  assert.equal(stale.commits.length, 0);
  console.log('PASS late review startup cannot select or overwrite a newer view');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
