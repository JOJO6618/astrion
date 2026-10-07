import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(resolve(root, 'package.json'));

// Load the real TS renderer without a browser or a generated build. Translation
// only supplies code-copy labels; Markdown and all parser plugins stay intact.
function moduleUrl(path, replacements = []) {
  let source = readFileSync(resolve(root, path), 'utf8');
  for (const [pattern, replacement] of replacements) {
    source = source.replace(pattern, replacement);
  }
  const javascript = ts
    .transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 }
    })
    .outputText.replace(/from ['"]([^.'"][^'"]*)['"]/g, (match, name) => {
      if (name.startsWith('data:')) return match;
      return `from ${JSON.stringify(pathToFileURL(require.resolve(name)).href)}`;
    });
  return `data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`;
}

const extensionUrl = moduleUrl('static/src/utils/remarkCjkStrong.ts');
const rendererImports = [
  [/import \{ t \} from ['"]@\/locales['"];?/, 'const t = (key) => key;'],
  [
    /import remarkCjkStrong from ['"]@\/utils\/remarkCjkStrong['"];?/,
    `import remarkCjkStrong from ${JSON.stringify(extensionUrl)};`
  ]
];
const rendererPath = 'static/src/composables/useMarkdownRenderer.ts';
const rendererUrl = moduleUrl(rendererPath, rendererImports);
const baselineUrl = moduleUrl(rendererPath, [...rendererImports, [/\.use\(remarkCjkStrong\)/, '']]);
const { renderMarkdownText, parseMarkdownSegments } = await import(rendererUrl);
const { renderMarkdownText: renderBaseline } = await import(baselineUrl);
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
