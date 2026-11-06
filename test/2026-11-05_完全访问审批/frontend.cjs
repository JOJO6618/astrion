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
const approval = load('static/src/components/input/approvalModel.ts');

test('manual waiting distinguishes automatic-only, human-first and terminal states', () => {
  assert.equal(approval.needsHumanDecision({ approval_id: 'a', tool_name: 'run_command', status: 'pending', human_approval_required: true }), true);
  for (const patch of [
    { human_approval_required: false }, { human_decision: 'approved' },
    { status: 'approved' }, { status: 'rejected' }, { status: 'expired' }
  ]) {
    assert.equal(approval.needsHumanDecision({ approval_id: 'a', tool_name: 'run_command', status: 'pending', ...patch }), false);
  }
  const normalized = approval.normalizeApproval({ approval_id: 'a', tool_name: 'run_command', status: 'pending', human_approval_required: false }, undefined, true);
  assert.equal(approval.needsHumanDecision(normalized), false);
});

function uiStore() {
  const { useUiStore } = load('static/src/stores/ui.ts', {
    pinia: { defineStore: (_, options) => () => {
      const store = options.state();
      for (const [key, method] of Object.entries(options.actions)) store[key] = method.bind(store);
      return store;
    } },
    '@/locales': { t: key => key }
  });
  return useUiStore();
}

test('confirmation forwards optional checkbox without changing ordinary confirms', async () => {
  const ui = uiStore();
  let checked = false;
  const pending = ui.requestConfirm({ checkboxLabel: 'remember', onCheckboxChange: value => { checked = value; } });
  assert.equal(ui.confirmDialog.checkboxChecked, false);
  ui.confirmDialog.onCheckboxChange(true);
  ui.resolveConfirm(true);
  assert.equal(await pending, true);
  assert.equal(checked, true);
  const plain = ui.requestConfirm({});
  ui.resolveConfirm(false);
  assert.equal(await plain, false);
});

function permissionHarness({ dismissed = false, confirm = true, remember = false } = {}) {
  const form = { full_access_warning_dismissed: dismissed };
  const updates = [], switches = [], prompts = [];
  const methods = load('static/src/app/methods/ui/permission.ts', {
    '@/locales': { t: key => key, currentLocale: { value: 'zh-CN' } },
    '@/components/input/approvalModel': approval,
    '../../../stores/policy': { usePolicyStore: () => ({}) },
    '../../../stores/personalization': { usePersonalizationStore: () => ({ form, updateField: value => updates.push(value) }) },
    '../auxiliaryOwnership': { beginAuxiliaryRequest: () => () => true, invalidateAuxiliaryRequest() {} },
    '../workspaceRequestScope': { workspaceScopedFetch: () => { throw new Error('Unexpected server call'); } }
  }).permissionMethods;
  const ctx = {
    executionModeEnabled: true, currentExecutionMode: 'sandbox', currentConversationId: '',
    uiRequestConfirm: async options => {
      prompts.push(options);
      options.onCheckboxChange(remember);
      return confirm;
    },
    setNewConversationMode: (kind, target) => switches.push([kind, target])
  };
  return { run: () => methods.changeExecutionMode.call(ctx, 'direct'), ctx, updates, switches, prompts };
}

test('accepting full access switches and stores user preference only when checked', async () => {
  const h = permissionHarness({ remember: true });
  await h.run();
  assert.equal(h.prompts.length, 1);
  assert.equal(h.prompts[0].confirmVariant, 'danger');
  assert.equal(h.switches[0][1], 'direct');
  assert.equal(h.updates[0].key, 'full_access_warning_dismissed');
  assert.equal(h.updates[0].value, true);
});

test('cancelling full access leaves sandbox and does not store checkbox', async () => {
  const h = permissionHarness({ confirm: false, remember: true });
  await h.run();
  assert.equal(h.switches.length, 0);
  assert.equal(h.updates.length, 0);
});

test('warning repeats unless dismissed, and restoring direct does not prompt', async () => {
  const ordinary = permissionHarness();
  await ordinary.run();
  assert.equal(ordinary.updates.length, 0);
  const dismissed = permissionHarness({ dismissed: true });
  await dismissed.run();
  assert.equal(dismissed.prompts.length, 0);
  assert.equal(dismissed.switches.length, 1);
  const restored = permissionHarness();
  restored.ctx.currentExecutionMode = 'direct';
  await restored.run();
  assert.equal(restored.prompts.length, 0);
});
