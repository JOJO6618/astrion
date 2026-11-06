const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const { test } = require('node:test');
const root = path.resolve(__dirname, '../..');

function load(relative, dependencies = {}) {
  const js = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const exports = {};
  vm.runInNewContext(js, {
    exports, console, setTimeout, clearTimeout,
    require(name) {
      if (!(name in dependencies)) throw new Error(`Unexpected dependency: ${name}`);
      return dependencies[name];
    }
  }, { filename: relative });
  return exports;
}
const model = load('static/src/components/input/approvalModel.ts');
const methods = load('static/src/app/methods/taskPolling/approvals.ts', {
  '@/locales': { t: key => key },
  '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
  '../auxiliaryOwnership': { invalidateAuxiliaryRequest() {} },
  '@/components/input/approvalModel': model
}).approvalMethods;

function context() {
  return {
    currentConversationId: 'c', resolvedToolApprovalIds: [], approvalSnapshotVersion: 0,
    pendingToolApprovals: [{ approval_id: 'a', tool_name: 'run_command', status: 'pending',
      auto_review_required: true, auto_review_status: 'reviewing', human_approval_required: false }],
    approvalReviewRecords: [], approvalPanelCollapsed: false, $forceUpdate() {}
  };
}

test('review error is terminal and is not mistaken for a denial or active review', () => {
  assert.equal(model.statusFromProgress({ stage: 'done', decision: 'error' }), 'error');
  assert.equal(model.statusFromProgress({ stage: 'model_call' }, 'error'), 'error');
  const old = { approval_id: 'a', tool_name: 'run_command', status: 'expired', auto_review_status: 'error' };
  const normalized = model.normalizeApproval({ ...old, status: 'pending', auto_review_status: 'reviewing' }, old);
  assert.equal(normalized.status, 'expired');
  assert.equal(normalized.auto_review_status, 'error');
  assert.equal(model.needsHumanDecision(normalized), false);
});

test('error completion removes pending request and records actual failure', () => {
  const ctx = context();
  methods.handleAutoApprovalProgress.call(ctx, {
    conversation_id: 'c', approval_id: 'a', progress: { stage: 'done', decision: 'error', code: 'review_timeout' }
  });
  methods.handleToolApprovalResolved.call(ctx, {
    conversation_id: 'c', approval_id: 'a', decision: 'error', reason: 'timeout', code: 'review_timeout'
  });
  assert.equal(ctx.pendingToolApprovals.length, 0);
  assert.equal(ctx.approvalPanelCollapsed, true);
  assert.equal(ctx.approvalReviewRecords[0].final_decision, 'error');
  assert.equal(ctx.approvalReviewRecords[0].reason, 'timeout');
  assert.ok(ctx.resolvedToolApprovalIds.includes('a'));
  methods.handleToolApprovalRequired.call(ctx, { conversation_id: 'c', approval: {
    approval_id: 'a', tool_name: 'run_command', status: 'pending'
  } });
  assert.equal(ctx.pendingToolApprovals.length, 0);
});

test('full-access switch off preserves human approval while automatic-only does not require it', () => {
  const base = { approval_id: 'a', tool_name: 'run_command', approval_type: 'full_access', status: 'pending',
    auto_review_required: true, auto_review_status: 'approved' };
  assert.equal(model.needsHumanDecision({ ...base, human_approval_required: true }), true);
  assert.equal(model.needsHumanDecision({ ...base, human_approval_required: false }), false);
  assert.equal(model.needsHumanDecision({ ...base, human_approval_required: true, human_decision: 'approved' }), false);
});
