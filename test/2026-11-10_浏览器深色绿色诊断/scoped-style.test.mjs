import assert from 'node:assert/strict';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { compileStyle, parse } from '@vue/compiler-sfc';
import postcss from 'postcss';

const filename = fileURLToPath(new URL('../../static/src/components/input/InputComposer.vue', import.meta.url));
const { descriptor, errors } = parse(fs.readFileSync(filename, 'utf8'), { filename });
assert.deepEqual(errors, []);
const styles = descriptor.styles.filter(style => style.scoped).map(style => style.content).join('\n');
const scopeId = 'data-v-background-regression';

function assertBannerScope(source, banner) {
  const compiled = compileStyle({ source, filename, id: scopeId, scoped: true });
  assert.deepEqual(compiled.errors, []);
  const rules = [];
  postcss.parse(compiled.code).walkRules(rule => rules.push(rule));
  assert.equal(rules.length, 1);
  assert.equal(rules[0].selectors.length, 2);
  for (const [index, selector] of rules[0].selectors.entries()) {
    const root = index === 0 ? ':root' : 'body';
    assert.equal(selector, `${root}[data-theme='dark'] .${banner}[${scopeId}]`);
  }
  assert.ok(rules[0].nodes.some(node => node.prop === 'background'));
}

for (const banner of ['goal-mode-banner', 'goal-mode-banner--running', 'goal-mode-banner--done']) {
  test(`${banner}: dark background remains scoped to the banner after Vue compilation`, () => {
    const escaped = banner.replaceAll('-', '\\-');
    const rule = styles.match(new RegExp(`:root\\[data-theme='dark'\\] \\.${escaped},\\s*body\\[data-theme='dark'\\] \\.${escaped}\\s*\\{[^}]*\\}`));
    assert.ok(rule, `Missing dark banner rule: ${banner}`);
    assertBannerScope(rule[0], banner);
  });
}

test('regression guard rejects the original :global selector that paints the page root', () => {
  const source = ":global(:root[data-theme='dark']) .goal-mode-banner--done,\n:global(body[data-theme='dark']) .goal-mode-banner--done { background: var(--state-success); }";
  assert.throws(() => assertBannerScope(source, 'goal-mode-banner--done'), assert.AssertionError);
});
