const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../node_modules/typescript');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/src/app/computed.ts'), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const exportsObject = {};
const store = { subAgents: [], commands: [], form: { tool_intent_enabled: true } };
function mockRequire(name) {
  if (name === '@/locales') return { t: key => key, currentLocale: { value: 'zh-CN' } };
  if (name === 'pinia') return { mapState: () => ({}), mapWritableState: () => ({}) };
  if (name.includes('avatarFace')) return { toolFaceKey: name => name };
  if (name.includes('messageVisibility')) return { messageStartsWork: () => true };
  return new Proxy({}, { get: () => () => store });
}
new Function('require', 'exports', code)(mockRequire, exportsObject);
const avatarStatus = exportsObject.computed.avatarStatus;
function host(overrides = {}) {
  return {
    messages: [{ role: 'assistant', actions: [{ type: 'tool', tool: {
      name: 'read_file', status: 'completed', intent_full: 'old-intent',
      intent_rendered: 'old-intent', intent_complete: true
    } }] }],
    taskInProgress: true, streamingMessage: true, apiRequestPending: true,
    stopRequested: false, preparingTools: new Map(), activeTools: new Map(), ...overrides
  };
}
assert.equal(avatarStatus.call(host()).text, 'appCore.waitingApiResponse');
assert.equal(avatarStatus.call(host()).apiWaiting, true);
const staleTool = host();
staleTool.messages[0].actions[0].tool.status = 'running';
assert.equal(avatarStatus.call(staleTool).apiWaiting, true);
const staleThinking = host();
staleThinking.messages[0].currentStreamingType = 'thinking';
assert.equal(avatarStatus.call(staleThinking).apiWaiting, true);
staleThinking.apiRequestPending = false;
assert.equal(avatarStatus.call(staleThinking).mode, 'think');
staleTool.apiRequestPending = false;
assert.equal(avatarStatus.call(staleTool).mode, 'tool');
assert.equal(avatarStatus.call(host({ apiRequestPending: false })).intentRetained, true);
assert.equal(avatarStatus.call(host({ taskInProgress: false, streamingMessage: false })).mode, 'idle');
assert.equal(avatarStatus.call(host({ stopRequested: true })).apiWaiting, undefined);
console.log('Avatar waiting-state regressions passed (8 scenarios).');
