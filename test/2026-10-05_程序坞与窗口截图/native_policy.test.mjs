import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const binary = fileURLToPath(new URL('./runtime/window-policy-tests', import.meta.url));
test('native window policy covers system carriers and preserves real occluders', { skip: process.platform !== 'darwin' }, () => {
  mkdirSync(fileURLToPath(new URL('./runtime/', import.meta.url)), { recursive: true });
  execFileSync('/usr/bin/swiftc', ['-parse-as-library', 'desktop-electron/native/window-discovery-policy.swift', fileURLToPath(new URL('./window_policy.swift', import.meta.url)), '-o', binary]);
  const result = execFileSync(binary, [], { encoding: 'utf8' });
  assert.equal(result.trim().split('\n').length, 10);
  assert.ok(result.includes('PASS: screenshot full-screen carrier'));
  assert.ok(result.includes('PASS: ordinary full-screen app is retained'));
});
