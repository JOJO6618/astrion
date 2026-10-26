const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const visibility = compile('static/src/utils/messageVisibility.ts');
const filename = 'static/src/components/chat/ChatArea.vue';
const { descriptor } = compiler.parse(fs.readFileSync(filename, 'utf8'), { filename });
const ast = ts.createSourceFile(filename + '.ts', descriptor.scriptSetup.content,
  ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
function definition(name) {
  for (const statement of ast.statements) {
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === name) return statement.getText(ast);
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (declaration.name.getText(ast) === name) return `const ${declaration.getText(ast)};`;
      }
    }
  }
  throw Error(`Missing production UI calculation ${name}`);
}
const names = ['RENDERABLE_ACTION_TYPES', 'isActionVisible', 'hasRenderableAssistantActions',
  'isEmptyAssistantMessage', 'isHiddenUserMessage', 'filteredMessages', 'getFilteredMessagesSafe',
  'findAssistantHeaderAnchorUser', 'shouldShowAssistantHeader', 'assistantWorkLabel', 'getGeneratingLetters'];
const template = compiler.compileTemplate({ source: descriptor.template.content, filename, id: 'chat-runtime-template' });
assert.deepEqual(template.errors, []);
function evaluate(code, globals) {
  const module = { exports: {} };
  const javascript = ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  vm.runInNewContext(javascript, { module, exports: module.exports, require: () => vue, ...globals });
  return module.exports;
}
const { render } = evaluate(template.code);

exports.renderMessages = async function renderMessages(messages) {
  const scope = {
    ...visibility, computed: vue.computed, props: { messages, chatMode: true },
    compactMessageDisplay: vue.ref('hidden'), currentLocale: vue.ref('zh-CN'),
    t: key => key, isMultiAgentMessage: () => false,
    userMDebug() {}, debugLoggedUnknownActionKeys: new Set()
  };
  const functions = evaluate(names.map(definition).join('\n') +
    `\nmodule.exports = {${names.join(',')}};`, scope);
  const state = {
    ...functions, filteredMessages: functions.filteredMessages.value,
    stickScrollElement: {}, renderPending: false, chatMode: true, blockDisplayMode: 'normal',
    aiAssistantName: 'Astrion', quickNavItems: [], quickNavMode: 'hidden', expandedBlocks: new Set(),
    collapsingBlocks: new Set(),
    editSummarySegmentEnds: new Set(), assistantTokenSpeedEnds: new Set(),
    tappedChatMessageId: '', editingMessageId: '', userBubbleTransitionSuppressed: false,
    $t: key => key, messageFiles: () => [], renderUserMessageContent: value => value,
    getMessageVisibility: visibility.getMessageVisibility, iconStyleSafe: () => ({}),
    userHeaderLabel: () => 'User', userHeaderIconKey: () => 'user',
    props: { historyLoading: false }, handleThinkingScroll() {}
  };
  const fallback = () => '';
  const calls = new Set([...descriptor.template.content.matchAll(/\b([A-Za-z_$][\w$]*)\s*\(/g)].map(match => match[1]));
  const context = new Proxy(state, { get(object, key) {
    if (key in object) return object[key];
    return calls.has(key) ? fallback : false;
  }});
  const app = vue.createSSRApp({ render() { return render(context, []); } });
  for (const name of new Set([...descriptor.template.content.matchAll(/<([A-Z][A-Za-z]+)/g)].map(m => m[1]))) {
    app.component(name, vue.defineComponent({
      props: ['data', 'content'],
      setup(props, ctx) {
        return () => vue.h('div', { 'data-component': name }, name === 'Virtualizer'
          ? (props.data || []).map((item, index) => ctx.slots.default?.({ item, index }))
          : name === 'MarkdownRenderer' ? props.content : []);
      }
    }));
  }
  return renderToString(app);
};
