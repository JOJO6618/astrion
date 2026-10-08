import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createUpdaterLogger } from '../../desktop-electron/src/updater-log.js';

function setup(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-update-log-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const app = { getPath: () => root, getVersion: () => 'test-version' };
  return { root, app, log: createUpdaterLogger(app), file: path.join(root, 'logs', 'desktop-update.log') };
}

test('更新关键事件以可解析 JSON 行持久保存，包含版本和进程', (t) => {
  const f = setup(t);
  f.log('native-preparation-start');
  f.log('native-before-quit-for-update', { targetVersion: 'next-version' });
  const lines = fs.readFileSync(f.file, 'utf8').trim().split('\n').map(JSON.parse);
  assert.equal(lines.length, 2);
  assert.deepEqual(lines.map((row) => row.event), ['native-preparation-start', 'native-before-quit-for-update']);
  assert.equal(lines[0].version, 'test-version');
  assert.equal(lines[0].pid, process.pid);
  assert.equal(Number.isNaN(Date.parse(lines[0].time)), false);
  assert.equal(lines[1].targetVersion, 'next-version');
});

test('超出 1MB 后轮转并仅保留三份备份', (t) => {
  const f = setup(t);
  f.log('initial');
  for (let index = 0; index < 5; index += 1) {
    fs.writeFileSync(f.file, 'x'.repeat(1024 * 1024));
    f.log('rotated', { index });
  }
  assert.equal(JSON.parse(fs.readFileSync(f.file, 'utf8')).index, 4);
  assert.equal(fs.existsSync(`${f.file}.1`), true);
  assert.equal(fs.existsSync(`${f.file}.2`), true);
  assert.equal(fs.existsSync(`${f.file}.3`), true);
  assert.equal(fs.existsSync(`${f.file}.4`), false);
});

test('日志路径不可写时不向安装调用者抛错', (t) => {
  const f = setup(t);
  fs.writeFileSync(path.join(f.root, 'logs'), 'not a directory');
  const warnings = [];
  t.mock.method(console, 'warn', (message) => warnings.push(message));
  assert.doesNotThrow(() => f.log('native-preparation-start'));
  assert.equal(warnings.length, 1);
  assert.equal(typeof warnings[0], 'string');
  assert.match(warnings[0], /native-preparation-start/);
});
