import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { startGatewayFixture, listenOnLoopback, closeServer, createTask } from '../2026-11-24_Rust后端/gateway_harness.mjs';

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
      await new Promise(resolve=>setTimeout(resolve,2000));
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
    await writeFile(join(config,'custom_models.json'),JSON.stringify([{model_name:'workflow-review',model_id:'audit-upstream',url:new URL('/v1',mock.endpoint),apikey:'isolated-fixture-key'}]));
    await writeFile(join(dataDir,'personalization.json'),JSON.stringify({auto_generate_title:false,default_work_mode:'execute',default_permission_mode:'approval',review_agents:{auto_approval:{model:'workflow-review',max_rounds:2}}}));
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


test('Agent bootstrap preserves just-resolved approval cards at its new cursor', {timeout:10000}, async t=>{
  const {gateway,mock}=await fixedFixture(t);
  const req=await browser(gateway);
  const task=await createTask(gateway,null,'verify approval record',{work_mode:'execute',permission_mode:'approval'});
  const consent=await task.until('manual approval request', poll=>poll.events.some(event=>event.type==='tool_approval_required'));
  const approval_id=(await req('/api/tool-approvals/pending?conversation_id='+task.conversationId)).items[0].approval_id;
  const pending = (await req('/api/conversations/'+task.conversationId+'/bootstrap')).data.display;
  assert.equal(pending.state.pending_tool_approvals[0].approval_id, approval_id);
  assert.deepEqual(pending.state.resolved_tool_approval_ids, []);
  await req('/api/tool-approvals/'+approval_id+'/decision','POST',{decision:'rejected'});
  const row=await task.until('review resolved', poll=>poll.events.some(event=>event.type==='tool_approval_resolved'));
  assert.equal(row.status,'running');
  const reviews=task.events.filter(e=>['tool_approval_required','tool_approval_resolved'].includes(e.type));
  const snap=(await req('/api/conversations/'+task.conversationId+'/bootstrap')).data.display;
  assert.equal(snap.task.status,'running');
  assert.ok(reviews.length>1);
  assert.ok(reviews.every(e=>e.idx<snap.next_event_idx));
  assert.equal(snap.state.approval_review_records.length,1);
  const [record]=snap.state.approval_review_records;
  assert.equal(record.approval_id,approval_id);
  assert.equal(record.tool_name,'write_file');
  assert.equal(record.approval.arguments.file_path,'notes.txt');
  assert.equal(record.approval.status,'rejected');
  assert.equal(record.final_decision,'rejected');
  assert.deepEqual(snap.state.resolved_tool_approval_ids,[approval_id]);
  assert.deepEqual(snap.state.pending_tool_approvals,[]);
  const polled=await gateway.pollTask(task.taskId,snap.next_event_idx);
  assert.ok(!(polled.events||[]).some(e=>e.type==='tool_approval_resolved'));
});
