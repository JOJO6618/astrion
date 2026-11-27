import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { resolveDesktopBackend } from '../../desktop-electron/src/rust-backend.js';
import { resolveStartupBackend, writeStartupFailure } from '../../desktop-electron/src/startup-diagnostics.js';

test('resource validation failure is logged before the Electron ready lifecycle', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-diagnostics-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const logs = path.join(root, 'logs');
  const ready = () => { throw new Error('unexpected lifecycle'); };
  let file;
  assert.throws(() => {
    resolveStartupBackend(
      () => resolveDesktopBackend({ resourcesPath: root, platform: 'darwin', env: {} }),
      error => { file = writeStartupFailure(error, logs, root); },
    );
    ready();
  }, /Missing packaged Rust backend/);
  assert.equal(file, path.join(logs, 'astrion-desktop-startup.log'));
  assert.match(fs.readFileSync(file, 'utf8'), /Missing packaged Rust backend/);
  assert.deepEqual(fs.readdirSync(logs), ['astrion-desktop-startup.log']);
});

test('unavailable logs path falls back to a private directory without losing the error', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'astrion-diagnostics-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const invalid = path.join(root, 'file-not-directory');
  fs.writeFileSync(invalid, 'unchanged');
  const failure = new Error('failed fixture');
  const log = writeStartupFailure(failure, invalid, root);
  assert.match(fs.readFileSync(log, 'utf8'), /failed fixture/);
  assert.equal(fs.readFileSync(invalid, 'utf8'), 'unchanged');
  assert.equal(path.dirname(path.dirname(log)), root);
  if (process.platform !== 'win32') assert.equal(fs.statSync(path.dirname(log)).mode & 0o077, 0);
});

test('startup without a resolution error does not log', () => {
  const runtime = { binary: 'astrion-headless' };
  const selected = resolveStartupBackend(() => runtime, () => assert.fail('unexpected log'));
  assert.equal(selected, runtime);
});
