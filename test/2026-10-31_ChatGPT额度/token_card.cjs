#!/usr/bin/env node
/**
 * ChatgptQuotaCard 的 SSR 回归用例（真实 SFC 编译）。
 *
 * 由 token_interaction.cjs 统一入口调用；只做断言，不复制组件实现。
 */
'use strict';

const ts = require('typescript');
const { createSSRApp, h } = require('vue');
const { compileScript } = require('@vue/compiler-sfc');
const { renderToString } = require('@vue/server-renderer');
const { assert, readSfc, CARD_PATH, pass } = require('./token_fixture.cjs');

// ─────────────────────────────────────────────────────────────────────────────
// 三、真实 ChatgptQuotaCard SSR
// ─────────────────────────────────────────────────────────────────────────────

const cardDescriptor = readSfc(CARD_PATH);
const compiledCard = compileScript(cardDescriptor, {
  id: 'chatgpt-quota-card-test',
  inlineTemplate: true
});
const cardJs = ts.transpileModule(compiledCard.content, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.CommonJS
  }
}).outputText;
const cardModule = { exports: {} };
new Function('exports', 'require', 'module', cardJs)(
  cardModule.exports,
  (id) => require(id),
  cardModule
);
const ChatgptQuotaCard = cardModule.exports.default;
assert.ok(ChatgptQuotaCard, 'ChatgptQuotaCard 编译结果为空');

const DICT = {
  'sidebar.chatgptQuota': 'ChatGPT 订阅额度',
  'sidebar.chatgptAccount': '账号 {hash}',
  'sidebar.chatgptQuotaUnavailable': '订阅额度暂不可用',
  'sidebar.chatgptQuotaLoading': '正在查询订阅额度…',
  'sidebar.chatgptQuotaNotApplicable': '当前模型未使用 ChatGPT 订阅',
  'sidebar.resetAt': '重置 {time}',
  'sidebar.currentContext': '当前上下文',
  'input.usedPercent': '{n} 已用'
};

function translate(key, params) {
  const text = DICT[key] || key;
  return text.replace(/\{(\w+)\}/g, (_, name) =>
    params && params[name] != null ? String(params[name]) : `{${name}}`
  );
}

function baseProps(overrides = {}) {
  return {
    accountHash: 'acct-1',
    windows: [
      { key: 'primary_window', label: '5 小时窗口', percent: 42, resetText: '10/31 20:30' },
      { key: 'secondary_window', label: '每周窗口', percent: 81, resetText: '11/7 09:00' }
    ],
    loading: false,
    error: '',
    applicable: true,
    contextPercentLabel: '37%',
    contextText: '74k/200k',
    ...overrides
  };
}

async function renderCard(props) {
  const app = createSSRApp({ render: () => h(ChatgptQuotaCard, props) });
  app.config.globalProperties.$t = translate;
  return renderToString(app);
}

async function testsCard() {
  console.log('ChatgptQuotaCard（真实 SFC 编译 + SSR）');

  // 1. 两个窗口的已使用百分比 + 重置时间 + 账号
  {
    const html = await renderCard(baseProps());
    assert.ok(html.includes('5 小时窗口'), '缺少 5 小时窗口');
    assert.ok(html.includes('42% 已用'), '缺少 5 小时窗口已使用百分比');
    assert.ok(html.includes('重置 10/31 20:30'), '缺少 5 小时窗口重置时间');
    assert.ok(html.includes('每周窗口'), '缺少每周窗口');
    assert.ok(html.includes('81% 已用'), '缺少每周已使用百分比');
    assert.ok(html.includes('重置 11/7 09:00'), '缺少每周重置时间');
    assert.ok(html.includes('账号 acct-1'), '缺少账号哈希');
    assert.ok(!html.includes('title='), '账号不得使用原生 title tooltip');
    assert.ok(!html.includes('chatgpt-quota-card__track'), 'hover 小卡不应显示百分比条');
    assert.ok(!html.includes('width:42%'), 'hover 小卡不应输出条宽');
    assert.ok(html.includes('当前上下文'), '有额度时缺少原 Token 上下文');
    assert.ok(html.includes('37% 已用'), '原 Token 上下文已使用百分比缺失');
    assert.ok(html.includes('74k/200k'), '原 Token 上下文 token 文本缺失');
    pass('hover 卡：两个已使用百分比 + 重置时间，无百分比条，原 Token 信息');
  }

  // 2. 点击打开的宽弹窗（showBars=true）显示百分比条
  {
    const html = await renderCard(baseProps({ showBars: true }));
    const tracks = html.match(/chatgpt-quota-card__track/g) || [];
    assert.equal(tracks.length, 2, '点击打开的弹窗应有两个百分比条');
    assert.ok(html.includes('width:42%'), '5 小时条宽缺失');
    assert.ok(html.includes('width:81%'), '每周条宽缺失');
    assert.ok(html.includes('42% 已用'), '点击弹窗仍须有已使用语义');
    assert.ok(html.includes('重置 10/31 20:30'), '点击弹窗仍须有重置时间');
    assert.ok(html.includes('当前上下文'), '点击弹窗仍须保留原 Token 上下文');
    pass('点击态（showBars=true）显示两个百分比条并保留原 Token 信息');
  }

  // 3. loading 时原 Token 信息仍在
  {
    const html = await renderCard(baseProps({ windows: [], loading: true }));
    assert.ok(html.includes('正在查询订阅额度…'), 'loading 文案缺失');
    assert.ok(!html.includes('chatgpt-quota-card__track'), 'hover/loading 态不应出现百分比条');
    assert.ok(html.includes('当前上下文'), 'loading 时原 Token 上下文丢失');
    assert.ok(html.includes('37% 已用'), 'loading 时原 Token 百分比丢失');
    assert.ok(html.includes('74k/200k'), 'loading 时原 Token 文本丢失');
    pass('loading 状态保留原 Token 上下文');
  }

  // 4. error 时原 Token 信息仍在
  {
    const html = await renderCard(
      baseProps({ windows: [], error: '连接 ChatGPT 超时，请检查订阅代理连接。' })
    );
    assert.ok(html.includes('连接 ChatGPT 超时'), 'error 文案缺失');
    assert.ok(!html.includes('chatgpt-quota-card__track'), 'hover/error 态不应出现百分比条');
    assert.ok(html.includes('当前上下文'), 'error 时原 Token 上下文丢失');
    assert.ok(html.includes('37% 已用'), 'error 时原 Token 百分比丢失');
    assert.ok(html.includes('74k/200k'), 'error 时原 Token 文本丢失');
    pass('error 状态保留原 Token 上下文');
  }

  // 5. 无账号 windows 为空（非 loading/error）时原 Token 信息仍在
  {
    const html = await renderCard(baseProps({ windows: [] }));
    assert.ok(html.includes('订阅额度暂不可用'), '无账号时应显示额度不可用');
    assert.ok(html.includes('当前上下文'), '无账号时原 Token 上下文丢失');
    assert.ok(html.includes('37% 已用'), '无账号时原 Token 百分比丢失');
    assert.ok(html.includes('74k/200k'), '无账号时原 Token 文本丢失');
    pass('无账号 windows 为空时保留原 Token 上下文');
  }

  // 6. applicable=false（防御分支）也保留原 Token 信息
  {
    const html = await renderCard(baseProps({ applicable: false, windows: [] }));
    assert.ok(html.includes('当前模型未使用 ChatGPT 订阅'), 'not applicable 文案缺失');
    assert.ok(html.includes('当前上下文'), 'not applicable 时原 Token 上下文丢失');
    pass('not applicable 防御分支保留原 Token 上下文');
  }
}

module.exports = { testsCard };
