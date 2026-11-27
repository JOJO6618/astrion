import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { parse } from '@vue/compiler-sfc';
import { transformSync } from 'esbuild';
import { computed, ref } from 'vue';

const root = fileURLToPath(new URL('../../', import.meta.url));
const quick = 'static/src/components/chat/quickdock/RunnerDetailPanel.vue';
const modal = 'static/src/components/overlay/SubAgentActivityDialog.vue';

function evaluate(source, bindings) {
  const js = transformSync(source, { loader: 'ts' }).code;
  return Function(...Object.keys(bindings), js)(...Object.values(bindings));
}

const sharedSources = readFileSync(
  resolve(root, 'static/src/components/chat/quickdock/subAgentToolText.ts'),
  'utf8'
)
  .replace(/^import[^\n]+\n/m, '')
  .replace(/^export /gm, '');
const shared = evaluate(
  sharedSources +
    '\nreturn { buildSubAgentToolParts, buildSubAgentToolText, isSubAgentToolTerminal, normalizeSubAgentToolStatus, subAgentToolStatusLabel };',
  { t: (key) => (key === 'overlay.stateCancelled' ? 'Cancelled' : key) }
);

function panelTimeline(component, rows) {
  // Run each production component's computed expression, not a reimplementation.
  // Vue reactivity is real; only unrelated store/locale state is supplied.
  const source = parse(readFileSync(resolve(root, component), 'utf8')).descriptor.scriptSetup
    .content;
  const start = source.indexOf('const timelineItems = computed');
  const end = source.indexOf('const outputLines =', start);
  assert.notEqual(start, -1);
  return evaluate(
    source.slice(start, end < 0 ? undefined : end) + '\nreturn timelineItems.value;',
    {
      computed,
      activityEntries: ref(rows.map((row, idx) => ({ ...row, activityKey: String(idx) }))),
      currentLocale: ref('en-US'),
      ...shared
    }
  );
}

function tool(id, name, status, payload = {}) {
  return { type: 'progress', id, tool: name, status, ...payload };
}

function output(content) {
  return { type: 'progress', subtype: 'output', content, is_final: true };
}

function detail(item) {
  return item.detail ?? item.text;
}

test('both production timelines merge aliases and keep final output after terminal tools', () => {
  for (const component of [modal, quick]) {
    for (const [raw, state] of [
      ['done', 'completed'],
      ['SUCCESS', 'completed'],
      ['error', 'failed'],
      ['timeout', 'failed'],
      ['cancelled', 'cancelled'],
      ['canceled', 'cancelled']
    ]) {
      const rows = panelTimeline(component, [
        tool('read', 'read_file', 'calling'),
        tool('read', 'read_file', 'running', {
          args: { path: 'fixture.txt', start_line: 3, end_line: 5 }
        }),
        tool('read', 'read_file', raw),
        output('Final text.')
      ]);
      assert.deepEqual(
        rows.map(({ kind }) => kind),
        ['tool', 'output']
      );
      assert.equal(rows[0].state, state);
      assert.match(detail(rows[0]), /fixture\.txt L3-5/);
      if (state === 'cancelled')
        assert.equal(rows[0].stateLabel ?? shared.subAgentToolStatusLabel(state), 'Cancelled');
    }
  }
  const template = parse(readFileSync(resolve(root, quick), 'utf8')).descriptor.template.content;
  assert.match(template, /item\.state === 'completed' \? '✓' : item\.state === 'cancelled'/);
});

test('both production timelines display supported historical parameters but not arbitrary MCP payloads', () => {
  const fixture = [
    tool('cmd', 'run_command', 'done', { arguments: { command: 'pwd' } }),
    tool('search', 'web_search', 'done', { arguments: { query: 'fixture query' } }),
    tool('mcp', 'mcp_fixture', 'done', { arguments: { token: 'not-visible' } })
  ];
  for (const component of [modal, quick]) {
    const rows = panelTimeline(component, fixture);
    assert.match(detail(rows[0]), /pwd/);
    assert.match(detail(rows[1]), /fixture query/);
    assert.doesNotMatch(JSON.stringify(rows), /not-visible/);
  }
});

const captured = process.env.ASTRION_ACTIVITY_FIXTURE;
test(
  'real authenticated activity snapshot renders read parameters, cancellation and final explanation',
  { skip: !captured },
  () => {
    const rows = JSON.parse(readFileSync(captured, 'utf8'));
    for (const component of [modal, quick]) {
      const rendered = panelTimeline(component, rows);
      const read = rendered.find((entry) => entry.kind === 'tool' && entry.key === 'read');
      const stop = rendered.find((entry) => entry.kind === 'tool' && entry.key === 'blocking');
      const final = rendered.find(
        (entry) => entry.kind === 'output' && entry.content.includes('本轮已停止')
      );
      assert.equal(read?.state, 'completed');
      assert.match(detail(read), /fixture\.txt L1-2/);
      assert.equal(stop?.state, 'cancelled');
      assert.ok(
        final && rendered.indexOf(stop) < rendered.indexOf(final),
        JSON.stringify(rendered)
      );
    }
  }
);
