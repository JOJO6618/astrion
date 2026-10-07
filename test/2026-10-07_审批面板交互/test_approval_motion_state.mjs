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
const vue = require('vue');
const { parse } = require('@vue/compiler-sfc');
function evaluate(source, dependencies, globals = {}) {
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module, exports: module.exports, ...globals,
    require: (name) => {
      if (!(name in dependencies)) throw new Error(`Unexpected dependency ${name}`);
      return dependencies[name];
    },
    setTimeout, clearTimeout,
  });
  return module.exports;
}
const source = (path) => readFileSync(resolve(root, path), 'utf8');
const model = evaluate(source('static/src/components/input/approvalModel.ts'), {});
const { toolMethods } = evaluate(source('static/src/app/methods/taskPolling/tool.ts'), {
  '../common': { debugLog() {} },
  '../../../stores/personalization': { usePersonalizationStore: () => ({ form: {} }) },
  '@/locales': { t: (key) => key },
  '@/components/input/approvalModel': model,
});
const approval = {
  approval_id: 'a', tool_name: 'run_command', approval_type: 'full_access',
  arguments: { command: 'echo example' }, status: 'pending',
  auto_review_required: true, auto_review_status: 'reviewing',
};
function state() {
  return {
    ...toolMethods, currentConversationId: 'c', pendingToolApprovals: [{ ...approval }],
    approvalSnapshotVersion: 0, resolvedToolApprovalIds: [],
    approvalReviewRecords: [{ id: 'review', kind: 'auto', approval_id: 'a', progress: [] }],
    approvalPanelCollapsed: false, $forceUpdate() {},
  };
}
function dock() {
  const props = vue.reactive({
    visible: true, collapsed: false, statusVisible: false,
    approvals: [{ ...approval }], reviewRecords: [],
  });
  const events = [];
  class Element {}
  class HTMLElement extends Element { inert = false; }
  const script = parse(source('static/src/components/input/ComposerApprovalDock.vue')).descriptor.scriptSetup.content;
  const scope = vue.effectScope();
  const api = scope.run(() => evaluate(script + `\nmodule.exports = {
    panelVisible, restoreVisible, displayApprovals, startPanelTransition, finishPanelTransition
  };`, {
    vue: { ...vue, onBeforeUnmount() {} },
    '@/components/panels/ToolApprovalPanel.vue': {},
    './ApprovalChevron.vue': {},
  }, {
    defineProps: () => props,
    defineEmits: () => (...args) => events.push(args),
    Element, HTMLElement,
  }));
  return { props, api, events, element: new HTMLElement(), dispose: () => scope.stop() };
}

test('terminal approval clears its card state immediately and ignores late progress', () => {
  const s = state();
  s.handleToolApprovalResolved({ approval_id: 'a', decision: 'approved', conversation_id: 'c' });
  assert.equal(s.pendingToolApprovals.length, 0);
  assert.equal(s.approvalReviewRecords.length, 1);
  assert.equal(s.approvalReviewRecords[0].approval.status, 'approved');
  assert.equal(s.approvalPanelCollapsed, true);
  assert.equal(s.approvalAutoCloseTimer, null);
  s.handleAutoApprovalProgress({ approval_id: 'a', conversation_id: 'c', progress: { stage: 'done', decision: 'approved' } });
  assert.equal(s.approvalReviewRecords.length, 1);
  assert.equal(s.pendingToolApprovals.length, 0);
});

test('human partial approval keeps the automatic review and pending card', () => {
  const s = state();
  s.handleToolApprovalResolved({ approval_id: 'a', decision: 'pending', conversation_id: 'c' });
  assert.equal(s.pendingToolApprovals.length, 1);
  assert.equal(s.approvalReviewRecords.length, 1);
  assert.equal(s.approvalPanelCollapsed, false);
});

test('leaving panel keeps its last content without creating a completed restore button', async () => {
  const d = dock();
  try {
    d.props.approvals = [];
    d.props.visible = false;
    d.props.collapsed = true;
    await vue.nextTick();
    d.api.startPanelTransition(d.element);
    assert.equal(d.api.panelVisible.value, false);
    assert.equal(d.api.displayApprovals.value[0].arguments.command, 'echo example');
    assert.equal(d.element.inert, true);
    assert.equal(d.api.restoreVisible.value, false);
    d.api.finishPanelTransition(d.element);
    assert.equal(d.api.restoreVisible.value, false);
    assert.equal(d.events.at(-2)[1], false);
  } finally { d.dispose(); }
});

test('restore button rises during collapse; interrupted restore keeps live progress', async () => {
  const d = dock();
  try {
    d.props.collapsed = true;
    d.api.startPanelTransition(d.element);
    await vue.nextTick();
    assert.equal(d.api.restoreVisible.value, true);
    d.api.finishPanelTransition(d.element);
    assert.equal(d.api.restoreVisible.value, true);
    d.props.collapsed = false;
    d.props.approvals = [{ ...approval, auto_review_status: 'approved' }];
    await vue.nextTick();
    d.api.startPanelTransition(d.element);
    assert.equal(d.element.inert, false);
    assert.equal(d.api.restoreVisible.value, false);
    assert.equal(d.api.displayApprovals.value[0].auto_review_status, 'approved');
    d.api.finishPanelTransition(d.element);
  } finally { d.dispose(); }
});
