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
    currentConversationId: 'c', currentPermissionMode: 'unrestricted',
    resolvedToolApprovalIds: [], approvalSnapshotVersion: 0,
    pendingToolApprovals: [], approvalReviewRecords: [], approvalPanelCollapsed: true,
    restoreToolApprovalPanel() { this.approvalPanelCollapsed = false; }, $forceUpdate() {}
  };
}

function request(ctx, automatic) {
  methods.handleToolApprovalRequired.call(ctx, {
    conversation_id: 'c', approval: {
      approval_id: 'a', tool_name: 'run_command', approval_type: 'full_access',
      status: 'pending', auto_review_required: automatic,
      human_approval_required: !automatic
    }
  });
}

test('unrestricted auto-only request retains explicit flags and review progress', () => {
  const ctx = context();
  request(ctx, true);
  assert.equal(ctx.pendingToolApprovals[0].auto_review_required, true);
  assert.equal(model.needsHumanDecision(ctx.pendingToolApprovals[0]), false);
  methods.handleAutoApprovalProgress.call(ctx, {
    conversation_id: 'c', approval_id: 'a',
    progress: { stage: 'model_call', message: 'reviewing' }
  });
  const row = ctx.pendingToolApprovals[0];
  assert.equal(row.auto_review_required, true);
  assert.equal(row.auto_review_status, 'reviewing');
  assert.equal(row.auto_review_progress.length, 1);
  // 2026-11-09 拍板：AI 可自动审核的请求不自动展开面板，进度仅手动查看。
  assert.equal(ctx.approvalPanelCollapsed, true);
});

test('unrestricted closed switch retains manual controls and no automatic review', () => {
  const ctx = context();
  request(ctx, false);
  const row = ctx.pendingToolApprovals[0];
  assert.equal(row.auto_review_required, false);
  assert.equal(model.needsHumanDecision(row), true);
  assert.equal(ctx.approvalPanelCollapsed, false);
});

test('unrestricted automatic terminal decisions clear pending and retain their actual status', () => {
  for (const decision of ['approved', 'rejected', 'error']) {
    const ctx = context();
    request(ctx, true);
    methods.handleToolApprovalResolved.call(ctx, {
      conversation_id: 'c', approval_id: 'a', decision, reason: 'synthetic'
    });
    assert.equal(ctx.pendingToolApprovals.length, 0);
    assert.equal(ctx.approvalReviewRecords[0].final_decision, decision);
    assert.equal(ctx.approvalPanelCollapsed, true);
    assert.ok(ctx.resolvedToolApprovalIds.includes('a'));
  }
});
