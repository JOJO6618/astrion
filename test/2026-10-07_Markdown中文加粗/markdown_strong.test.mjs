import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../../', import.meta.url));
const rendererPath = resolve(root, 'static/src/composables/useMarkdownRenderer.ts');

async function loadRenderer(useCjkStrong) {
  const source = readFileSync(rendererPath, 'utf8');
  const hook = '  .use(remarkCjkStrong)\n';
  assert.ok(source.includes(hook));
  const { outputFiles } = await build({
    bundle: true,
    write: false,
    platform: 'node',
    format: 'esm',
    alias: { '@': resolve(root, 'static/src') },
    stdin: {
      contents: useCjkStrong ? source : source.replace(hook, ''),
      loader: 'ts',
      resolveDir: dirname(rendererPath)
    }
  });
  return import(
    `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString('base64')}`
  );
}

const [{ renderMarkdownText, parseMarkdownSegments }, { renderMarkdownText: renderBaseline }] =
  await Promise.all([loadRenderer(true), loadRenderer(false)]);
const render = (source, streaming = false) => renderMarkdownText(source, streaming, true);

for (const [source, expected] of [
  ['- **第 2、3 项：**气泡', '<li><strong>第 2、3 项：</strong>气泡</li>'],
  ['正文**（重点）**继续', '<p>正文<strong>（重点）</strong>继续</p>'],
  ['**“重点”**内容', '<p><strong>“重点”</strong>内容</p>'],
  ['**重点。**内容', '<p><strong>重点。</strong>内容</p>'],
  ['**重点！**内容', '<p><strong>重点！</strong>内容</p>'],
  ['**重点…**内容', '<p><strong>重点…</strong>内容</p>'],
  ['正文**重点：**内容', '<p>正文<strong>重点：</strong>内容</p>'],
  ['**重点：**内容**补充：**内容', '<p><strong>重点：</strong>内容<strong>补充：</strong>内容</p>'],
  ['***重点：***内容', '<p><em><strong>重点：</strong></em>内容</p>']
]) {
  test(`CJK strong: ${source}`, () => assert.ok(render(source).includes(expected)));
}

test('all five labels in the reported message render as strong', () => {
  const labels = ['第 2、3 项', '第 1 项', '第 4 项', '第 5、6 项', '第 7 项'];
  const html = render(labels.map((label) => `- **${label}：**气泡`).join('\n'));
  for (const label of labels) assert.ok(html.includes(`<strong>${label}：</strong>`));
  assert.equal((html.match(/<li>/g) || []).length, 5);
  assert.ok(!html.includes('**'));
});

test('streaming strong closes only when its closing delimiter arrives', () => {
  assert.ok(!render('- **第 2、3 项：', true).includes('<strong>'));
  assert.ok(!render('- **第 2、3 项：*', true).includes('<strong>'));
  assert.ok(render('- **第 2、3 项：**气泡', true).includes('<strong>第 2、3 项：</strong>'));
});

for (const source of [
  '`**重点：**内容`',
  '```md\n**重点：**内容\n```',
  '    **重点：**内容',
  '\\*\\*重点：\\*\\*内容',
  '** 重点：**内容',
  '**重点： **内容',
  '**重点：\u00a0**内容',
  '**重点：**\u3000内容',
  '**重点：',
  '正文*（重点）*继续',
  '__重点：__内容',
  '**label:**text',
  '**重点：**text'
]) {
  test(`preserve literal/code or original boundary: ${JSON.stringify(source)}`, () => {
    if (source === '**重点：**\u3000内容') {
      assert.ok(render(source).includes('<strong>重点：</strong>'));
    } else {
      assert.ok(!render(source).includes('<strong>'));
    }
  });
}

test('ordinary strong, emphasis, nested emphasis and GFM strike remain supported', () => {
  assert.equal(render('**bold** and *italic*'), '<p><strong>bold</strong> and <em>italic</em></p>');
  assert.equal(render('**第 2、3 项**：气泡'), '<p><strong>第 2、3 项</strong>：气泡</p>');
  assert.equal(render('**重点：** 内容'), '<p><strong>重点：</strong> 内容</p>');
  assert.ok(render('**重点：*说明***内容').includes('<strong>重点：<em>说明</em></strong>'));
  assert.ok(render('~~旧内容~~ **重点：**内容').includes('<del>旧内容</del>'));
});

test('links and link destinations keep their existing behavior', () => {
  assert.ok(
    render('[**重点：**内容](https://example.com/path)').includes(
      '<a href="https://example.com/path"><strong>重点：</strong>内容</a>'
    )
  );
  for (const source of [
    '[链接](https://example.com/**重点：**内容)',
    'https://example.com/path（说明）',
    'https://zh.wikipedia.org/wiki/计算机'
  ]) {
    const html = render(source);
    assert.match(html, /<a href=/);
    assert.equal(html, renderBaseline(source, false, true));
  }
});

test('GFM tables, citations and math retain their own nodes', () => {
  const table = render('| 标题 | 内容 |\n| --- | --- |\n| **重点：**内容 | 气泡 |');
  assert.ok(table.includes('<table>'));
  assert.ok(table.includes('<strong>重点：</strong>内容'));
  assert.ok(render('**重点：**内容【cite:src_sample】').includes('md-citation-chip'));
  assert.ok(render('**公式：**内容 $x + 1$').includes('math-inline'));
});

test('normal text segments use the shared renderer while fenced code stays separate', () => {
  const segments = parseMarkdownSegments('- **重点：**内容\n\n```md\n**重点：**内容\n```');
  assert.equal(segments[0].type, 'text');
  assert.ok(render(segments[0].content).includes('<strong>重点：</strong>'));
  assert.equal(segments[1].type, 'code');
  assert.equal(segments[1].content.trim(), '**重点：**内容');
});
