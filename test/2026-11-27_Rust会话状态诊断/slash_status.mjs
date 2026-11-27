import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { startGatewayFixture, listenOnLoopback, closeServer } from '../2026-11-24_Rust后端/gateway_harness.mjs';

async function startMock() {
  const requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[]; for await (const b of req) chunks.push(b);
    const body=JSON.parse(Buffer.concat(chunks)); requests.push(body);
    const n=requests.filter(r=>r.model===body.model).length;
    const tool=(id,name,args)=>({tool_calls:[{index:0,id,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
    let delta;
    if (body.model==='audit-upstream') delta=tool('verdict','approve_decision',{decision:'rejected',reason:'synthetic review evidence'});
    else if (n===1) delta=tool('write','write_file',{file_path:'notes.txt',content:'fixture review only'});
    else {
      await new Promise(resolve=>setTimeout(resolve,1000));
      delta={content:'finished fixture'};
    }
    res.writeHead(200,{'content-type':'text/event-stream'});
    for(const [d,finish] of [[delta,null],[{},delta.tool_calls?'tool_calls':'stop']]){
      res.write('data: '+JSON.stringify({choices:[{index:0,delta:d,finish_reason:finish}]})+'\n\n');
    }
    res.end('data: [DONE]\n\n');
  });
  const port=await listenOnLoopback(server);
  return {endpoint:`http://127.0.0.1:${port}/v1/chat/completions`,requests,close:()=>closeServer(server)};
}

async function fixedFixture(t) {
  return startGatewayFixture(t,{web:true,model:'main-upstream',startMock,async prepare({dataDir,mock}){
    const config=join(dataDir,'config');
    await mkdir(config,{recursive:true});
    await writeFile(join(config,'custom_models.json'),JSON.stringify([{model_name:'workflow-review',model_id:'audit-upstream',url:new URL('/v1',mock.endpoint),apikey:'isolated-fixture-key'},{model_name:'fixture-default',model_id:'main-upstream',url:new URL('/v1',mock.endpoint),apikey:'isolated-fixture-key'}]));
    await writeFile(join(dataDir,'personalization.json'),JSON.stringify({auto_generate_title:false,default_work_mode:'execute',default_permission_mode:'approval',default_run_mode:'thinking',default_reasoning_effort:'high',default_model:'fixture-default'}));
  }});
}
async function browser(gateway) {
  const url=gateway.baseUrl;
  const challenge=await fetch(url+'/api/csrf-token');
  const initial=challenge.headers.get('set-cookie').split(';')[0];
  const login=await fetch(url+'/host-login',{method:'POST',headers:{cookie:initial,origin:url,'x-csrf-token':(await challenge.json()).token,'content-type':'application/json'},body:'{}'});
  assert.equal(login.status,200);
  const cookie=login.headers.get('set-cookie').split(';')[0];
  const csrf=await (await fetch(url+'/api/csrf-token',{headers:{cookie}})).json();
  const headers={cookie,origin:url,'content-type':'application/json','x-csrf-token':csrf.token,'x-astrion-workspace-id':'default'};
  return async (path,method='GET',body)=>{
    const res=await fetch(url+path,{headers,method,body:body===undefined?undefined:JSON.stringify(body)});
    const text=await res.text();
    assert.ok(res.ok,`${path} ${res.status}: ${text}`);
    return JSON.parse(text);
  };
}



test('slash status uses selected conversation settings and makes diagnostic availability explicit', {timeout:10000}, async t=>{
  const {gateway}=await fixedFixture(t);
  const req=await browser(gateway);
  const created=await req('/api/conversations','POST',{settings:{work_mode:'execute',permission_mode:'approval',execution_mode:'sandbox',network_permission:'restricted',reasoning_effort:'low',run_mode:'fast',thinking_mode:false,model_key:'workflow-review'}});
  const cid=created.data?.conversation_id||created.conversation_id;
  assert.ok(cid,JSON.stringify(created));
  const boot=(await req('/api/conversations/'+cid+'/bootstrap')).data;
  const status=(await req('/api/commands','POST',{conversation_id:cid,command:'/status'})).data;
  assert.equal(boot.meta.run_mode,'fast'); assert.equal(boot.meta.thinking_mode,false);
  assert.equal(status.run_mode,'fast');
  assert.equal(status.thinking_mode,false);
  assert.equal(status.model_key,boot.meta.model_key);
  assert.equal(status.reasoning_effort,'low');
  assert.equal(status.context.conversation_count,0);
  assert.equal(status.context.current_tokens,null);
  assert.equal(status.context.usage_percent,null);
  assert.equal(status.project.available,false);
  assert.equal(status.project.total_size,null);

  const defaults=(await req('/api/commands','POST',{command:'/status'})).data;
  assert.equal(defaults.model_key,'fixture-default');
  assert.equal(defaults.run_mode,'thinking');
  assert.equal(defaults.reasoning_effort,'high');

  const cleared=await req('/api/conversations','POST',{settings:{work_mode:'execute',permission_mode:'approval',execution_mode:'sandbox',network_permission:'restricted',reasoning_effort:null,run_mode:'fast',thinking_mode:false,model_key:'workflow-review'}});
  const clearedId=cleared.data?.conversation_id||cleared.conversation_id;
  const inherited=(await req('/api/commands','POST',{conversation_id:clearedId,command:'/status'})).data;
  assert.equal(inherited.reasoning_effort,null,'explicit none must not inherit global high');
});
