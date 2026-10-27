const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const calls = [];
const transfers = compile('static/src/utils/conversationTransfer.ts', {
  '@/utils/downloadFile': { downloadApplicationFile: async () => ({ status: 'started' }) }
}, {
  URLSearchParams,
  window: { ensureCsrfToken: async () => {} },
  fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ success: true, data: {
      conversation_id: 'conv_imported', workspace_id: new URL(url, 'http://test').searchParams.get('workspace_id')
    } }) };
  }
});

function dialog(props) {
  const file = 'static/src/components/sidebar/ConversationTransferDialog.vue';
  const { descriptor, errors } = compiler.parse(fs.readFileSync(path.join(root, file), 'utf8'), { filename: file });
  assert.deepEqual(errors, []);
  const script = compiler.compileScript(descriptor, { id: 'import-target-test' });
  const code = ts.transpileModule(script.content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const module = { exports: {} };
  const invalidations = [];
  const completed = [];
  vm.runInNewContext(code, {
    module, exports: module.exports, console, FormData,
    crypto: { randomUUID: () => 'synthetic-operation' },
    document: { activeElement: null },
    require(key) {
      if (key === 'vue') return { ...vue, onBeforeUnmount() {} };
      if (key === 'vue-i18n') return { useI18n: () => ({ t: key => key }) };
      if (key === '@/stores/ui') return { useUiStore: () => ({}) };
      if (key === '@/stores/model') return { useModelStore: () => ({ models: [] }) };
      if (key === '@/stores/conversation') return { useConversationStore: () => ({
        invalidateWorkspaceCache: id => invalidations.push(id), loadWorkspaceConversations: async () => {}
      }) };
      if (key === '@/utils/conversationTransfer') return transfers;
      if (key.endsWith('.vue')) return { default: {} };
      throw new Error(`Unexpected import: ${key}`);
    }
  }, { filename: file });
  const state = module.exports.default.setup(props, { expose() {}, emit: (...event) => completed.push(event) });
  state.fileInput.value = { click() {} };
  return { state, invalidations, completed };
}

async function selectAndImport(instance) {
  instance.state.selectedFile({ target: { files: [new File(['zip'], 'test.astrion.zip')], value: 'chosen' } });
  await instance.state.submit();
}

(async () => {
  const props = { chatMode: false, workspaceId: 'current-a' };
  const grouped = dialog(props);
  grouped.state.pickImport('menu-b');
  props.workspaceId = 'switched-c';
  await selectAndImport(grouped);
  assert.equal(calls.at(-1).url, '/api/conversations/import?workspace_id=menu-b');
  assert.equal(grouped.completed[0][1].workspaceId, 'menu-b');
  assert.ok(grouped.invalidations.includes('menu-b'));
  assert.ok(!grouped.invalidations.includes('switched-c'));

  const fallbackProps = { chatMode: false, workspaceId: 'current-a' };
  const fallback = dialog(fallbackProps);
  fallback.state.pickImport();
  fallbackProps.workspaceId = 'switched-c';
  await selectAndImport(fallback);
  assert.equal(calls.at(-1).url, '/api/conversations/import?workspace_id=current-a');

  const chat = dialog({ chatMode: true, workspaceId: '' });
  chat.state.pickImport();
  await selectAndImport(chat);
  assert.equal(calls.at(-1).url, '/api/conversations/import?kind=chat');
  console.log('PASS workspace-menu import keeps the chosen target through file picking and navigation');
  console.log('PASS ungrouped import captures the current workspace and Chat keeps account scope');
})().catch(error => { console.error(error); process.exitCode = 1; });
