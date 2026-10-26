const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');

const filename = 'static/src/components/input/InputComposer.vue';
const source = fs.readFileSync(filename, 'utf8');
const { descriptor } = compiler.parse(source, { filename });
const ast = ts.createSourceFile(filename + '.ts', descriptor.scriptSetup.content,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
// Evaluate the production visibility calculations, rather than copying their logic.
function expression(name) {
  for (const statement of ast.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (declaration.name.getText(ast) === name) return declaration.initializer.getText(ast);
    }
  }
  throw new Error(`Missing production calculation: ${name}`);
}
const compiled = compiler.compileTemplate({
  source: descriptor.template.content, filename, id: 'composer-approval-regression'
});
assert.deepEqual(compiled.errors, []);
const moduleCode = ts.transpileModule(compiled.code, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
}).outputText;
const compiledModule = { exports: {} };
vm.runInNewContext(moduleCode, {
  module: compiledModule, exports: compiledModule.exports, require: () => vue
});

async function render(chatMode, approvals, collapsed) {
  const props = vue.reactive({ chatMode, toolApprovals: approvals,
    approvalPanelCollapsed: collapsed, quickMenuOpen: false });
  const scope = { props, computed: vue.computed,
    skillSlashMenuOpen: vue.ref(false), fileAtOpen: vue.ref(false) };
  const visible = vm.runInNewContext(expression('approvalDockVisible'), scope);
  const expanded = vm.runInNewContext(expression('approvalExpanded'), {
    ...scope, approvalDockVisible: visible
  });
  const state = {
    ...props, approvalDockVisible: visible.value, approvalExpanded: expanded.value,
    approvalReviewRecords: [{ id: 'old-review' }],
    inputMessage: 'Ready to send', isConnected: true, inputLocked: false,
    sendButtonDisabled: false, selectedImages: [], selectedVideos: [], selectedFiles: [],
    currentWorkModeOption: {}, currentPermissionOption: {}, currentPermissionMeta: {},
    iconStyle: () => ({}), toolCategoryIcon: () => '',
    handleSlashMenuTransitionStart() {}, handleSlashMenuAfterEnter() {},
    handleSlashMenuAfterLeave() {},
    $t: key => key, $emit: () => {}, props
  };
  const context = new Proxy(state, { get: (object, key) => key in object ? object[key] : false });
  let dockMounts = 0;
  const app = vue.createSSRApp({
    render() { return compiledModule.exports.render(context, []); }
  });
  const components = [...descriptor.template.content.matchAll(/<([A-Z][A-Za-z]+)/g)]
    .map(match => match[1]);
  for (const name of new Set(components)) {
    if (name === 'Transition') continue;
    app.component(name, vue.defineComponent({
      inheritAttrs: false,
      setup() {
        if (name === 'ComposerApprovalDock') dockMounts++;
        return () => vue.h('div', { 'data-component': name });
      }
    }));
  }
  const html = await renderToString(app);
  assert.ok(html.includes('data-component="EditorContent"'), 'Real input template retains editor');
  assert.ok(html.includes('send-btn'), 'Send/stop button remains rendered');
  assert.ok(html.includes('context-usage-switcher'), 'Token ring remains rendered');
  if (chatMode) {
    assert.equal(dockMounts, 0, 'Chat must not instantiate the approval dock');
    assert.equal(visible.value, false, 'Chat must not reserve approval layout');
    assert.equal(expanded.value, false, 'Chat must not enter expanded approval layout');
    assert.ok(html.includes('chatMode.online') || html.includes('chatMode.offline'));
    assert.ok(!html.includes('permission-switcher'));
  } else {
    assert.equal(dockMounts, 1, 'Agent retains its approval component');
    assert.equal(visible.value, !collapsed || approvals.length > 0);
    assert.equal(expanded.value, visible.value && !collapsed);
  }
}

(async () => {
  for (const chatMode of [true, false]) {
    for (const collapsed of [undefined, false, true]) {
      for (const approvals of [[], [{ id: 'pending-approval' }]]) {
        await render(chatMode, approvals, collapsed);
      }
    }
  }
  console.log('12 real composer template checks passed: Chat never mounts approvals, editor/send/token controls remain, Agent approvals retained.');
})().catch(error => { console.error(error); process.exitCode = 1; });
