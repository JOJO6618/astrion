// validateDecisions / finalMergeTarget 规则回归：同类合并、跨类拒绝、自合并拒绝、循环链拒绝、正常链允许。
import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const { validateDecisions, finalMergeTarget } = await import(path.join(here, '.build/reviewRules.mjs'));

const candidates = [
  { id: 'btn-a', category: 'text-button' },
  { id: 'btn-b', category: 'text-button' },
  { id: 'btn-c', category: 'text-button' },
  { id: 'icon-a', category: 'icon-button' },
  { id: 'menu-a', category: 'menu' }
];

const dec = (status, targetId = null, note = '') => ({ status, targetId, note });

test('空决策与常规 pending/keep 决策通过', () => {
  assert.equal(validateDecisions({}, candidates), null);
  assert.equal(validateDecisions({ 'btn-a': dec('pending'), 'btn-b': dec('keep') }, candidates), null);
});

test('同类别合并被允许（单跳）', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('merge', 'btn-b') }, candidates), null);
});

test('跨类别合并被拒绝', () => {
  const error = validateDecisions({ 'btn-a': dec('merge', 'icon-a') }, candidates);
  assert.equal(error, '请选择同类别的组件');
  assert.equal(validateDecisions({ 'menu-a': dec('merge', 'btn-b') }, candidates), '请选择同类别的组件');
});

test('合并到自身被拒绝', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('merge', 'btn-a') }, candidates), '不能合并到自身');
});

test('双向循环被拒绝', () => {
  assert.equal(
    validateDecisions({ 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-a') }, candidates),
    '合并关系不能形成循环'
  );
});

test('三节点循环被拒绝，且不依赖遍历顺序', () => {
  const cycle = { 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-c'), 'btn-c': dec('merge', 'btn-a') };
  assert.equal(validateDecisions(cycle, candidates), '合并关系不能形成循环');
  const reversed = Object.fromEntries(Object.entries(cycle).reverse());
  assert.equal(validateDecisions(reversed, candidates), '合并关系不能形成循环');
});

test('指向自身链外的环（btn-a→btn-b→btn-c→btn-b）也被拒绝', () => {
  assert.equal(
    validateDecisions(
      { 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-c'), 'btn-c': dec('merge', 'btn-b') },
      candidates
    ),
    '合并关系不能形成循环'
  );
});

test('正常合并链被允许（A→B→C，C 保留）', () => {
  const decisions = { 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-c'), 'btn-c': dec('keep') };
  assert.equal(validateDecisions(decisions, candidates), null);
});

test('链尾无决策也允许', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-c') }, candidates), null);
});

test('未知候选 id 被拒绝', () => {
  assert.equal(validateDecisions({ ghost: dec('keep') }, candidates), '候选不存在：ghost');
});

test('无效状态被拒绝', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('merged') }, candidates), '审阅状态无效');
  assert.equal(validateDecisions({ 'btn-a': { status: '', targetId: null, note: '' } }, candidates), '审阅状态无效');
  assert.equal(validateDecisions({ 'btn-a': { targetId: null, note: '' } }, candidates), '审阅状态无效');
});

test('非合并状态携带 targetId 被拒绝', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('keep', 'btn-b') }, candidates), '仅合并状态可以指定目标');
  assert.equal(validateDecisions({ 'btn-a': dec('pending', 'btn-b') }, candidates), '仅合并状态可以指定目标');
});

test('合并目标不存在被拒绝', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('merge', 'ghost') }, candidates), '请选择存在的合并目标');
  assert.equal(validateDecisions({ 'btn-a': dec('merge', null) }, candidates), '请选择存在的合并目标');
});

test('备注长度边界：2000 通过，2001 拒绝，非字符串拒绝', () => {
  assert.equal(validateDecisions({ 'btn-a': dec('keep', null, 'x'.repeat(2000)) }, candidates), null);
  assert.equal(validateDecisions({ 'btn-a': dec('keep', null, 'x'.repeat(2001)) }, candidates), '备注最多2000字');
  assert.equal(validateDecisions({ 'btn-a': { status: 'keep', targetId: null, note: 5 } }, candidates), '备注最多2000字');
  assert.equal(validateDecisions({ 'btn-a': { status: 'keep', targetId: null } }, candidates), '备注最多2000字');
});

test('finalMergeTarget：单跳、多跳、链尾、无决策、循环', () => {
  assert.equal(finalMergeTarget('btn-b', {}), null);
  const chain = { 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-c'), 'btn-c': dec('keep') };
  assert.equal(finalMergeTarget('btn-a', chain), 'btn-c');
  assert.equal(finalMergeTarget('btn-b', chain), 'btn-c');
  assert.equal(finalMergeTarget('btn-c', chain), null);
  const cycle = { 'btn-a': dec('merge', 'btn-b'), 'btn-b': dec('merge', 'btn-a') };
  assert.equal(finalMergeTarget('btn-a', cycle), null);
});
