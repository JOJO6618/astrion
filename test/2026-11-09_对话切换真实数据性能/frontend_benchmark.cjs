/* Real history conversion and MinimalBlocks state calculations; no browser DOM yet. */
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { performance } = require('node:perf_hooks');
const { reactive, computed, watch, effectScope } = require('vue');
const { compile, noop, locale } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const scratch = path.join(root, '_experiments/conversation-switch-benchmark-20261109');
const stats = (values) => {
  values.sort((a,b)=>a-b);
  return { count: values.length, median_ms: +values[Math.floor(values.length/2)].toFixed(3), max_ms: +values.at(-1).toFixed(3) };
};
const measure = (fn) => { const start = performance.now(); const value = fn(); return [value, performance.now()-start]; };
const display = compile('static/src/app/methods/conversation/display.ts');
const tools = compile('static/src/utils/chatDisplay.ts', {'@/locales':locale,'./icons':{TOOL_ICON_MAP:{}}});
const history = compile('static/src/app/methods/history.ts', {
  './common':{debugLog:noop}, '@/locales':locale,'./conversation/display':display,
}).historyMethods;
const host = {currentConversationId:'benchmark', cloneToolArguments:tools.cloneToolArguments,buildToolLabel:tools.buildToolLabel};
const source = fs.readFileSync(path.join(root,'static/src/components/chat/MinimalBlocks.vue'),'utf8');
const extract = (start,end) => { const i=source.indexOf(start); if(i<0)throw Error(start);return source.slice(i,source.indexOf(end,i)+end.length); };
const code = ts.transpileModule(extract('const blockGroups = computed(() => {','\n});')+'\n'+extract('const isSummaryRunning = (actions: Action[], groupId: string) => {','\n};')+'\nreturn { blockGroups, isSummaryRunning };', {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
const create = new Function('props','computed',code);
const results=[];
const browserFixtures=[];
const filterDisplay = require('./display_filter.cjs');
const inputs=JSON.parse(fs.readFileSync(path.join(scratch,'frontend-input.json'),'utf8'));
for(const input of inputs) {
  let state; const buildMs=[];
  for(let r=0;r<3;r++) {const [value,ms]=measure(()=>history.buildHistoryState.call(host,input.messages,input.id));state=value;buildMs.push(ms);}
  const assistants=state.messages.filter(m=>m.role==='assistant');
  const candidates=[...assistants].sort((a,b)=>(b.actions?.length||0)-(a.actions?.length||0));
  const heavy=candidates[0];
  const rawSize=(msg)=>Buffer.byteLength(JSON.stringify(msg));
  const largest=[...assistants].sort((a,b)=>rawSize(b)-rawSize(a))[0];
  const props=reactive({actions:heavy.actions,conversationRunning:true,isLatestMessage:true});
  const instance=create(props,computed);
  const groups=instance.blockGroups.value.filter(g=>g.type==='summary');
  const checks=()=>{for(const g of groups)for(let k=0;k<3;k++)instance.isSummaryRunning(g.actions,g.id);};
  const running=[],old=[],traverse=[];
  for(let r=0;r<3;r++) {
    props.isLatestMessage=true; running.push(measure(checks)[1]);
    props.isLatestMessage=false; old.push(measure(checks)[1]);
    const scope=effectScope();
    traverse.push(measure(()=>scope.run(()=>watch(()=>props.actions,noop,{deep:true,immediate:true,flush:'sync'})))[1]);
    scope.stop();
  }
  results.push({id:input.id,input_messages:input.messages.length,ui_messages:state.messages.length,
    assistant_messages:assistants.length,total_actions:assistants.reduce((n,m)=>n+m.actions.length,0),
    largest_action_message:{actions:heavy.actions.length,summary_groups:groups.length,utf8_mib:+(rawSize(heavy)/1048576).toFixed(3)},
    largest_byte_message:{actions:largest.actions.length,utf8_mib:+(rawSize(largest)/1048576).toFixed(3)},
    build_history:stats(buildMs),three_summary_checks_per_group_running:stats(running),
    three_summary_checks_per_group_old:stats(old),initial_deep_watch:stats(traverse)});
  const [hiddenMessages,filterMs] = measure(()=>filterDisplay(state.messages,'hidden'));
  const hiddenAssistants=hiddenMessages.filter(m=>m.role==='assistant');
  const hiddenHeavy=[...hiddenAssistants].sort((a,b)=>b.actions.length-a.actions.length)[0];
  const hiddenProps=reactive({actions:hiddenHeavy.actions,conversationRunning:true,isLatestMessage:true});
  const hiddenInstance=create(hiddenProps,computed);
  const hiddenGroups=hiddenInstance.blockGroups.value.filter(g=>g.type==='summary');
  const [,hiddenChecks]=measure(()=>{for(const g of hiddenGroups)for(let k=0;k<3;k++)hiddenInstance.isSummaryRunning(g.actions,g.id);});
  results.at(-1).compact_hidden={filter_ms:+filterMs.toFixed(3),visible_messages:hiddenMessages.length,
    max_actions:hiddenHeavy.actions.length,summary_groups:hiddenGroups.length,
    max_action_message_mib:+(rawSize(hiddenHeavy)/1048576).toFixed(3),three_summary_checks_ms:+hiddenChecks.toFixed(3)};
  browserFixtures.push({id:input.id,cases:[{name:'most-actions',message:heavy},{name:'most-bytes',message:largest},{name:'most-actions-hidden',message:hiddenHeavy}],allUiMessages:state.messages});
}
fs.writeFileSync(path.join(scratch,'frontend-results.json'),JSON.stringify(results,null,2));
fs.writeFileSync(path.join(scratch,'browser-input.json'),JSON.stringify(browserFixtures));
console.log(JSON.stringify(results,null,2));
