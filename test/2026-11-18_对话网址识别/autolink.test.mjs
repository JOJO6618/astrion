import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { parseFragment } from 'parse5';

const root = fileURLToPath(new URL('../../', import.meta.url));
const { outputFiles } = await build({
  entryPoints: [resolve(root, 'static/src/composables/useMarkdownRenderer.ts')],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm',
  alias: { '@': resolve(root, 'static/src') }
});
const { parseMarkdownSegments, renderMarkdownText } = await import(
  `data:text/javascript;base64,${Buffer.from(outputFiles[0].contents).toString('base64')}`
);

function renderMessage(markdown, streaming = false) {
  return parseMarkdownSegments(markdown, streaming)
    .filter((segment) => segment.type === 'text')
    .map((segment) => renderMarkdownText(segment.content, streaming, true))
    .join('\n');
}

function readAnchors(html) {
  const anchors = [];
  const textContent = (node) => node.value ?? (node.childNodes || []).map(textContent).join('');
  function walk(node) {
    if (node.tagName === 'a') {
      const href = node.attrs.find((attribute) => attribute.name === 'href')?.value;
      if (/^https?:\/\//i.test(href || '')) anchors.push({ text: textContent(node), href });
    }
    node.childNodes?.forEach(walk);
  }
  walk(parseFragment(html));
  return anchors;
}

const addressLine =
  '- 下载页：https://astrion.cyjai.com/download.html（页面由 `download.js` 从 `downloads.json` 动态渲染，已确认清单里是 0.10.8）';
const manifestLine = '- 更新清单：https://astrion.cyjai.com/downloads/latest-mac.yml';
const downloadPage = 'https://astrion.cyjai.com/download.html';
const manifest = 'https://astrion.cyjai.com/downloads/latest-mac.yml';
const localhost = 'http://127.0.0.1:8321/index.html';
const multiLevel = 'https://example.com/root/a//b/some_file/c/d?mode=fast&next=/x/y#result';
const longPath = `https://example.com${'/long/path'.repeat(90)}`;
const unicodePath = 'https://example.com/wiki/計算機（消歧義）';
const encodedUnicodePath = `https://example.com/wiki/${encodeURIComponent('計算機（消歧義）')}`;
const sameText = (text, href = text) => ({ text, href });

function runTests() {
  test('0.10.8 发布对话原句的说明移出链接，保留行内代码与更新清单', () => {
    for (const streaming of [false, true]) {
      const html = renderMessage(`${addressLine}\n${manifestLine}`, streaming);
      assert.deepEqual(readAnchors(html), [sameText(downloadPage), sameText(manifest)]);
      assert.match(
        html,
        /<\/a>（页面由 <code>download\.js<\/code> 从 <code>downloads\.json<\/code> 动态渲染/
      );
    }
  });

  const apple = 'https://www.apple.com.cn/';
  const examples = [
    [
      '旧导航对话的加粗定界符不进入地址',
      `地址还是 **${localhost}**（预览面板里也有）。`,
      [sameText(localhost)]
    ],
    [
      '多网页操作对话中的 Apple 官网尾部斜杠完整保留',
      `换好了，标签 3 现在是 Apple 中国官网（${apple}），首页正常加载：主视觉是节日送礼专题，下面依次是 iPhone Duo、MacBook Pro、iPhone 18 Pro、Apple Watch Series 12、iPad、AirPods Pro 3、iPad mini 和 Trade In 换购。`,
      [sameText(apple)]
    ],
    ['多级路径、查询参数和锚点完整保留', `${multiLevel}，看这里。`, [sameText(multiLevel)]],
    ['长路径', longPath, [sameText(longPath)]],
    [
      '方括号查询参数',
      'https://example.com/list?ids[]=32&ids[]=33#items',
      [
        sameText(
          'https://example.com/list?ids[]=32&ids[]=33#items',
          'https://example.com/list?ids%5B%5D=32&ids%5B%5D=33#items'
        )
      ]
    ],
    [
      '裸 www 链接',
      '看这里 www.example.com/a_b/c（说明）',
      [sameText('www.example.com/a_b/c', 'http://www.example.com/a_b/c')]
    ],
    [
      '中文路径',
      '百科 https://zh.wikipedia.org/wiki/计算机，供查阅。',
      [
        sameText(
          'https://zh.wikipedia.org/wiki/计算机',
          `https://zh.wikipedia.org/wiki/${encodeURIComponent('计算机')}`
        )
      ]
    ],
    [
      '百分号编码的全角标点',
      'https://example.com/a/%EF%BC%88b%EF%BC%89',
      [sameText('https://example.com/a/%EF%BC%88b%EF%BC%89')]
    ],
    [
      '保留普通半角括号',
      'https://example.com/wiki/Earth_(planet)',
      [sameText('https://example.com/wiki/Earth_(planet)')]
    ],
    [
      '与目标同文的显式 Markdown 链接',
      `[${unicodePath}](${unicodePath})`,
      [sameText(unicodePath, encodedUnicodePath)]
    ],
    ['尖括号显式链接', `<${multiLevel}>`, [sameText(multiLevel)]],
    [
      '引用式链接',
      '[下载][doc]\n\n[doc]: https://example.com/a_b/c',
      [sameText('下载', 'https://example.com/a_b/c')]
    ],
    ['显式链接中的全角括号', `[词条](${unicodePath})`, [sameText('词条', encodedUnicodePath)]],
    [
      '两个裸网址紧挨中文逗号',
      'https://example.com/a，https://example.com/b',
      [sameText('https://example.com/a'), sameText('https://example.com/b')]
    ],
    [
      '第二个裸网址还在流式生成',
      'https://example.com/a，https://',
      [sameText('https://example.com/a')]
    ],
    ['裸网址紧跟破折号', 'https://example.com/a_b—查看详情', [sameText('https://example.com/a_b')]],
    [
      '无标点的中文后缀沿用合法路径解释',
      'https://example.com/download.html页面由',
      [
        sameText(
          'https://example.com/download.html页面由',
          `https://example.com/download.html${encodeURIComponent('页面由')}`
        )
      ]
    ],
    [
      '显式 HTML 锚点',
      '<a href="https://example.com/a_b">说明</a>',
      [sameText('说明', 'https://example.com/a_b')]
    ],
    [
      '行内和围栏代码中的网址保持纯文本',
      '`https://example.com/a`\n\n```txt\nhttps://example.com/b\n```',
      []
    ]
  ];

  for (const [name, markdown, expected] of examples) {
    test(name, () => {
      for (const streaming of [false, true]) {
        assert.deepEqual(readAnchors(renderMessage(markdown, streaming)), expected);
      }
    });
  }

  test('HTML 锚点与内部裸网址同时解析时，保留完整可点击地址', () => {
    const url = 'https://example.com/a_b';
    const markdown = `<a href="${url}">${url}</a>`;
    const visibleAnchors = readAnchors(renderMessage(markdown)).filter((anchor) => anchor.text);
    assert.deepEqual(visibleAnchors, [sameText(url)]);
  });
}

function reviewMessages(markdown) {
  const roles = [...markdown.matchAll(/^(user|assistant)：/gm)];
  assert.ok(roles.length, 'Expected a dialogue-mode Astrion conversation review');
  return roles.map(([prefix, role], index) => {
    const start = roles[index].index;
    const end = roles[index + 1]?.index ?? markdown.length;
    return { role, start, markdown: markdown.slice(start + prefix.length, end).trimEnd() };
  });
}

function reportReview(path) {
  const exported = readFileSync(path, 'utf8');
  const messages = [];
  for (const message of reviewMessages(exported)) {
    const lines = message.markdown.split(/\r?\n/);
    const firstLine = exported.slice(0, message.start).split('\n').length;
    const source = [];
    for (const [index, line] of lines.entries()) {
      if (!/(?:https?:\/\/|\bwww\.)/i.test(line)) continue;
      source.push({
        line: firstLine + index,
        before: lines[index - 1],
        original: line,
        after: lines[index + 1]
      });
    }
    if (!source.length) continue;
    const links = readAnchors(renderMessage(message.markdown));
    messages.push({
      role: message.role,
      source,
      links,
      streamingMatches:
        JSON.stringify(links) === JSON.stringify(readAnchors(renderMessage(message.markdown, true)))
    });
  }
  return { path, messages };
}

const [command, ...paths] = process.argv.slice(2);
if (command === '--report') {
  assert.ok(paths.length, 'Pass one or more dialogue-mode review files after --report');
  process.stdout.write(`${JSON.stringify(paths.map(reportReview), null, 2)}\n`);
} else {
  assert.equal(command, undefined, 'Use --report followed by dialogue-mode review paths');
  runTests();
}
