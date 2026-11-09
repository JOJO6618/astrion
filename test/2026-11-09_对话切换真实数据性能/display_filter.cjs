/* Extract the actual ChatArea filtering expressions; no application startup. */
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const { reactive, computed } = require('vue');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const source = fs.readFileSync(path.resolve(__dirname,'../../static/src/components/chat/ChatArea.vue'),'utf8');
const extract=(start,end)=>{const pos=source.indexOf(start);if(pos<0)throw Error(start);return source.slice(pos,source.indexOf(end,pos)+end.length);};
const visibility=compile('static/src/utils/messageVisibility.ts');
const merge=compile('static/src/components/chat/assistantDisplayRuns.ts').mergeAssistantDisplayRuns;
const snippets=[
  extract('function isMultiAgentMessage(message: any): boolean {','\n}'),
  extract('const isHiddenUserMessage = (message: any) => {','\n};'),
  extract('const isEmptyAssistantMessage = (message: any) => {','\n};'),
  extract('const RENDERABLE_ACTION_TYPES = new Set([','\n]);'),
  extract('function isActionVisible(action: any): boolean {','\n}'),
  extract('const filteredMessages = computed(() => {','\n});'),
];
const code=ts.transpileModule(snippets.join('\n')+'\nreturn filteredMessages.value;', {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
const run=new Function('props','computed','compactMessageDisplay','getMessageVisibility','mergeAssistantDisplayRuns','userMDebug','debugLoggedUnknownActionKeys','getSubAgentSystemNoticeLabel',code);
module.exports=(messages,mode)=>run(reactive({messages,chatMode:false}),computed,{value:mode},visibility.getMessageVisibility,merge,()=>{},new Set(),()=>null);
// The samples contain no persisted system-role messages. The unused system-label
// helper is inert; all user/assistant/tool filtering and merging uses current source.
