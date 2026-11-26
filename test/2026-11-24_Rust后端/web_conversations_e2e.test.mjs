import assert from 'node:assert/strict';
import { readDocument, migrate } from '../2026-11-27_Rust对话格式迁移/fixture.mjs';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import test from 'node:test';
import { startGatewayFixture } from './gateway_harness.mjs';

const cookie = response => response.headers.get('set-cookie').split(';', 1)[0];

test('browser creates from explicit settings, scopes lookups and pages after filtering', {timeout:30_000}, async t => {
  const {gateway, dataDir} = await startGatewayFixture(t, {
    model:'offline', web:true, workspaceIds:['default', 'other'],
    startMock: async () => ({endpoint:'http://127.0.0.1:1/v1/chat/completions', close(){}}),
  });
  const config = join(dataDir,'config');
  await mkdir(config,{recursive:true});
  await writeFile(join(config,'custom_models.json'), JSON.stringify([{model_name:'browser-fixture', model_id:'browser-fixture', url:'http://127.0.0.1:1/v1', apikey:'synthetic'}]));
  await writeFile(join(dataDir,'personalization.json'),JSON.stringify({browser_tools_default:true,deep_compress_trigger_tokens:120000}));
  const origin = gateway.baseUrl;
  const request = (path, init={}) => fetch(origin+path, {signal:AbortSignal.timeout(5000), ...init});
  const challenge = await request('/api/csrf-token');
  const login = await request('/host-login', {method:'POST',headers:{cookie:cookie(challenge), origin,'x-csrf-token':(await challenge.json()).token,'content-type':'application/json'},body:'{}'});
  assert.equal(login.status,200);
  const session = cookie(login);
  const token = (await (await request('/api/csrf-token',{headers:{cookie:session}})).json()).token;
  const headers = {cookie:session,origin,'x-csrf-token':token,'content-type':'application/json'};
  const settings = { model_key:'browser-fixture', run_mode:'fast', thinking_mode:false, reasoning_effort:null, work_mode:'execute', permission_mode:'approval',execution_mode:'sandbox',network_permission:'restricted' };
  const post = s => request('/api/conversations',{method:'POST', headers, body:JSON.stringify({settings:s})});
  assert.equal((await post({...settings,work_mode:'plan'})).status,400);
  await writeFile(join(dataDir,'admin_policy.json'),JSON.stringify({global:{disabled_models:['browser-fixture']}}));
  assert.equal((await post(settings)).status,403);
  await writeFile(join(dataDir,'admin_policy.json'),'{}');
  const response = await post(settings);
  const created = await response.json();
  assert.equal(response.status,201,JSON.stringify(created));
  const id = created.conversation_id;
  const directory = join(dataDir,'conversations','default');
  assert.deepEqual((await readdir(directory)).filter(f=>f.startsWith('conv_')), []);
  const document = readDocument(directory,id);
  assert.equal(document.metadata.permission_mode,'approval');
  assert.equal(document.metadata.browser_tools_enabled,true);
  assert.equal(document.metadata.compress_limit_tokens,120000);
  assert.equal((await request(`/api/conversations/${id}?workspace_id=other`,{headers})).status,404);
  const info = await (await request(`/api/conversations/${id}`,{headers})).json();
  assert.equal(info.data.messages_count,0);
  assert.equal(info.data.metadata.model_key,settings.model_key);
  const full = await (await request('/api/conversations?limit=1',{headers})).json();
  assert.equal(full.data.total,1);
  assert.equal(full.data.conversations[0].id,id);
  const older = 'conv_20000101_000000_000';
  const old = {...document,id:older,updated_at:'2000-01-01T00:00:00',metadata:{...document.metadata,total_messages:1},messages:[{role:'user',content:'earlier'}]};
  await writeFile(join(directory,older+'.json'),JSON.stringify(old));
  await migrate(origin,{cookie:session,origin,'x-csrf-token':token});
  const filtered = await (await request('/api/conversations?non_empty=1&limit=1',{headers})).json();
  assert.equal(filtered.data.total,1);
  assert.equal(filtered.data.conversations[0].id,older);
  assert.equal(filtered.data.has_more,false);
});
