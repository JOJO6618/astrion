// review-server 回归：GET / PUT / 重新 GET / 409 / 非法请求 / 跨域 / 损坏文件不覆盖。
// 请求全部打到真实 reviewServer 中间件；能监听时走真实 localhost:端口（fetch），
// 沙箱禁止 listen 时自动降级为进程内真实 IncomingMessage + 响应捕获（同一套断言）。
// fixture 根目录、候选目录和 reviews.json 全部在 .tmp/ 内，绝不读写 design-library/.local/reviews.json。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { makeRoot, startServer, decision, readDocument, fileBytes } from './harness.mjs';

async function withServer(t) {
  const { root, file } = makeRoot(t);
  const transport = await startServer(t, { root, repository: path.join(root, 'repo') });
  return { ...transport, root, file };
}

test('GET 空记录返回 schemaVersion=1 / revision=0 / updatedAt=null / decisions={}', async (t) => {
  const { call, file, root } = await withServer(t);
  const response = await call({});
  assert.equal(response.status, 200);
  assert.match(String(response.headers.get('content-type')), /application\/json/);
  assert.deepEqual(response.payload, { schemaVersion: 1, revision: 0, updatedAt: null, decisions: {} });
  assert.equal(fs.existsSync(path.join(root, '.local')), false, 'GET 不应创建 .local 目录');
  assert.equal(fs.existsSync(file), false, 'GET 不应写盘');
});

test('PUT 写入文件、revision 递增、重新 GET 读到同一记录', async (t) => {
  const { call, file } = await withServer(t);
  const decisions = { 'btn-a': decision('merge', 'btn-b', '两个主按钮统一'), 'btn-b': decision('keep') };
  const put = await call({ method: 'PUT', body: { revision: 0, decisions } });
  assert.equal(put.status, 200, JSON.stringify(put.payload));
  assert.equal(put.payload.revision, 1);
  assert.equal(put.payload.schemaVersion, 1);
  assert.equal(typeof put.payload.updatedAt, 'string');
  assert.ok(!Number.isNaN(Date.parse(put.payload.updatedAt)), 'updatedAt 必须是可解析时间');
  assert.deepEqual(put.payload.decisions, decisions);

  const onDisk = readDocument(file);
  assert.equal(onDisk.revision, 1);
  assert.deepEqual(onDisk.decisions, decisions);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600, 'reviews.json 应为 0600');
  assert.deepEqual(
    fs.readdirSync(path.dirname(file)).filter((name) => name.endsWith('.tmp')),
    [], '原子写临时文件必须清理'
  );

  const get = await call({});
  assert.equal(get.status, 200);
  assert.deepEqual(get.payload, put.payload);
});

test('连续两次 PUT 正常递增 revision', async (t) => {
  const { call, file } = await withServer(t);
  const first = await call({ method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('keep') } } });
  assert.equal(first.status, 200);
  const second = await call({
    method: 'PUT',
    body: { revision: first.payload.revision, decisions: { 'btn-a': decision('keep'), 'btn-b': decision('keep') } }
  });
  assert.equal(second.status, 200);
  assert.equal(second.payload.revision, 2);
  assert.equal(readDocument(file).revision, 2);
});

test('revision 过期返回 409 并回传磁盘当前记录，且不覆盖文件', async (t) => {
  const { call, file } = await withServer(t);
  await call({ method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('keep') } } });
  const before = fileBytes(file);

  const conflict = await call({ method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('merge', 'btn-b') } } });
  assert.equal(conflict.status, 409);
  assert.equal(conflict.payload.error, '审阅版本已更新');
  assert.equal(conflict.payload.document.revision, 1);
  assert.deepEqual(conflict.payload.document.decisions, { 'btn-a': decision('keep') });
  assert.deepEqual(fileBytes(file), before, '409 不得改写磁盘文件');
});

test('非法审阅状态返回 400 且不写入', async (t) => {
  const { call, file } = await withServer(t);
  const response = await call({ method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('merged') } } });
  assert.equal(response.status, 400);
  assert.equal(response.payload.error, '审阅状态无效');
  assert.equal(fs.existsSync(file), false);
});

test('坏 JSON 返回 400 且不创建文件', async (t) => {
  const { call, file } = await withServer(t);
  const response = await call({ method: 'PUT', body: '{"revision":0,"decisions":' });
  assert.equal(response.status, 400);
  assert.equal(response.payload.error, '无效JSON');
  assert.equal(fs.existsSync(file), false);
});

test('超长备注（2001 字）返回 400，2000 字通过', async (t) => {
  const { call, file } = await withServer(t);
  const tooLong = await call({
    method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('keep', null, '备'.repeat(2001)) } }
  });
  assert.equal(tooLong.status, 400);
  assert.equal(tooLong.payload.error, '备注最多2000字');
  assert.equal(fs.existsSync(file), false);

  const ok = await call({
    method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('keep', null, '备'.repeat(2000)) } }
  });
  assert.equal(ok.status, 200);
});

test('跨站 Origin 写入被拒绝（403），GET 不受 Origin 限制', async (t) => {
  const { call, file } = await withServer(t);
  const evil = await call({ method: 'PUT', headers: { Origin: 'http://evil.example' }, body: { revision: 0, decisions: {} } });
  assert.equal(evil.status, 403);
  assert.equal(evil.payload.error, '不接受跨站审阅请求');

  const otherPort = await call({
    method: 'PUT', headers: { Origin: 'http://127.0.0.1:59999' }, body: { revision: 0, decisions: {} }
  });
  assert.equal(otherPort.status, 403, '不同端口的同源也不是同源');

  const nullOrigin = await call({ method: 'PUT', omitOrigin: true, body: { revision: 0, decisions: {} } });
  assert.equal(nullOrigin.status, 403, '缺少 Origin 的写入必须拒绝');

  assert.equal(fs.existsSync(file), false, '被拒绝的写入不得落盘');
  assert.equal((await call({})).status, 200, 'GET 无需 Origin');
});

test('缺少 X-Component-Room 或 Content-Type 非 JSON 的写入被拒绝', async (t) => {
  const { call, file } = await withServer(t);
  const noHeader = await call({ method: 'PUT', headers: { 'X-Component-Room': '0' }, body: { revision: 0, decisions: {} } });
  assert.equal(noHeader.status, 403);
  assert.equal(noHeader.payload.error, '审阅写入需要同源JSON请求');

  const plain = await call({
    method: 'PUT', headers: { 'Content-Type': 'text/plain' }, body: '{"revision":0,"decisions":{}}'
  });
  assert.equal(plain.status, 403);
  assert.equal(fs.existsSync(file), false);
});

test('非本机 Host 头被拒绝（403）', async (t) => {
  const { rawRequest } = await withServer(t);
  const response = await rawRequest({ method: 'GET', headers: { Host: 'evil.example', Origin: 'http://evil.example' } });
  assert.equal(response.status, 403);
  assert.equal(response.payload.error, '仅允许本机访问');

  const spoof = await rawRequest({ method: 'GET', headers: { Host: 'localhost.evil.example' } });
  assert.equal(spoof.status, 403);
});

test('不支持的方法返回 405', async (t) => {
  const { call, file } = await withServer(t);
  const post = await call({ method: 'POST', body: { revision: 0, decisions: {} } });
  assert.equal(post.status, 405);
  assert.equal(post.payload.error, '不支持此操作');
  const del = await call({ method: 'DELETE' });
  assert.equal(del.status, 405);
  assert.equal(fs.existsSync(file), false);
});

test('超长请求体（>512KB）被拒绝且不落盘', async (t) => {
  const { call, file } = await withServer(t);
  const huge = { revision: 0, decisions: {}, pad: 'x'.repeat(600 * 1024) };
  let response;
  try {
    response = await call({ method: 'PUT', body: huge });
  } catch (cause) {
    // 真实 TCP 下服务端提前结束响应，客户端可能拿到连接重置；关键断言是“未落盘”。
    assert.match(String(cause), /fetch failed|ECONNRESET|socket hang up/i);
    response = null;
  }
  if (response) {
    assert.equal(response.status, 413, `期望 413，实际 ${response.status}`);
    assert.equal(response.payload.error, '审阅记录过大');
  }
  assert.equal(fs.existsSync(file), false);
});

test('未知候选 id / 跨类别 / 自合并 / 循环 都在写入前被拒（400）', async (t) => {
  const { call, file } = await withServer(t);
  const cases = [
    [{ ghost: decision('keep') }, '候选不存在：ghost'],
    [{ 'btn-a': decision('merge', 'icon-a') }, '请选择同类别的组件'],
    [{ 'btn-a': decision('merge', 'btn-a') }, '不能合并到自身'],
    [{ 'btn-a': decision('merge', 'btn-b'), 'btn-b': decision('merge', 'btn-a') }, '合并关系不能形成循环']
  ];
  for (const [decisions, expected] of cases) {
    const response = await call({ method: 'PUT', body: { revision: 0, decisions } });
    assert.equal(response.status, 400, JSON.stringify(decisions));
    assert.equal(response.payload.error, expected);
  }
  assert.equal(fs.existsSync(file), false);
});

test('正常合并链 A→B→C 可写入并持久化', async (t) => {
  const { call, file } = await withServer(t);
  const decisions = {
    'btn-a': decision('merge', 'btn-b'), 'btn-b': decision('merge', 'btn-c'), 'btn-c': decision('keep')
  };
  const put = await call({ method: 'PUT', body: { revision: 0, decisions } });
  assert.equal(put.status, 200, JSON.stringify(put.payload));
  assert.deepEqual(readDocument(file).decisions, decisions);
  assert.deepEqual((await call({})).payload.decisions, decisions);
});

test('写入时只保留 status/targetId/note，丢弃多余字段', async (t) => {
  const { call, file } = await withServer(t);
  const put = await call({
    method: 'PUT',
    body: { revision: 0, decisions: { 'btn-a': { ...decision('keep'), evil: 'y', extra: 1 } } }
  });
  assert.equal(put.status, 200);
  assert.deepEqual(readDocument(file).decisions['btn-a'], decision('keep'));
});

test('磁盘文件是坏 JSON：GET/PUT 均 500，文件内容原样保留', async (t) => {
  const { call, file } = await withServer(t);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const broken = '{ this is not json';
  fs.writeFileSync(file, broken);

  const get = await call({});
  assert.equal(get.status, 500);
  assert.match(String(get.payload.error), /./);

  const put = await call({ method: 'PUT', body: { revision: 0, decisions: { 'btn-a': decision('keep') } } });
  assert.equal(put.status, 500);
  assert.equal(fs.readFileSync(file, 'utf8'), broken, '损坏文件绝不能被覆盖');
});

test('磁盘文件 schemaVersion 不符 / revision 非法：500 且不覆盖', async (t) => {
  const { call, file } = await withServer(t);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const badSchema = JSON.stringify({ schemaVersion: 2, revision: 0, updatedAt: null, decisions: {} }, null, 2);
  fs.writeFileSync(file, badSchema);
  assert.equal((await call({})).status, 500);
  assert.equal((await call({ method: 'PUT', body: { revision: 0, decisions: {} } })).status, 500);
  assert.equal(fs.readFileSync(file, 'utf8'), badSchema);

  const badRevision = JSON.stringify({ schemaVersion: 1, revision: -1, updatedAt: null, decisions: {} }, null, 2);
  fs.writeFileSync(file, badRevision);
  assert.equal((await call({})).status, 500);
  assert.equal(fs.readFileSync(file, 'utf8'), badRevision);
});

test('磁盘文件里存在违规决策（跨类别）：500 且不覆盖，提示校验失败', async (t) => {
  const { call, file } = await withServer(t);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const invalidDoc = JSON.stringify({
    schemaVersion: 1, revision: 3, updatedAt: null,
    decisions: { 'btn-a': decision('merge', 'icon-a') }
  }, null, 2);
  fs.writeFileSync(file, invalidDoc);
  const get = await call({});
  assert.equal(get.status, 500);
  assert.match(String(get.payload.error), /磁盘审阅文件校验失败/);
  assert.equal((await call({ method: 'PUT', body: { revision: 3, decisions: {} } })).status, 500);
  assert.equal(fs.readFileSync(file, 'utf8'), invalidDoc);
});

test('payload 结构非法（数组 / 缺 revision / 非数字 revision / 负 revision / decisions 非对象）返回 400', async (t) => {
  const { call, file } = await withServer(t);
  const payloads = [
    [],
    { decisions: {} },
    { revision: '0', decisions: {} },
    { revision: -1, decisions: {} },
    { revision: 0, decisions: [] }
  ];
  for (const payload of payloads) {
    const response = await call({ method: 'PUT', body: payload });
    assert.equal(response.status, 400, JSON.stringify(payload));
    assert.equal(response.payload.error, '无效审阅格式');
  }
  assert.equal(fs.existsSync(file), false);
});

test('非 /__review 路径交给下一个中间件', async (t) => {
  const { rawRequest, state } = await withServer(t);
  const response = await rawRequest({ method: 'GET', path: '/index.html' });
  assert.equal(response.status, 599);
  assert.equal(response.payload, 'NEXT');
  assert.equal(state.nextCalls, 1);
});

test('（诊断）本次请求通道', async (t) => {
  const { kind } = await withServer(t);
  assert.ok(['tcp', 'inproc'].includes(kind));
  if (kind === 'inproc') {
    console.warn('[transport] 无法监听端口，已降级为进程内真实中间件调用：只验证 HTTP 语义，不经过 TCP 栈');
  } else {
    console.warn('[transport] 使用真实 127.0.0.1 TCP 服务');
  }
});
