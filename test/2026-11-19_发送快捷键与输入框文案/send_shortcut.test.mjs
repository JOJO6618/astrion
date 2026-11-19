import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import {
  normalizeSendShortcut,
  resolveEnterIntent
} from '../../static/src/utils/sendShortcut.ts';
import {
  COMPOSER_PLACEHOLDER_KEYS,
  pickComposerPlaceholderKey
} from '../../static/src/utils/composerPlaceholders.ts';

const key = (overrides = {}) => ({
  key: 'Enter',
  isComposing: false,
  keyCode: 13,
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...overrides
});

test('非法或缺失设置回落 mod_enter', () => {
  assert.equal(normalizeSendShortcut('enter'), 'enter');
  assert.equal(normalizeSendShortcut('mod_enter'), 'mod_enter');
  assert.equal(normalizeSendShortcut(undefined), 'mod_enter');
  assert.equal(normalizeSendShortcut('shift_enter'), 'mod_enter');
});

test('mod_enter：Ctrl/⌘+Enter 发送，Enter 与 Shift+Enter 换行', () => {
  for (const coarse of [false, true]) {
    assert.equal(resolveEnterIntent(key({ ctrlKey: true }), 'mod_enter', coarse), 'send');
    assert.equal(resolveEnterIntent(key({ metaKey: true }), 'mod_enter', coarse), 'send');
    assert.equal(resolveEnterIntent(key(), 'mod_enter', coarse), 'newline');
    assert.equal(resolveEnterIntent(key({ shiftKey: true }), 'mod_enter', coarse), 'newline');
  }
});

test('enter：Enter 发送，Shift/Ctrl/⌘+Enter 换行', () => {
  assert.equal(resolveEnterIntent(key(), 'enter', false), 'send');
  assert.equal(resolveEnterIntent(key({ shiftKey: true }), 'enter', false), 'newline');
  assert.equal(resolveEnterIntent(key({ ctrlKey: true }), 'enter', false), 'newline');
  assert.equal(resolveEnterIntent(key({ metaKey: true }), 'enter', false), 'newline');
});

test('enter 模式在触屏设备上不生效：回车换行，外接键盘修饰键仍可发送', () => {
  assert.equal(resolveEnterIntent(key(), 'enter', true), 'newline');
  assert.equal(resolveEnterIntent(key({ ctrlKey: true }), 'enter', true), 'send');
});

test('输入法组字、Alt、非 Enter 不处理', () => {
  for (const mode of ['enter', 'mod_enter']) {
    assert.equal(resolveEnterIntent(key({ isComposing: true }), mode, false), null);
    assert.equal(resolveEnterIntent(key({ keyCode: 229 }), mode, false), null);
    assert.equal(resolveEnterIntent(key({ altKey: true }), mode, false), null);
    assert.equal(resolveEnterIntent(key({ key: 'a' }), mode, false), null);
  }
});

test('占位文案：16 条、中英 key 齐全、换句不重复上一条', () => {
  assert.equal(COMPOSER_PLACEHOLDER_KEYS.length, 16);
  for (const locale of ['zh-CN', 'en-US']) {
    const source = readFileSync(
      new URL(`../../static/src/locales/${locale}/input.ts`, import.meta.url),
      'utf8'
    );
    for (const fullKey of COMPOSER_PLACEHOLDER_KEYS) {
      const name = fullKey.replace('input.', '');
      assert.match(source, new RegExp(`\\n  ${name}: `), `${locale} 缺少 ${name}`);
    }
  }
  for (const previous of COMPOSER_PLACEHOLDER_KEYS) {
    for (let i = 0; i < 20; i++) {
      assert.notEqual(pickComposerPlaceholderKey(previous), previous);
    }
  }
});
