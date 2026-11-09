// useReviews 回归（不启动浏览器、不做视觉）：
// 写入排队/去抖、草稿恢复、版本冲突、规则拦截。
// 通过 mock 全局 fetch + localStorage 驱动真实 useReviews（打包自 design-library/room/useReviews.ts），
// 不读不写真实 design-library/.local/reviews.json。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { decision } from './harness.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const buildFile = path.join(here, '.build/useReviews.mjs');
if (!fs.existsSync(buildFile)) throw new Error('缺少 .build/useReviews.mjs，请先运行 node build.mjs');
const { useReviews } = await import(buildFile);

const DRAFT_KEY = 'astrion.component-room.draft.v1';
const candidates = [
  { id: 'btn-a', category: 'text-button' },
  { id: 'btn-b', category: 'text-button' },
  { id: 'btn-c', category: 'text-button' },
  { id: 'icon-a', category: 'icon-button' }
];

const doc = (revision, decisions = {}) => ({ schemaVersion: 1, revision, updatedAt: null, decisions });
const clone = (value) => JSON.parse(JSON.stringify(value));

function createStorage(initial = {}) {
  const store = new Map(Object.entries(initial));
  const ops = [];
  return {
    store, ops,
    getItem(key) { ops.push(['get', key]); return store.has(key) ? store.get(key) : null; },
    setItem(key, value) { ops.push(['set', key, value]); store.set(key, String(value)); },
    removeItem(key) { store.delete(key); },
    readDraft() { const raw = store.get(DRAFT_KEY); return raw ? JSON.parse(raw) : null; }
  };
}

/**
 * 可控的 fetch mock：
 *  - remote  磁盘上的当前记录；
 *  - puts    记录每次 PUT（含请求体）；
 *  - gate    可挂起 PUT 响应，用来制造“保存过程中继续编辑”；
 *  - conflictOnce / failGet 用来制造 409 与初始化失败。
 */
function createBackend(initial) {
  const backend = {
    remote: clone(initial),
    puts: [],
    gets: 0,
    gate: null,
    failGet: null,
    conflictOnce: false
  };
  backend.fetch = async (url, init = {}) => {
    const method = (init.method || 'GET').toUpperCase();
    if (method === 'GET') {
      backend.gets += 1;
      if (backend.failGet) throw new Error(backend.failGet);
      return jsonResponse(200, clone(backend.remote));
    }
    const body = JSON.parse(init.body);
    backend.puts.push(clone(body));
    if (backend.gate) await backend.gate.promise;
    if (backend.conflictOnce) {
      backend.conflictOnce = false;
      return jsonResponse(409, { error: '审阅版本已更新', document: clone(backend.remote) });
    }
    if (body.revision !== backend.remote.revision) {
      return jsonResponse(409, { error: '审阅版本已更新', document: clone(backend.remote) });
    }
    backend.remote = {
      schemaVersion: 1,
      revision: backend.remote.revision + 1,
      updatedAt: new Date().toISOString(),
      decisions: clone(body.decisions)
    };
    return jsonResponse(200, clone(backend.remote));
  };
  return backend;
}

function jsonResponse(status, payload) {
  return { status, ok: status >= 200 && status < 300, json: async () => clone(payload) };
}

function withGlobals(t, backend, storage) {
  const previousFetch = globalThis.fetch;
  const previousStorage = globalThis.localStorage;
  globalThis.fetch = backend.fetch;
  globalThis.localStorage = storage;
  t.after(() => {
    globalThis.fetch = previousFetch;
    if (previousStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = previousStorage;
  });
}

async function waitFor(predicate, label, timeout = 3000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`等待超时：${label}`);
}

test('初始化：GET 后进入 saved，document 为远端记录', async (t) => {
  const backend = createBackend(doc(4, { 'btn-a': decision('keep') }));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();
  assert.equal(reviews.ready.value, true);
  assert.equal(reviews.saveState.value, 'saved');
  assert.equal(reviews.document.value.revision, 4);
  assert.deepEqual(reviews.document.value.decisions, { 'btn-a': decision('keep') });
  assert.equal(backend.puts.length, 0);
  assert.equal(reviews.error.value, '');
});

test('连续快速编辑只触发一次 PUT（去抖 + 排队），落盘为最新合并结果', async (t) => {
  const backend = createBackend(doc(2));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();

  assert.equal(reviews.update('btn-a', { status: 'merge', targetId: 'btn-b' }), true);
  assert.equal(reviews.update('btn-b', { status: 'keep' }), true);
  assert.equal(reviews.update('btn-c', { note: '备注' }), true);
  assert.equal(reviews.saveState.value, 'saving');
  assert.equal(backend.puts.length, 0, '300ms 内不应立即写盘');

  await waitFor(() => reviews.saveState.value === 'saved', '保存完成');
  assert.equal(backend.puts.length, 1, '三次编辑应合并成一次 PUT');
  assert.equal(backend.puts[0].revision, 2);
  assert.deepEqual(backend.puts[0].decisions, {
    'btn-a': decision('merge', 'btn-b'), 'btn-b': decision('keep', null), 'btn-c': decision('pending', null, '备注')
  });
  assert.equal(reviews.document.value.revision, 3);
  assert.deepEqual(backend.remote.decisions, backend.puts[0].decisions);
});

test('保存进行中继续编辑：用新 revision 再发一次 PUT，不丢改动', async (t) => {
  const backend = createBackend(doc(0));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();

  let release;
  backend.gate = { promise: new Promise((resolve) => { release = resolve; }) };
  reviews.update('btn-a', { status: 'keep' });
  await waitFor(() => backend.puts.length === 1, '第一次 PUT 发出');
  assert.equal(backend.puts[0].revision, 0);

  // 第一次 PUT 还没返回时再编辑
  reviews.update('btn-b', { status: 'keep' });
  release();
  backend.gate = null;

  await waitFor(() => reviews.saveState.value === 'saved', '两轮保存完成');
  assert.equal(backend.puts.length, 2, '保存中编辑必须在同一轮 flush 中补发');
  assert.equal(backend.puts[1].revision, 1, '第二轮 PUT 必须带新 revision');
  assert.deepEqual(backend.puts[1].decisions['btn-b'], decision('keep'));
  assert.equal(reviews.document.value.revision, 2);
  assert.equal(storage.readDraft().dirty, false, '全部落盘后草稿标记为 clean');
});

test('草稿恢复：baseRevision 与磁盘一致时自动回写，清空 dirty 标记', async (t) => {
  const backend = createBackend(doc(5, { 'btn-a': decision('pending') }));
  const draft = { baseRevision: 5, dirty: true, decisions: { 'btn-a': decision('keep', null, '草稿') } };
  const storage = createStorage({ [DRAFT_KEY]: JSON.stringify(draft) });
  withGlobals(t, backend, storage);

  const reviews = useReviews(candidates);
  await reviews.initialize();
  await waitFor(() => reviews.saveState.value === 'saved', '草稿自动回写');
  assert.deepEqual(reviews.document.value.decisions, { 'btn-a': decision('keep', null, '草稿') });
  assert.equal(backend.puts.length, 1);
  assert.equal(backend.puts[0].revision, 5);
  assert.equal(backend.remote.decisions['btn-a'].note, '草稿');
  assert.equal(storage.readDraft().dirty, false);
});

test('草稿 baseRevision 落后于磁盘：进入 conflict，不自动写盘', async (t) => {
  const backend = createBackend(doc(9, { 'btn-a': decision('merge', 'btn-b') }));
  const draft = { baseRevision: 7, dirty: true, decisions: { 'btn-a': decision('keep') } };
  const storage = createStorage({ [DRAFT_KEY]: JSON.stringify(draft) });
  withGlobals(t, backend, storage);

  const reviews = useReviews(candidates);
  await reviews.initialize();
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(reviews.saveState.value, 'conflict');
  assert.equal(backend.puts.length, 0, '冲突时不得自动覆盖磁盘');
  assert.deepEqual(reviews.document.value.decisions, { 'btn-a': decision('keep') }, '保留本地草稿内容给用户选择');
  assert.match(reviews.error.value, /草稿/);
});

test('草稿内容非法（未知候选）被视为无效并丢弃', async (t) => {
  const backend = createBackend(doc(1, { 'btn-a': decision('keep') }));
  const draft = { baseRevision: 1, dirty: true, decisions: { ghost: decision('keep') } };
  const storage = createStorage({ [DRAFT_KEY]: JSON.stringify(draft) });
  withGlobals(t, backend, storage);

  const reviews = useReviews(candidates);
  await reviews.initialize();
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(reviews.saveState.value, 'saved');
  assert.equal(backend.puts.length, 0);
  assert.deepEqual(reviews.document.value.decisions, { 'btn-a': decision('keep') }, '应使用磁盘记录');
});

test('PUT 返回 409：进入 conflict、保留远端快照，useRemote 采用远端、useDraft 重试成功', async (t) => {
  const backend = createBackend(doc(3));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();

  backend.conflictOnce = true;
  backend.remote = doc(4, { 'btn-b': decision('keep') });
  reviews.update('btn-a', { status: 'keep' });
  await waitFor(() => reviews.saveState.value === 'conflict', '进入冲突');

  assert.equal(reviews.document.value.revision, 3, '冲突时本地 revision 不前进');
  assert.equal(reviews.saveState.value, 'conflict');
  assert.match(reviews.error.value, /另一页面/);

  // 冲突状态下继续编辑不应触发新的 PUT
  const putsBefore = backend.puts.length;
  reviews.update('btn-c', { status: 'keep' });
  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(backend.puts.length, putsBefore, '冲突期间不得继续写盘');

  reviews.useRemote();
  assert.equal(reviews.saveState.value, 'saved');
  assert.equal(reviews.document.value.revision, 4);
  assert.deepEqual(reviews.document.value.decisions, { 'btn-b': decision('keep') }, '采用远端后本地改动被丢弃');

  // 再制造一次冲突，走 useDraft 重试
  backend.conflictOnce = true;
  backend.remote = doc(5, { 'btn-b': decision('keep') });
  reviews.update('btn-a', { status: 'merge', targetId: 'btn-b' });
  await waitFor(() => reviews.saveState.value === 'conflict', '第二次冲突');
  await reviews.useDraft();
  await waitFor(() => reviews.saveState.value === 'saved', 'useDraft 后保存完成');
  assert.equal(reviews.document.value.revision, 6);
  assert.deepEqual(backend.remote.decisions['btn-a'], decision('merge', 'btn-b'));
});

test('规则拦截：自合并/跨类别被拒，返回 false 且不落盘', async (t) => {
  const backend = createBackend(doc(0));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();

  assert.equal(reviews.update('btn-a', { status: 'merge', targetId: 'btn-a' }), false);
  assert.equal(reviews.error.value, '不能合并到自身');
  assert.equal(reviews.update('btn-a', { status: 'merge', targetId: 'icon-a' }), false);
  assert.equal(reviews.error.value, '请选择同类别的组件');
  assert.deepEqual(reviews.document.value.decisions, {}, '被拒绝的改动不得写入本地状态');

  await new Promise((resolve) => setTimeout(resolve, 400));
  assert.equal(backend.puts.length, 0);
  assert.equal(reviews.saveState.value, 'saved');
});

test('状态切出 merge 时自动清空 targetId', async (t) => {
  const backend = createBackend(doc(0));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();

  reviews.update('btn-a', { status: 'merge', targetId: 'btn-b' });
  assert.equal(reviews.document.value.decisions['btn-a'].targetId, 'btn-b');
  reviews.update('btn-a', { status: 'keep' });
  assert.equal(reviews.document.value.decisions['btn-a'].targetId, null);
  await waitFor(() => reviews.saveState.value === 'saved', '保存完成');
  assert.equal(backend.remote.decisions['btn-a'].targetId, null);
});

test('初始化 GET 失败但有草稿：恢复草稿并标记 error', async (t) => {
  const backend = createBackend(doc(2));
  backend.failGet = '网络不可用';
  const draft = { baseRevision: 2, dirty: true, decisions: { 'btn-a': decision('keep') } };
  const storage = createStorage({ [DRAFT_KEY]: JSON.stringify(draft) });
  withGlobals(t, backend, storage);

  const reviews = useReviews(candidates);
  await reviews.initialize();
  assert.equal(reviews.ready.value, true);
  assert.equal(reviews.saveState.value, 'error');
  assert.match(reviews.error.value, /网络不可用/);
  assert.deepEqual(reviews.document.value.decisions, { 'btn-a': decision('keep') });
  assert.equal(backend.puts.length, 0, '离线时不得尝试写盘');
});

test('未 ready 前的 update 被拒绝', async (t) => {
  const backend = createBackend(doc(0));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  assert.equal(reviews.update('btn-a', { status: 'keep' }), false);
  await reviews.initialize();
  assert.equal(reviews.update('btn-a', { status: 'keep' }), true);
  await waitFor(() => reviews.saveState.value === 'saved', '保存完成');
});

test('reviewedCount 只统计非 pending 的候选', async (t) => {
  const backend = createBackend(doc(0, { 'btn-a': decision('keep'), 'btn-b': decision('pending') }));
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);
  await reviews.initialize();
  assert.equal(reviews.reviewedCount.value, 1);
  reviews.update('btn-b', { status: 'merge', targetId: 'btn-c' });
  assert.equal(reviews.reviewedCount.value, 2);
  await waitFor(() => reviews.saveState.value === 'saved', '保存完成');
});

test('GET 持续失败：flush 重试只重新初始化，绝不假报 saved', async (t) => {
  const backend = createBackend(doc(0));
  backend.failGet = '网络不可用';
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);

  await reviews.initialize();
  assert.equal(reviews.saveState.value, 'error');
  assert.equal(reviews.document.value.revision, 0);
  const getsAfterInit = backend.gets;

  assert.equal(reviews.update('btn-a', { status: 'keep' }), true);
  assert.equal(reviews.saveState.value, 'saving');

  await waitFor(() => backend.gets > getsAfterInit, 'flush 触发重新 GET');
  await new Promise((resolve) => setTimeout(resolve, 100));

  assert.equal(reviews.saveState.value, 'error', 'GET 持续失败时状态必须是 error');
  assert.notEqual(reviews.saveState.value, 'saved', '绝不能假报 saved');
  assert.match(reviews.error.value, /网络不可用/);
  assert.equal(backend.puts.length, 0, '未成功初始化前不得 PUT');
  assert.equal(reviews.document.value.revision, 0, 'revision 不得凭空前进');

  // 再显式重试一次，仍然不能变成 saved
  await reviews.flush();
  assert.equal(reviews.saveState.value, 'error');
  assert.equal(backend.puts.length, 0);
  assert.equal(reviews.document.value.revision, 0);
});

test('首次 GET 失败后远端已有新记录：flush 重新 GET 进入冲突，不用 revision 0 覆盖', async (t) => {
  const backend = createBackend(doc(0));
  backend.failGet = '首次加载失败';
  const storage = createStorage();
  withGlobals(t, backend, storage);
  const reviews = useReviews(candidates);

  await reviews.initialize();
  assert.equal(reviews.saveState.value, 'error');
  assert.equal(reviews.document.value.revision, 0);

  // 离线期间用户编辑，落到本地草稿（baseRevision 仍是 0）
  assert.equal(reviews.update('btn-a', { status: 'keep', note: '离线草稿' }), true);
  assert.deepEqual(storage.readDraft(), {
    baseRevision: 0, dirty: true, decisions: { 'btn-a': decision('keep', null, '离线草稿') }
  });

  // 期间磁盘已被另一页面更新到 revision 2
  backend.failGet = null;
  backend.remote = doc(2, { 'btn-b': decision('keep'), 'btn-c': decision('keep') });

  const getsBefore = backend.gets;
  await waitFor(() => backend.gets > getsBefore, 'flush 重新 GET');
  await waitFor(() => reviews.saveState.value === 'conflict', '进入冲突');

  assert.equal(backend.puts.length, 0, '不得用 revision 0 直接 PUT 覆盖磁盘');
  // 重新 GET 成功后 revision 跟着远端走（2）；冲突期间不写盘，后续 useDraft/useRemote 会显式重置 revision。
  assert.equal(reviews.document.value.revision, 2, '绝不能停在 revision 0 当作可写基线');
  assert.deepEqual(
    reviews.document.value.decisions,
    { 'btn-a': decision('keep', null, '离线草稿') },
    '本地草稿必须保留给用户选择'
  );
  assert.match(reviews.error.value, /草稿/);

  // 选择保留草稿：提交时应使用远端 revision 2，而不是 0
  await reviews.useDraft();
  await waitFor(() => reviews.saveState.value === 'saved', 'useDraft 保存完成');
  assert.equal(backend.puts.length, 1);
  assert.equal(backend.puts[0].revision, 2, 'PUT 必须带远端最新 revision');
  assert.equal(backend.remote.revision, 3);
  assert.deepEqual(backend.remote.decisions['btn-a'], decision('keep', null, '离线草稿'));
});
