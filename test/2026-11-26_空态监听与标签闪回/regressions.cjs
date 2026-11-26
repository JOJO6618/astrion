const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { reactive, computed, watch, nextTick } = require('vue');
const { compile, fixture, tab, answer, commandRequest } = require('../2026-11-14_桌面多视图/frontend/fixture.cjs');

// These Options API functions run without mounting the desktop shell. Only their unrelated imports are stubbed.
function appOptions(file) {
  const source = fs.readFileSync(path.resolve(__dirname, '../..', file), 'utf8');
  const imports = Object.fromEntries(ts.preProcessFile(source).importedFiles.map(({fileName}) => [fileName, {}]));
  return compile(file, { ...imports, pinia: {mapState: () => ({}), mapWritableState: () => ({})} });
}

test('typing in a tool leaves old messages untouched and role transitions still update the welcome screen', async () => {
  const {systemMethods} = appOptions('static/src/app/methods/ui/system.ts');
  const {computed: getters} = appOptions('static/src/app/computed.ts');
  const {watchers} = appOptions('static/src/app/watchers.ts');
  let oldReads = 0;
  const transitions = [];
  const host = reactive({ messages: [], isConversationBlank: systemMethods.isConversationBlank,
    refreshBlankHeroState() { transitions.push(this.isConversationBlank()); } });
  assert.equal(watchers.messages, undefined);
  const stop = watch(computed(() => getters.conversationBlank.call(host)), watchers.conversationBlank.bind(host));
  host.messages = Array.from({length:617}, () => ({role:'tool', payload:{get result(){oldReads++;return '';}}}));
  await nextTick();
  host.messages[0].role = 'user';await nextTick();
  host.messages.push({role:'assistant', tool:{intent:''}});await nextTick();
  for(const char of '检查当前任务进度') {host.messages.at(-1).tool.intent += char;await nextTick();}
  host.messages.shift();await nextTick();
  host.messages.at(-1).role = 'system';await nextTick();
  host.messages = [{role:'assistant'}];await nextTick();
  host.messages = [];await nextTick();stop();
  assert.equal(oldReads, 0);
  assert.deepEqual(transitions, [false,true,false,true]);
});

test('old acknowledgments cannot override a newer click, including a click back to the confirmed tab', async () => {
  const f = fixture({chrome:true});const tabs = [tab('a'), tab('b')];
  const settled = []; f.window.astrionShell.settleShow = id => settled.push(id);
  f.store.applyServerSnapshot(f.state(1,tabs,'a'));
  const first = f.store.showTab('b');const firstId=f.store.pendingShow.id;
  assert.equal(f.store.activeKey,'b');
  const second = f.store.showTab('a');const secondId=f.store.pendingShow.id;
  assert.notEqual(firstId,secondId);assert.equal(f.store.activeKey,'a');
  f.store.applyServerSnapshot(f.state(2,tabs,'a'));
  answer(await commandRequest(f,'activate'),{success:true,state:f.state(3,tabs,'b')});await first;
  assert.equal(f.store.confirmedActiveKey,'b');assert.equal(f.store.activeKey,'a');
  answer(await commandRequest(f,'activate'),{success:true,state:f.state(4,tabs,'a')});await second;
  assert.equal(f.store.pendingShow,null);assert.deepEqual(settled,[firstId,secondId]);
});

test('failed activation or removal releases the presentation of the pending tab', async () => {
  const f = fixture({chrome:true});const tabs=[tab('a'),tab('b')];
  f.store.applyServerSnapshot(f.state(1,tabs,'a'));const first=f.store.showTab('b');
  answer(await commandRequest(f,'activate'),{success:false,error:'offline'},503);
  await assert.rejects(first);assert.equal(f.store.activeKey,'a');
  const second=f.store.showTab('b');f.store.applyServerSnapshot(f.state(3,[tabs[0]],'a'));
  assert.equal(f.store.activeKey,'a');assert.equal(f.store.pendingShow,null);
  answer(await commandRequest(f,'activate'),{success:true,state:f.state(2,tabs,'b')});await second;
  assert.equal(f.store.activeKey,'a');
});
