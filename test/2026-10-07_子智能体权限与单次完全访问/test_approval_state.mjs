import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(resolve(root, 'package.json'));
const ts = require('typescript');
function load(relativePath, dependencies = {}) {
  const text = readFileSync(resolve(root, relativePath), 'utf8');
  const compiled = ts.transpileModule(text, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  const context = { module, exports: module.exports, require: (name) => {
    if (!(name in dependencies)) throw new Error(`Unexpected dependency ${name}`);
    return dependencies[name];
  }, setTimeout, clearTimeout, console, fetch: (...args) => globalThis.fetch(...args) };
  vm.runInNewContext(compiled, context, { filename: relativePath });
  return module.exports;
}
const model = load('static/src/components/input/approvalModel.ts');
const cli = load('cli/src/approval.ts');
const { toolMethods } = load('static/src/app/methods/taskPolling/tool.ts', {
  '../common': { debugLog() {} }, '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
  '@/locales': { t: (key) => key }, '@/components/input/approvalModel': model,
});
const { permissionMethods } = load('static/src/app/methods/ui/permission.ts', {
  '@/locales': { t: (key) => key }, '@/components/input/approvalModel': model,
  '../../../stores/policy': {}, '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
});
const item = { approval_id: 'a', tool_name: 'run_command', approval_type: 'full_access',
  status: 'pending', auto_review_required: true, auto_review_status: 'reviewing',
  human_decision: null, arguments: { command: 'echo approved' } };
function state() {
  return { ...toolMethods, currentConversationId: 'c', pendingToolApprovals: [],
    approvalSnapshotVersion: 0, resolvedToolApprovalIds: [], approvalReviewRecords: [],
    approvalPanelCollapsed: true, currentPermissionMode: 'auto_approval',
    restored: 0, restoreToolApprovalPanel() { this.restored++; this.approvalPanelCollapsed = false; },
    $forceUpdate() {} };
}

test('web snapshots preserve submitted human and terminal automatic decisions', () => {
  const prior = { ...item, human_decision: 'approved', auto_review_status: 'approved', status: 'approved' };
  const merged = model.normalizeApproval({ ...item, auto_review_status: 'pending' }, prior);
  assert.equal(merged.human_decision, 'approved');
  assert.equal(merged.auto_review_status, 'approved');
  assert.equal(merged.status, 'approved');
  assert.equal(model.normalizeApproval({ ...item, auto_review_status: 'pending' }, item).auto_review_status, 'reviewing');
});

test('full access stays visible despite hidden ordinary auto approvals', () => {
  const s = state();
  s.handleToolApprovalRequired({ approval: item, conversation_id: 'c' });
  assert.equal(s.restored, 1);
  s.approvalPanelCollapsed = true;
  s.handleToolApprovalRequired({ approval: { ...item, human_decision: 'approved' }, conversation_id: 'c' });
  assert.equal(s.restored, 1);
  assert.equal(s.approvalPanelCollapsed, true);
  assert.equal(s.pendingToolApprovals.length, 1);
});

test('partial decision does not clear the web request; terminal replay cannot restore it', () => {
  const s = state();
  s.handleToolApprovalRequired({ approval: item, conversation_id: 'c' });
  s.handleToolApprovalResolved({ approval_id: 'a', decision: 'pending', conversation_id: 'c' });
  assert.equal(s.pendingToolApprovals.length, 1);
  s.handleToolApprovalResolved({ approval_id: 'a', decision: 'approved', conversation_id: 'c' });
  clearTimeout(s.approvalAutoCloseTimer);
  assert.equal(s.pendingToolApprovals.length, 0);
  s.handleToolApprovalRequired({ approval: item, conversation_id: 'c' });
  assert.equal(s.pendingToolApprovals.length, 0);
});

test('web late pending snapshot cannot recreate a resolved request', async () => {
  const s = state();
  s.handleToolApprovalRequired({ approval: item, conversation_id: 'c' });
  const originalFetch = globalThis.fetch;
  let respond;
  globalThis.fetch = () => new Promise((resolve) => { respond = resolve; });
  try {
    const request = permissionMethods.fetchPendingToolApprovals.call(s);
    s.handleToolApprovalResolved({ approval_id: 'a', decision: 'approved', conversation_id: 'c' });
    clearTimeout(s.approvalAutoCloseTimer);
    respond({ ok: true, json: async () => ({ success: true, items: [item] }) });
    await request;
    assert.equal(s.pendingToolApprovals.length, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('events for another conversation do not change requests or progress', () => {
  const s = state();
  s.handleToolApprovalRequired({ approval: item, conversation_id: 'other' });
  s.handleAutoApprovalProgress({ approval_id: 'a', conversation_id: 'other', progress: { stage: 'start' } });
  assert.equal(s.pendingToolApprovals.length, 0);
  assert.equal(s.approvalReviewRecords.length, 0);
});

test('visible review history and per-review progress are bounded', () => {
  let records = [];
  for (let i = 0; i < 60; i++) records = model.updateReviewRecords(records, 'auto', { approval_id: String(i), progress: { stage: 'start' } });
  assert.equal(records.length, 30);
  for (let i = 0; i < 300; i++) records = model.updateReviewRecords(records, 'auto', { approval_id: '59', progress: { stage: 'model_call', message: String(i) } });
  assert.equal(records.at(-1).progress.length, 100);
});

test('CLI exposes only allow and reject and waits after human approval', () => {
  const pending = { ...cli.mergeApprovalState(item), id: 'a' };
  assert.equal([...cli.approvalActions(pending)].join(','), 'run,reject');
  const waiting = cli.mergeApprovalState({ ...item, human_decision: 'approved' }, pending);
  assert.equal(waiting.status, 'pending');
  assert.equal([...cli.approvalActions({ ...waiting, id: 'a' })].join(','), 'reject');
});

const { ChatRuntime } = load('cli/src/runtime.ts', { './approval': cli });
function runtime(gateway = {}) {
  const updates = [];
  const resolved = [];
  const messages = [];
  const instance = new ChatRuntime(gateway, {
    onApprovalRequired: (a) => updates.push(a), onApprovalResolved: (id) => resolved.push(id),
    onSystemMessage: (text) => messages.push(text), tr: (key) => key, api: { reset() {} },
  });
  return { instance, updates, resolved, messages };
}

test('CLI partial acknowledgement and progress preserve pending request', async () => {
  const r = runtime({ decideApproval: async () => ({ ...item, human_decision: 'approved' }) });
  r.instance.handleEvent('tool_approval_required', { approval: item });
  await r.instance.decideApproval('a', 'approved');
  assert.equal(r.resolved.length, 0);
  assert.equal(r.updates.at(-1).status, 'pending');
  assert.equal(r.updates.at(-1).humanDecision, 'approved');
  r.instance.handleEvent('auto_approval_progress', { approval_id: 'a', progress: { stage: 'model_call', message: 'reviewing' } });
  assert.equal(r.updates.at(-1).autoReviewProgress.message, 'reviewing');
  r.instance.handleEvent('tool_approval_resolved', { approval_id: 'a', decision: 'approved' });
  assert.equal(r.resolved.join(','), 'a');
});

test('CLI late HTTP acknowledgement cannot recreate a resolved approval', async () => {
  let respond;
  const r = runtime({ decideApproval: () => new Promise((resolve) => { respond = resolve; }) });
  r.instance.handleEvent('tool_approval_required', { approval: item });
  const request = r.instance.decideApproval('a', 'approved');
  r.instance.handleEvent('tool_approval_resolved', { approval_id: 'a', decision: 'approved' });
  const count = r.updates.length;
  respond({ ...item, human_decision: 'approved' });
  await request;
  r.instance.handleEvent('tool_approval_required', { approval: item });
  assert.equal(r.updates.length, count);
});

test('CLI failed decision restores actions and reports the failure', async () => {
  const r = runtime({ decideApproval: async () => { throw new Error('offline'); } });
  r.instance.handleEvent('tool_approval_required', { approval: item });
  await r.instance.decideApproval('a', 'approved');
  assert.equal(r.updates.at(-1).decisionPending, false);
  assert.equal(r.resolved.length, 0);
  assert.match(r.messages.at(-1), /offline/);
});

test('CLI switching to a new conversation clears the old approval', () => {
  const r = runtime();
  r.instance.handleEvent('tool_approval_required', { approval: item });
  r.instance.enterNewSession();
  assert.equal(r.resolved.join(','), 'a');
});
