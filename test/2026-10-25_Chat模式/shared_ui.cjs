const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const vue = require('../../node_modules/vue');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { renderToString } = require('../../node_modules/@vue/server-renderer');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');
const root = path.resolve(__dirname, '../..');
const types = compile('static/src/chat/types.ts');
const { toDisplayMessages } = compile('static/src/chat/display.ts', {'./types':types});
const user = {message_id:'u', role:'user', content:'question', timestamp:'2026-10-26T02:00:00', media_refs:[{media_id:'sha256:image',kind:'image'}, {media_id:'sha256:video',kind:'video'}], metadata:{chat:{parent_id:null}}};
const first = {message_id:'a1',role:'assistant',content:'Before tools',tool_calls:[{id:'call1',function:{name:'web_search',arguments:'{"query":"test"}'}}],metadata:{chat:{parent_id:'u'}}};
const result = {message_id:'t',role:'tool',tool_call_id:'call1',content:JSON.stringify({success:false,status:'cancelled',error:'Stopped'}),metadata:{chat:{parent_id:'a1'}}};
const final = {message_id:'a2',role:'assistant',content:'Final <suggest_agent reason="files" />',metadata:{chat:{parent_id:'t'}}};
const displayed = toDisplayMessages([user, first, result, final]);
assert.equal(displayed.length,3);
assert.equal(displayed[0].created_at,user.timestamp);
assert.deepEqual([...displayed[0].images],['sha256:image']);
assert.deepEqual([...displayed[0].videos],['sha256:video']);
assert.equal(displayed[1].raw.message_id,'a1');
assert.equal(displayed[2].raw.message_id,'a2');
assert.equal(displayed[1].actions.filter(a=>a.type==='text').length,1);
assert.equal(displayed[1].actions[1].tool.status,'failed');
assert.equal(displayed[1].actions[1].tool.result.status,'cancelled');
assert.equal(displayed[2].actions[0].content,'Final');
assert.equal(toDisplayMessages([{...user,metadata:{visibility:'hidden'}}]).length,0);
const stale = toDisplayMessages([first,result],[{actions:[{id:'call1',type:'tool',tool:{id:'call1',status:'running'}}]}]);
assert.equal(stale[0].actions[1].tool.status,'failed');
const live = toDisplayMessages([{message_id:'live',role:'assistant',content:'Partial',reasoning_content:'Thinking',live:true}]);
assert.ok(live[0].actions.every(a=>a.streaming));
assert.equal(live[0].messageId,'live');

function compileComponent(file, overrides={}) {
  const source = fs.readFileSync(path.join(root,file),'utf8');
  const {descriptor,errors} = compiler.parse(source,{filename:file});
  assert.deepEqual(errors,[]);
  const script = compiler.compileScript(descriptor,{id:'test-shared-chat',inlineTemplate:true});
  const code = ts.transpileModule(script.content,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
  const module = {exports:{}};
  vm.runInNewContext(code,{module,exports:module.exports,console,Map,Set,window:{},document:{},require(key){
    if(key==='vue') return vue;
    if(key in overrides) return overrides[key];
    if(key.endsWith('.vue')) return {default:vue.defineComponent({name:path.basename(key,'.vue'),setup(_,ctx){return ()=>vue.h('section',{'data-component':path.basename(key,'.vue')},ctx.slots.default?.());}})};
    if(key==='@/utils/chatDisplay') return new Proxy({},{get:()=>()=>''});
    if(key==='@/utils/icons') return {ICONS:{menu:'/menu.svg',chatBubble:'/chat.svg'}};
    if(key==='@/app/bootstrap') return {};
    if(key==='@/app/methods/ui/system') return {systemMethods:{handleCopyCodeClick(){}}};
    throw Error(`Unexpected import ${key}`);
  }},{filename:file});
  return module.exports.default;
}
const sharedFiles = ['static/src/components/chat/ConversationModelHeader.vue','static/src/components/chat/ChatArea.vue','static/src/components/chat/MinimalBlocks.vue','static/src/components/input/InputComposer.vue','static/src/components/sidebar/ConversationSidebar.vue','static/src/components/chat-mode/ChatShell.vue'];
for(const file of sharedFiles){
  const {descriptor,errors} = compiler.parse(fs.readFileSync(path.join(root,file),'utf8'),{filename:file});
  assert.deepEqual(errors,[]);
  const script = compiler.compileScript(descriptor,{id:'shared-sfc-check'});
  const template = compiler.compileTemplate({source:descriptor.template.content,filename:file,id:'shared-sfc-check',compilerOptions:{bindingMetadata:script.bindings}});
  assert.deepEqual(template.errors,[],file);
}

(async()=>{
  for(const empty of [true,false]) {
    const page = vue.reactive({
      chat:{cid:'conv_test',settings:{settings:{chat_deep_compress_trigger_tokens:100000}},thinking:true,effort:null,running:false,busy:false,loading:false,model:'m',network:true,finalMessage:m=>m.message_id==='a2',report(){},browse(){},regenerate(){}},
      draft:{text:'Draft',identity:1},ui:{toggleSidebar(){}},personalization:{form:{show_status_avatar:true}},
      collapsedGroups:new Set(),expandedBlocks:new Set(),headerMenuOpen:false,modelMenuSearchQuery:'',inputFocused:false,uploading:false,editing:null,saving:false,tokenOpen:false,mobileOpen:false,isMobile:false,
      messages:empty?[]:displayed,title:'Chat title',modelOptions:[{key:'m',label:'Model'}],currentModel:{key:'m',label:'Model',supportsThinking:true},groups:[],modeOptions:[],suggestion:'',retryTurn:null,contextTokens:42,images:[],videos:[],
      preview(){return '/media';},removeMedia(){},run(){},configure(){},openSettings(){},versions(){return [];},versionIndex(){return 0;},navigate(){},send(){},upload(){},saveEdit(){},deleteConversation(){},duplicateConversation(){},search(){}
    });
    const ChatArea = vue.defineComponent({props:['messages'],setup(props,ctx){return ()=>vue.h('section',{'data-component':'ChatArea'},props.messages.flatMap(message=>[vue.h('p',message.content),ctx.slots[message.role==='user'?'user-actions':'assistant-actions']?.({message})]));}});
    const InputComposer = vue.defineComponent({props:['inputMessage','chatMode','networkEnabled'],setup(props){return ()=>vue.h('div',{'data-component':'InputComposer','data-chat':props.chatMode,'data-network':props.networkEnabled},props.inputMessage);}});
    const component = compileComponent('static/src/components/chat-mode/ChatShell.vue',{
      '@/chat/useChatPage':{useChatPage:()=>page},
      '@/components/chat/ChatArea.vue':{default:ChatArea},
      '@/components/input/InputComposer.vue':{default:InputComposer}
    });
    const app=vue.createSSRApp(component);
    app.config.globalProperties.$t=key=>key;
    const html=await renderToString(app);
    assert.ok(html.includes('data-component="ConversationSidebar"'));
    assert.ok(html.includes('data-component="ConversationModelHeader"'));
    assert.ok(html.includes('data-component="ChatArea"'));
    assert.ok(html.includes('data-component="InputComposer"'));
    assert.ok(html.includes('Draft'));
    assert.equal(html.includes('blank-hero-overlay'),empty);
    assert.ok(!html.includes('data-component="ChatComposer"'));
  }
  console.log('Shared Chat UI checks passed: tree projection, tool cancellation, media, 6 SFCs, empty and populated shell rendering.');
})().catch(error=>{console.error(error);process.exitCode=1;});
