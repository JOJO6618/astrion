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
  vm.runInNewContext(
    js,
    {
      exports,
      console,
      setTimeout,
      clearTimeout,
      require(name) {
        if (!(name in dependencies)) throw new Error(`Unexpected dependency: ${name}`);
        return dependencies[name];
      }
    },
    { filename: relative }
  );
  return exports;
}

const model = load('static/src/components/input/approvalModel.ts');
const methods = load('static/src/app/methods/taskPolling/approvals.ts', {
  '@/locales': { t: (key) => key },
  '../auxiliaryOwnership': { invalidateAuxiliaryRequest() {} },
  '@/components/input/approvalModel': model
}).approvalMethods;

function context() {
  return {
    currentConversationId: 'c',
    currentPermissionMode: 'auto_approval',
    resolvedToolApprovalIds: [],
    approvalSnapshotVersion: 0,
    pendingToolApprovals: [],
    decidingApprovalIds: [],
    approvalReviewRecords: [],
    approvalPanelCollapsed: false,
    approvalAutoCloseTimer: null,
    restoreToolApprovalPanel() {
      this.approvalPanelCollapsed = false;
    },
    $forceUpdate() {}
  };
}

function request(ctx, overrides = {}) {
  methods.handleToolApprovalRequired.call(ctx, {
    conversation_id: 'c',
    approval: {
      approval_id: 'a',
      tool_name: 'run_command',
      approval_type: 'full_access',
      status: 'pending',
      auto_review_required: true,
      human_approval_required: true,
      ...overrides
    }
  });
}

test('human-approved full access is no longer a pending human decision', () => {
  const ctx = context();
  request(ctx);
  assert.equal(model.needsHumanDecision(ctx.pendingToolApprovals[0]), true);
  methods.handleToolApprovalRequired.call(ctx, {
    conversation_id: 'c',
    approval: {
      approval_id: 'a',
      tool_name: 'run_command',
      approval_type: 'full_access',
      status: 'pending',
      auto_review_required: true,
      human_approval_required: true,
      human_decision: 'approved',
      auto_review_status: 'reviewing'
    }
  });
  // 人工已批准、等待 AI 审核：状态栏入口不应继续显示。
  assert.equal(model.needsHumanDecision(ctx.pendingToolApprovals[0]), false);
});

test('automatic-only request never asks for a human decision', () => {
  const ctx = context();
  request(ctx, { human_approval_required: false });
  assert.equal(model.needsHumanDecision(ctx.pendingToolApprovals[0]), false);
});

test('terminal task clears residual approvals and folds the panel', () => {
  const ctx = context();
  request(ctx);
  ctx.decidingApprovalIds = ['a'];
  methods.clearResidualToolApprovals.call(ctx);
  assert.equal(ctx.pendingToolApprovals.length, 0);
  assert.equal(ctx.decidingApprovalIds.length, 0);
  assert.equal(ctx.approvalPanelCollapsed, true);
  assert.ok(ctx.approvalSnapshotVersion > 0);
});

test('clearing without residual approvals is a no-op', () => {
  const ctx = context();
  const version = ctx.approvalSnapshotVersion;
  const collapsed = ctx.approvalPanelCollapsed;
  methods.clearResidualToolApprovals.call(ctx);
  assert.equal(ctx.approvalSnapshotVersion, version);
  assert.equal(ctx.approvalPanelCollapsed, collapsed);
});
