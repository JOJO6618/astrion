import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addNode, createHistory, createMessagesList, forkMessage, getDeepestChildId,
  selectBranch, siblingInfo, switchSibling, toModelMessages, validateHistory,
} from '../../_experiments/chat-branch-prototype/js/tree.js';
import { ChatStore } from '../../_experiments/chat-branch-prototype/js/store.js';
import { loadHistory, saveHistory, STORAGE_KEY } from '../../_experiments/chat-branch-prototype/js/persistence.js';

function fixture() {
  const history = createHistory();
  const u1 = addNode(history, { id: 'm_u1', role: 'user', content: '原始问题' });
  const a1 = addNode(history, { id: 'm_a1', parentId: u1.id, role: 'assistant', content: '原始回答' });
  const u2 = addNode(history, { id: 'm_u2', parentId: a1.id, role: 'user', content: '原始追问' });
  const a2 = addNode(history, { id: 'm_a2', parentId: u2.id, role: 'assistant', content: '原始后续回答' });
  return { history, u1, a1, u2, a2 };
}
async function* immediate({ messages, fail }) {
  yield `回复：${messages.at(-1).content}`;
  if (fail) throw new Error('测试连接失败');
  yield '。';
}
function makeStore(options = {}) {
  const sample = fixture();
  return { ...sample, store: new ChatStore({ history: sample.history, stream: immediate, ...options }) };
}

function gatedStream() {
  let release;
  let ready;
  const waiting = new Promise((resolve) => { release = resolve; });
  const started = new Promise((resolve) => { ready = resolve; });
  return {
    release, started,
    stream: async function* () {
      yield '第一段';
      ready();
      await waiting;
      yield '第二段';
    },
  };
}

test('空历史可验证，并且能建立一条交替的消息路径', () => {
  const history = createHistory();
  assert.equal(validateHistory(history), history);
  assert.deepEqual(createMessagesList(history), []);
  assert.deepEqual(createMessagesList(fixture().history).map((n) => n.id), ['m_u1', 'm_a1', 'm_u2', 'm_a2']);
});

test('用户保存原地更新，不增加节点或版本，不生成并保留原后续', async () => {
  let calls = 0;
  const { store, history, u1, a2 } = makeStore({ stream: async function* () { calls++; } });
  const original = structuredClone(history.messages);
  const selections = structuredClone(store.history.selectedChildren);
  const edited = await store.edit(u1.id, '只保存的新问题');
  assert.equal(calls, 0);
  assert.equal(edited.id, u1.id);
  assert.equal(edited.content, '只保存的新问题');
  assert.deepEqual(edited.childrenIds, original[u1.id].childrenIds);
  assert.equal(Object.keys(store.history.messages).length, 4);
  assert.equal(store.history.currentId, a2.id);
  assert.deepEqual(store.history.selectedChildren, selections);
  for (const [id, node] of Object.entries(original)) {
    if (id !== u1.id) assert.deepEqual(store.history.messages[id], node);
  }
  assert.equal(siblingInfo(store.history, edited.id).count, 1);
  assert.equal(store.lastRequest, null);
});

test('用户编辑发送创建用户分支和AI回复，请求只包含新路径', async () => {
  const { store, u1 } = makeStore();
  const result = await store.edit(u1.id, '发送的新问题', { send: true });
  const edited = store.history.messages[result.parentId];
  assert.equal(edited.sourceId, u1.id);
  assert.equal(result.status, 'complete');
  assert.deepEqual(store.lastRequest.messages, [{ role: 'user', content: '发送的新问题' }]);
  assert.equal(store.history.messages.m_a2.content, '原始后续回答');
  assert.equal(store.history.currentId, result.id);
});

test('编辑历史追问只继承共同前缀，不混入旧追问与后续回答', async () => {
  const { store, u2, a1 } = makeStore();
  const result = await store.edit(u2.id, '另一个追问', { send: true });
  assert.equal(store.history.messages[result.parentId].parentId, a1.id);
  assert.deepEqual(store.lastRequest.messages.map((m) => m.content), ['原始问题', '原始回答', '另一个追问']);
});

test('保存后重生成使用已修改的原用户节点，不重复创建用户消息', async () => {
  const { store, u2, a2 } = makeStore();
  const edited = await store.edit(u2.id, '先保存再生成');
  const users = Object.values(store.history.messages).filter((n) => n.role === 'user').length;
  const response = await store.regenerate(a2.id);
  assert.equal(edited.id, u2.id);
  assert.equal(response.parentId, u2.id);
  assert.equal(store.lastRequest.messages.at(-1).content, '先保存再生成');
  assert.equal(Object.values(store.history.messages).filter((n) => n.role === 'user').length, users);
});

test('AI编辑原地保存，节点和版本数不变，后续继续沿原路径', async () => {
  const { store, a1, a2 } = makeStore();
  const children = [...store.history.messages[a1.id].childrenIds];
  const edited = await store.edit(a1.id, '我手动修改的AI正文');
  assert.equal(edited.id, a1.id);
  assert.equal(edited.parentId, a1.parentId);
  assert.deepEqual(edited.childrenIds, children);
  assert.equal(Object.keys(store.history.messages).length, 4);
  assert.equal(siblingInfo(store.history, a1.id).count, 1);
  assert.equal(store.history.currentId, a2.id);
  await store.send('基于修改版继续');
  assert.deepEqual(store.lastRequest.messages.map((m) => m.content), ['原始问题', '我手动修改的AI正文', '原始追问', '原始后续回答', '基于修改版继续']);
});

test('重新生成AI历史回复在原用户下创建兄弟，且不含被替换回答', async () => {
  const { store, a1, u1 } = makeStore();
  const response = await store.regenerate(a1.id);
  assert.equal(response.parentId, u1.id);
  assert.equal(response.sourceId, a1.id);
  assert.equal(response.kind, 'regenerated');
  assert.equal(siblingInfo(store.history, response.id).count, 2);
  assert.deepEqual(store.lastRequest.messages, [{ role: 'user', content: '原始问题' }]);
});

test('版本切换恢复对应的后续，版本边界不会越界', async () => {
  const { store, u1, a2 } = makeStore();
  const response = await store.edit(u1.id, '新根版本', { send: true });
  const edited = store.history.messages[response.parentId];
  assert.equal(switchSibling(store.history, edited.id, -1), a2.id);
  assert.equal(siblingInfo(store.history, u1.id).index, 0);
  assert.equal(switchSibling(store.history, u1.id, -1), a2.id);
  assert.equal(switchSibling(store.history, u1.id, 1), response.id);
  assert.equal(switchSibling(store.history, edited.id, 1), response.id);
});

test('多层分叉会记住上次选中的子路径，而非总回到最新回复', async () => {
  const { store, u1, a1, a2 } = makeStore();
  const newest = await store.regenerate(a1.id);
  store.navigate(a1.id);
  assert.equal(store.history.currentId, a2.id);
  const otherResponse = await store.edit(u1.id, '另一棵根分支', { send: true });
  const otherRoot = store.history.messages[otherResponse.parentId];
  store.navigate(u1.id);
  assert.equal(store.history.currentId, a2.id);
  assert.notEqual(store.history.currentId, newest.id);
  store.navigate(otherRoot.id);
  assert.equal(getDeepestChildId(store.history, u1.id), a2.id);
});

test('模拟失败保留部分回复，重试创建新兄弟，失败记录不覆盖', async () => {
  const { store, a1 } = makeStore();
  store.toggleFailure();
  const failed = await store.regenerate(a1.id);
  assert.equal(failed.status, 'error');
  assert.equal(failed.error, '测试连接失败');
  assert.ok(failed.content.length > 0);
  const snapshot = structuredClone(failed);
  const retried = await store.regenerate(failed.id);
  assert.equal(retried.status, 'complete');
  assert.equal(retried.kind, 'retried');
  assert.equal(retried.parentId, failed.parentId);
  assert.equal(retried.sourceId, failed.id);
  assert.equal(siblingInfo(store.history, retried.id).count, 3);
  assert.deepEqual(store.history.messages[failed.id], snapshot);
  assert.equal(store.failNext, false);
});

test('生成中切换分支不会污染可见分支，完成不抢回当前路径', async () => {
  const gate = gatedStream();
  const { store, a1, a2 } = makeStore({ stream: gate.stream });
  const pending = store.regenerate(a1.id);
  await gate.started;
  const target = store.active.nodeId;
  store.navigate(a1.id);
  assert.equal(store.history.currentId, a2.id);
  gate.release();
  await pending;
  assert.equal(store.history.currentId, a2.id);
  assert.equal(store.history.messages[target].content, '第一段第二段');
  assert.equal(store.history.messages[a2.id].content, '原始后续回答');
});

test('停止之后忽略迟到的输出，保留部分回复并允许重试', async () => {
  const gate = gatedStream();
  const { store, a1 } = makeStore({ stream: gate.stream });
  const pending = store.regenerate(a1.id);
  await gate.started;
  const target = store.active.nodeId;
  store.stop();
  gate.release();
  await pending;
  assert.equal(store.history.messages[target].content, '第一段');
  assert.equal(store.history.messages[target].status, 'stopped');
  assert.equal(store.active, null);
  store.stream = immediate;
  assert.equal((await store.regenerate(target)).status, 'complete');
});

test('运行中禁止第二个生成/编辑写者，但允许导航', async () => {
  const gate = gatedStream();
  const { store, a1, u1 } = makeStore({ stream: gate.stream });
  const pending = store.regenerate(a1.id);
  await gate.started;
  const count = Object.keys(store.history.messages).length;
  assert.throws(() => store.regenerate(a1.id), /先停止/);
  await assert.rejects(store.send('并发输入'), /先停止/);
  await assert.rejects(store.edit(u1.id, '并发编辑'), /先停止/);
  assert.equal(Object.keys(store.history.messages).length, count);
  store.navigate(u1.id);
  gate.release();
  await pending;
});

test('空编辑与无效操作不创建节点，不破坏当前路径', async () => {
  const { store, u1, a1 } = makeStore();
  const before = JSON.stringify(store.history);
  await assert.rejects(store.edit(u1.id, '  '), /不能为空/);
  await assert.rejects(store.edit(a1.id, '编辑AI', { send: true }), /仅支持保存/);
  assert.throws(() => store.regenerate(u1.id), /只能重新生成/);
  assert.equal(JSON.stringify(store.history), before);
  const pending = new ChatStore({ stream: immediate });
  const user = addNode(pending.history, { role: 'user', content: '待生成' });
  await pending.edit(user.id, '保存待生成');
  await assert.rejects(pending.send('第二条用户'), /先为已保存/);
});

test('刷新保存整棵树、版本顺序、选择记忆，而非只保存可见路径', async () => {
  const data = new Map();
  const storage = { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  const { store, u1, a1 } = makeStore({ persist: (history) => saveHistory(storage, history) });
  await store.edit(u1.id, '保留的新根', { send: true });
  store.navigate(a1.id);
  const restored = loadHistory(storage, () => { throw new Error('不能回退'); });
  assert.deepEqual(restored, store.history);
  assert.equal(Object.keys(restored.messages).length, 6);
  assert.equal(restored.currentId, 'm_a2');
  assert.ok(data.has(STORAGE_KEY));
});

test('刷新中断生成时恢复为停止并立即保存，原有部分内容和分支仍保留', () => {
  const { history, u2 } = fixture();
  const partial = addNode(history, { parentId: u2.id, role: 'assistant', content: '半截回复', status: 'streaming' });
  let persisted;
  const store = new ChatStore({ history, stream: immediate, persist: (value) => { persisted = structuredClone(value); } });
  assert.equal(store.history.messages[partial.id].status, 'stopped');
  assert.equal(store.history.messages[partial.id].content, '半截回复');
  assert.equal(Object.keys(store.history.messages).length, 5);
  assert.equal(persisted.messages[partial.id].status, 'stopped');
  assert.equal(persisted.revision, history.revision + 1);
});

test('浏览器存储失败不会把成功回复变成生成失败，也不会重复生成', async () => {
  const { store, a1 } = makeStore({ persist: () => { throw new Error('quota'); } });
  const response = await store.regenerate(a1.id);
  assert.equal(response.status, 'complete');
  assert.match(store.storageError, /存储不可用/);
  assert.equal(siblingInfo(store.history, response.id).count, 2);
});

test('损坏存储必须报错，不静默回退并覆盖已有数据', () => {
  const storage = { getItem: () => '{bad json' };
  assert.throws(() => loadHistory(storage, createHistory), SyntaxError);
});

test('拒绝孤立节点、循环、重复父子连接及无效分支选择', () => {
  const { history } = fixture();
  const orphan = structuredClone(history);
  delete orphan.messages.m_u1;
  assert.throws(() => validateHistory(orphan));
  const cycle = structuredClone(history);
  cycle.messages.m_a2.childrenIds.push('m_u1');
  assert.throws(() => validateHistory(cycle));
  const duplicate = structuredClone(history);
  duplicate.messages.m_u1.childrenIds.push('m_a1');
  assert.throws(() => validateHistory(duplicate));
  const selection = structuredClone(history);
  selection.selectedChildren.m_u1 = 'm_a2';
  assert.throws(() => validateHistory(selection));
});

test('拒绝重复ID、不交替角色；文本不作HTML或代码处理', () => {
  const { history, u1 } = fixture();
  assert.throws(() => addNode(history, { id: u1.id, role: 'user', content: '重复' }));
  assert.throws(() => addNode(history, { parentId: u1.id, role: 'user', content: '同角色' }));
  const value = '<img src=x onerror=alert(1)>\n  原样保存  ';
  const node = forkMessage(history, u1.id, value);
  assert.equal(node.content, value);
  assert.deepEqual(toModelMessages(history, node.id), [{ role: 'user', content: value }]);
});

test('已有兄弟版本时保存仍不增加分支或改变兄弟顺序', async () => {
  const { store, u1, a1 } = makeStore();
  await store.edit(u1.id, '发送版', { send: true });
  const generated = await store.regenerate(a1.id);
  const nodes = Object.keys(store.history.messages);
  const cursor = store.history.currentId;
  const rootVersions = siblingInfo(store.history, u1.id).ids;
  const aiVersions = siblingInfo(store.history, a1.id).ids;
  await store.edit(u1.id, '原地保存用户');
  await store.edit(a1.id, '原地保存AI');
  assert.deepEqual(Object.keys(store.history.messages), nodes);
  assert.deepEqual(siblingInfo(store.history, u1.id).ids, rootVersions);
  assert.deepEqual(siblingInfo(store.history, a1.id).ids, aiVersions);
  assert.equal(store.history.currentId, cursor);
  assert.equal(store.history.messages[generated.id].content, '回复：原始问题。');
});

test('连续保存与未改正文保存均不产生新节点或分支点', async () => {
  const { store, u1, a1 } = makeStore();
  const topology = () => Object.values(store.history.messages).map(({ id, parentId, childrenIds }) => ({ id, parentId, childrenIds: [...childrenIds] }));
  const before = topology();
  for (let i = 0; i < 5; i++) {
    await store.edit(u1.id, `第${i}次保存`);
    await store.edit(a1.id, '同一段AI正文');
  }
  assert.deepEqual(topology(), before);
  assert.equal(siblingInfo(store.history, u1.id).count, 1);
  assert.equal(siblingInfo(store.history, a1.id).count, 1);
});

test('160次混合保存、发送、重生成和导航，只允许保存修改原正文', async () => {
  const { store } = makeStore();
  let rng = 7811;
  const random = (max) => { rng = (rng * 1664525 + 1013904223) >>> 0; return rng % max; };
  for (let i = 0; i < 160; i++) {
    const previous = Object.values(store.history.messages).map((n) => [n.id, n.content]);
    const nodes = Object.values(store.history.messages);
    const target = nodes[random(nodes.length)];
    let savedId = null;
    if (i % 4 === 0) store.navigate(target.id);
    else if (i % 4 === 1) { savedId = target.id; await store.edit(target.id, `第${i}次保存`); }
    else if (target.role === 'assistant') await store.regenerate(target.id);
    else await store.edit(target.id, `第${i}次发送`, { send: true });
    validateHistory(store.history);
    for (const [id, content] of previous) {
      if (id !== savedId) assert.equal(store.history.messages[id].content, content);
    }
    if (savedId) assert.equal(Object.keys(store.history.messages).length, nodes.length);
    const path = createMessagesList(store.history);
    assert.equal(new Set(path.map((n) => n.id)).size, path.length);
    assert.equal(path.at(-1).id, store.history.currentId);
  }
});
