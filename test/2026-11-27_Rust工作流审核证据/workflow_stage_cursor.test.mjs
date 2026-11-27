import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { startGatewayFixture, listenOnLoopback, closeServer, trackTask } from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { readDocument, seedDocument } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';

async function startMock() {
  const requests=[];
  const server=createServer(async(req,res)=>{
    const chunks=[]; for await (const b of req) chunks.push(b);
    const body=JSON.parse(Buffer.concat(chunks)); requests.push(body);
    const n=requests.filter(r=>r.model===body.model).length;
    const tool=(id,name,args)=>({tool_calls:[{index:0,id,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
    let delta;
    if (body.model==='audit-upstream') delta=tool('verdict','report_workflow_review',{decision:'pass',message:'verified fixture'});
    else if (n===1) delta=tool('list','list_workflows',{name:'case'});
    else if (n===2) delta=tool('report','report_workflow_stage',{summary:'fixture stage'});
    else delta={content:'workflow fixture reply'};
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
    await writeFile(join(config,'custom_models.json'),JSON.stringify([{model_name:'workflow-review',model_id:'audit-upstream',url:new URL('/v1',mock.endpoint),apikey:'isolated-fixture-key'}]));
    await writeFile(join(dataDir,'personalization.json'),JSON.stringify({auto_generate_title:false,review_agents:{workflow_review:{model:'workflow-review',max_rounds:2}}}));
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

test('review gets stage tools after deep-compacted history', {timeout:15000}, async t=>{
  const {gateway,dataDir,mock}=await fixedFixture(t);
  const req=await browser(gateway);
  await req('/api/workflows/case','PUT',{workflow:{
    name:'case',description:'fixture',reviewMode:'readonly',endConditions:'stop',maxStageRounds:10,
    nodes:[
      {id:'start',kind:'start',next:'step'},
      {id:'step',kind:'stage',name:'Step',goal:'Mark',instructions:'Check evidence',next:'review'},
      {id:'review',kind:'review',name:'Check',prompt:'Check',next:'finish',rejectTo:'step',maxRejects:2},
      {id:'finish',kind:'end',name:'Finish'},
    ]
  }});
  const {conversation_id:id}=await gateway.createSession();
  const directory=join(dataDir,'conversations/default');
  const doc=readDocument(directory,id);
  for(let i=0;i<6;i++){
    doc.messages.push({role:'user',content:'old compacted message '+i,metadata:{deep_compacted:true}});
    doc.messages.push({role:'assistant',content:'old answer',metadata:{deep_compacted:true}});
  }
  doc.messages.push({role:'user',content:'fixture compacted summary',metadata:{message_source:'compression_handoff'}});
  seedDocument(directory,doc);
  const active=await req('/api/workflow/activate','POST',{name:'case',conversation_id:id});
  const task=trackTask(gateway,active.task_id,id);
  await task.until('review finished',row=>row.status==='succeeded');
  const first=mock.requests[0].messages;
  assert.ok(first.every(m=>!m.content?.includes?.('old compacted message')));
  const review=mock.requests.find(r=>r.model==='audit-upstream');
  assert.ok(review,'no review request: '+JSON.stringify(task.events));
  assert.match(review.messages[1].content,/→ list_workflows：case/);
});
