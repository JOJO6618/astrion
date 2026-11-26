import assert from 'node:assert/strict';
import test from 'node:test';
import { createServer } from 'node:http';
import { join } from 'node:path';
import { writeFile, readFile } from 'node:fs/promises';
import { startGatewayFixture, listenOnLoopback, closeServer, createTask, trackTask } from '../2026-11-24_Rust后端/gateway_harness.mjs';
import { startAppRuntimeFixture } from '../2026-11-26_Rust提问计划审批/app_runtime_fixture.mjs';

const model = 'compression-fixture';
const summary = 'Saved fixture summary: the user asked to process the isolated note.';
const original = 'Initial fixture turn: read the isolated note repeatedly.';
async function startMock() {
  const requests = [];
  const server = createServer(async (req, res) => {
    const bytes = [];
    for await (const b of req) bytes.push(b);
    const body = JSON.parse(Buffer.concat(bytes));
    const messages = body.messages;
    const count = messages.filter(m => m.role === 'tool').length;
    const last = messages.at(-1);
    const summarizing = !body.tools?.length;
    requests.push({ body, at: Date.now(), summarizing });
    const tool = !summarizing && last.content !== 'after compression' && count < 10;
    const delta = tool ? { tool_calls: [{ index:0,id:'call_fixture_'+count,type:'function',function:{name:'read_file',arguments:JSON.stringify({path:'note.txt', type:'read'})}}]} :
      {content:summarizing?summary:last.content==='after compression'?'Resumed the summary.':'Completed ten file reads.'};
    res.writeHead(200,{'content-type':'text/event-stream'});
    const send = (delta,finish_reason) => res.write('data: '+JSON.stringify({id:'fixture',model,object:'chat.completion.chunk',choices:[{index:0,delta,finish_reason}]})+'\n\n');
    send(delta,null);
    send({},tool?'tool_calls':'stop');
    res.end('data: [DONE]\n\n');
  });
  const port=await listenOnLoopback(server);
  return {endpoint:'http://127.0.0.1:'+port+'/v1/chat/completions',requests,close:()=>closeServer(server)};
}

test('a long Agent tool run completes, compresses in place, and continues from its summary', {timeout:90000}, async t=>{
  const options={model,startMock,workspaceIds:['default'],web:true,async prepare({workspace,dataDir}){
    await writeFile(join(workspace,'note.txt'),'isolated file contents');
    await writeFile(join(dataDir,'personalization.json'),JSON.stringify({default_work_mode:'execute',default_permission_mode:'approval'}));
  }};
  const app=process.env.ASTRION_RUNTIME_TEST_APP;
  const f=app?await startAppRuntimeFixture(t,app,options):await startGatewayFixture(t,options);
  const {gateway,mock}=f;
  const task=await createTask(gateway,null,original);
  await task.until('ten tool calls',row=>row.status==='succeeded');
  assert.equal(mock.requests.length,11);
  const reads = mock.requests[10].body.messages.filter(message => message.role === 'tool');
  assert.equal(reads.length, 10);
  for (const result of reads) assert.match(result.content, /isolated file contents/);
  const base=gateway.baseUrl;
  const challenge=await fetch(base+'/api/csrf-token');
  const first=challenge.headers.get('set-cookie').split(';')[0];
  const login=await fetch(base+'/host-login',{method:'POST',headers:{cookie:first,origin:base,'x-csrf-token':(await challenge.json()).token,'content-type':'application/json'},body:'{}'});
  assert.equal(login.status,200);
  const cookie=login.headers.get('set-cookie').split(';')[0];
  const csrf=await (await fetch(base+'/api/csrf-token',{headers:{cookie}})).json();
  const headers={cookie,origin:base,'content-type':'application/json','x-csrf-token':csrf.token,'x-astrion-workspace-id':'default'};
  const prefix='/api/conversations/'+task.conversationId;
  const run=await fetch(base+prefix+'/compress',{method:'POST',headers,body:'{}'});
  const result=await run.json();
  assert.equal(run.status,202,JSON.stringify(result));
  const poll=trackTask(gateway,result.data.task_id,task.conversationId);
  await poll.until('compression',row=>row.status==='succeeded');
  const event=poll.events.find(e=>e.type==='compression_finished');
  assert.equal(event.data.guide_inserted,true);
  assert.ok((await readFile(join(f.workspace,event.data.compact_file),'utf8')).includes(original));
  const status=await (await fetch(base+prefix+'/compression_status',{headers})).json();
  assert.equal(status.data.compression_count,1);
  assert.equal(status.data.compression_in_progress,false);
  const again=await createTask(gateway,task.conversationId,'after compression');
  await again.until('resumed final answer',row=>row.status==='succeeded');
  const last=mock.requests.at(-1).body.messages;
  assert.ok(last.some(m=>m.content?.includes?.(summary)));
  assert.ok(!last.some(m=>m.content===original || m.role==='tool'));
  t.diagnostic('10 actual read_file calls, '+mock.requests.length+' model SSE requests, one compact file and one resumed task');
});
