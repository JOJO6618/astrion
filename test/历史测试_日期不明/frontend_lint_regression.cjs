// Run: node test/历史测试_日期不明/frontend_lint_regression.cjs
// Exercise actual SFC setup code with Vue reactivity; stub canvas and network boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('typescript');
const vue = require('vue');
const { parse, compileScript } = require('@vue/compiler-sfc');

const root = path.resolve(__dirname, '../..');
const locales = { t: (key) => key, currentLocale: vue.ref('zh-CN') };
const scopes = [];
const timers = new Set();
const modelPath = 'static/src/components/workflow/workflowModel.ts';

function evaluate(source, filename, dependencies) {
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(output, {
    module,
    exports: module.exports,
    require: (id) => {
      if (Object.hasOwn(dependencies, id)) return dependencies[id];
      if (id.endsWith('.vue') || id.endsWith('.css')) return {};
      return require(id);
    },
    document: { addEventListener() {}, removeEventListener() {} },
    setTimeout: (callback, delay) => {
      const timer = setTimeout(callback, delay);
      timers.add(timer);
      return timer;
    },
    clearTimeout,
    console
  }, { filename });
  return module.exports;
}

const model = evaluate(fs.readFileSync(path.join(root, modelPath), 'utf8'), modelPath, {
  '@/locales': locales
});

function setupSfc(filename, props, dependencies = {}) {
  const scope = vue.effectScope();
  scopes.push(scope);
  const events = [];
  const mounted = [];
  const descriptor = parse(fs.readFileSync(path.join(root, filename), 'utf8')).descriptor;
  const source = compileScript(descriptor, { id: 'lint-regression' }).content;
  const component = evaluate(source, filename, {
    vue: { ...vue, onMounted: (fn) => mounted.push(fn), onBeforeUnmount() {} },
    '@/locales': locales,
    '@/utils/icons': { ICONS: {} },
    ...dependencies
  }).default;
  const state = scope.run(() => component.setup(props, {
    expose() {}, emit: (...event) => events.push(event)
  }));
  scope.run(() => mounted.forEach((fn) => fn()));
  return { state, events };
}

async function checkWorkflow() {
  let finishSave;
  const saves = [];
  const props = vue.reactive({ workflow: model.createEmptyWorkflow('original') });
  const original = JSON.stringify(props.workflow);
  const { state, events } = setupSfc(
    'static/src/components/workflow/WorkflowEditorView.vue', props, {
      '@vue-flow/core': {
        useVueFlow: () => ({
          project: (point) => point, fitView() {},
          getViewport: () => ({ x: 0, y: 0, zoom: 1 }), updateNodeInternals() {}
        })
      },
      '@vue-flow/background': {}, '@vue-flow/controls': {}, '@vue-flow/minimap': {},
      './workflowModel': model,
      './api': {
        saveWorkflow: (workflow) => {
          saves.push(JSON.parse(JSON.stringify(workflow)));
          return new Promise((resolve) => { finishSave = resolve; });
        }
      }
    }
  );
  assert.equal(JSON.stringify(props.workflow), original, 'layout must not mutate the prop');
  state.workflow.value.name = 'renamed';
  state.workflow.value.nodes[0].name = 'edited node';
  assert.equal(props.workflow.name, 'original');
  assert.notEqual(props.workflow.nodes[0].name, 'edited node');
  assert.equal(state.dirty.value, true);
  assert.equal(state.errorCount.value, 0, 'default graph must remain valid');

  const pendingSave = state.onSave();
  assert.equal(saves[0].name, 'renamed');
  state.workflow.value.description = 'edited while saving';
  finishSave();
  await pendingSave;
  const saved = events.find(([event]) => event === 'save')[1];
  assert.equal(saved.name, 'renamed', 'parent must receive the saved name for rename handling');
  assert.equal(saved.description, '', 'save event must contain the submitted snapshot');
  assert.equal(state.dirty.value, true, 'edits made during save must remain unsaved');

  const nextSave = state.onSave();
  finishSave();
  await nextSave;
  assert.equal(state.dirty.value, false);
  props.workflow = model.createEmptyWorkflow('replacement');
  await vue.nextTick();
  assert.equal(state.workflow.value.name, 'replacement');
  assert.equal(state.dirty.value, false);
  assert.equal(state.selectedNode.value, null);
}

async function checkQuestions() {
  const props = vue.reactive({ questions: [], activeIndex: 0, visible: false });
  const { state, events } = setupSfc(
    'static/src/components/overlay/UserQuestionDialog.vue', props
  );
  assert.equal(state.currentDraft.value.text, '', 'empty question list must have a safe draft');
  props.questions = [
    { question_id: 'first', options: [{ id: 'yes' }] },
    { question_id: 'second' }
  ];
  assert.equal(state.currentDraft.value.text, '', 'replacement draft must exist before render');
  state.selectOption('yes');
  props.activeIndex = 1;
  state.currentDraft.value.text = 'answer two';
  assert.equal(state.canSubmit.value, true);
  props.activeIndex = 0;
  assert.equal(state.currentDraft.value.selected_option_id, 'yes');
  state.submit();
  const answers = events.find(([event]) => event === 'submit')[1];
  assert.equal(answers[0].question_id, 'first');
  assert.equal(answers[0].selected_option_id, 'yes');
  assert.equal(answers[1].text, 'answer two');
  props.questions = [];
  assert.equal(state.currentDraft.value.text, '');
  await vue.nextTick();
}

(async () => {
  try {
    await checkWorkflow();
    await checkQuestions();
    console.log('PASS: workflow draft/save/rename and question switching regressions');
  } finally {
    scopes.forEach((scope) => scope.stop());
    timers.forEach(clearTimeout);
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
