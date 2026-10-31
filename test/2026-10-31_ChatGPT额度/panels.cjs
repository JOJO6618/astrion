/**
 * ChatGPT 订阅额度面板（TokenDrawer / ChatgptQuotaPanel）离线回归。
 *
 * 目标：用真实 SFC + 真实 useChatgptQuota 渲染，验证模板确实解包 composable 返回的
 * computed ref（回归点：写成 quota.windows.length 时永不渲染窗口、quota.applicable 恒为真），
 * 以及宿主机/docker 布局、账号选择规则、账号哈希、已用百分比与重置时间、loading/error 状态。
 *
 * 账号选择规则（2026-10-31 用户纠正后）：
 * - 显式选中 ChatGPT 订阅模型（isChatgptModel 且 providerId）→ 查询该模型所属账号；
 * - 普通模型 / 只带 providerId 的提供商同步模型 → 回退首个已登录订阅账号
 *   （store.defaultAccountId），不按陌生 providerId 猜 OAuth；
 * - 没有任何订阅账号 → 不可用；非管理员 → applicable=false，也不发查询。
 *
 * 只 stub store 与 locales（不 stub composable），不访问网络、不读取任何真实凭据；
 * 定时器由假实现接管，不产生后台轮询。
 *
 * 运行：node test/2026-10-31_ChatGPT额度/panels.cjs
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const vue = require('../../node_modules/vue');
const { renderToString } = require('../../node_modules/vue/server-renderer');
const ts = require('../../node_modules/typescript');
const compiler = require('../../node_modules/@vue/compiler-sfc');
const { compile } = require('../2026-10-07_运行对话快照/ts_fixture.cjs');

const root = path.resolve(__dirname, '../..');
// 插值用「key(值|值)」形式：SSR 会转义插值里的双引号，避免断言依赖转义细节。
const locale = {
  t: (key, args) => (args ? `${key}(${Object.values(args).join('|')})` : key),
  currentLocale: vue.ref('zh-CN')
};

// 每个用例渲染前覆盖；store 形状与新的 @/stores/chatgptQuota 一致（无账号列表字段）。
const env = {
  settings: { isAdmin: true, isHostMode: true },
  modelStore: { currentModelKey: '', allModels: [], currentModel: null },
  store: null
};

const makeStore = ({
  entries = {},
  defaultAccountId = '',
  accountsLoading = false,
  accountsError = ''
} = {}) => ({
  entries,
  defaultAccountId,
  accountsLoading,
  accountsError,
  // 只记录，不做真实请求：SSR 单次渲染下账号列表视为已加载缓存。
  quotaCalls: [],
  accountsCalls: [],
  fetchAccounts(force = false) {
    this.accountsCalls.push(!!force);
    return Promise.resolve();
  },
  fetchQuota(accountId, force = false) {
    this.quotaCalls.push([accountId, !!force]);
    return Promise.resolve();
  }
});

const setCase = ({
  isAdmin = true,
  isHostMode = true,
  currentModelKey = '',
  allModels = [],
  currentModel = null,
  store
}) => {
  env.settings = { isAdmin, isHostMode };
  env.modelStore = { currentModelKey, allModels, currentModel };
  env.store = store || makeStore();
  return env.store;
};

// 真实 composable：用共享 fixture 编译（只替换 store 与 locales）。
const composable = compile(
  'static/src/components/token/useChatgptQuota.ts',
  {
    vue,
    '@/locales': locale,
    '@/stores/settings': { useSettingsStore: () => env.settings },
    '@/stores/chatgptQuota': { useChatgptQuotaStore: () => env.store }
  },
  { setInterval: () => 0, clearInterval: () => {} }
);

// 真实 SFC：解析 + 内联模板编译，依赖通过 require 映射注入。
const loadSfc = (file) => {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const { descriptor, errors } = compiler.parse(source, { filename: file });
  assert.deepEqual(errors, [], `${file} should parse without SFC errors`);
  const script = compiler.compileScript(descriptor, { id: file, inlineTemplate: true });
  const code = ts.transpileModule(script.content, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(
    code,
    {
      module,
      exports: module.exports,
      require(key) {
        if (key === 'vue') return vue;
        if (key === '@/locales') return locale;
        if (key === '@/stores/settings') return { useSettingsStore: () => env.settings };
        if (key === '@/stores/model') return { useModelStore: () => env.modelStore };
        if (key === '@/stores/chatgptQuota') return { useChatgptQuotaStore: () => env.store };
        if (key === './useChatgptQuota') return composable;
        if (key === '@/components/common/CloseButton.vue')
          return { default: vue.defineComponent({ render: () => null }) };
        if (key === './CostSummary.vue')
          return { default: vue.defineComponent({ render: () => null }) };
        if (key === './ChatgptQuotaPanel.vue') return { default: panel };
        throw new Error(`Unmocked dependency ${file}: ${key}`);
      },
      setInterval: () => 0,
      clearInterval: () => {},
      console
    },
    { filename: file }
  );
  return module.exports.default;
};

const panel = loadSfc('static/src/components/token/ChatgptQuotaPanel.vue');
const drawer = loadSfc('static/src/components/token/TokenDrawer.vue');

const render = (component, props) => {
  const app = vue.createSSRApp(component, props);
  app.config.globalProperties.$t = locale.t;
  return renderToString(app);
};

const drawerProps = {
  visible: true,
  collapsed: false,
  conversationId: 'conv-1',
  currentConversationTokens: { cumulative_input_tokens: 1200 },
  currentContextTokens: 300,
  containerStatus: { mode: 'docker' },
  containerNetRate: { down_bps: null, up_bps: null },
  projectStorage: { used_bytes: 0 },
  usageQuota: { applicable: true, fast: { count: 1, limit: 2 }, thinking: {}, search: {} },
  formatTokenCount: String,
  formatPercentage: String,
  formatBytes: String,
  formatQuotaValue: () => 'quota',
  formatResetTime: String,
  formatRate: String
};

const quotaEntry = (hash, primary, secondary, resetAt = 1893456000) => ({
  usage: {
    rate_limit: {
      primary_window: { used_percent: primary, reset_at: resetAt },
      secondary_window: { used_percent: secondary, reset_at: resetAt + 86400 }
    }
  },
  accountHash: hash,
  loading: false,
  error: '',
  updatedAt: Date.now()
});

// 两条账号的真实缓存：显式账号 acc1（a1b2c3 / 42% / 81%）与首个账号（first01 / 12% / 34%）。
const accountEntries = {
  acc1: quotaEntry('a1b2c3', 42.4, 81),
  'account-first': quotaEntry('first01', 12.4, 34)
};

const oauthModel = { key: 'gpt-5-codex', isChatgptModel: true, providerId: 'acc1' };
// 提供商同步模型：也带 providerId，但不是 ChatGPT 订阅账号（陌生 key 不得当 OAuth）。
const syncedModel = { key: 'synced-model', isChatgptModel: false, providerId: 'provider-x' };
const plainModel = { key: 'plain-model', isChatgptModel: false };

const firstAccountStore = () =>
  makeStore({ entries: accountEntries, defaultAccountId: 'account-first' });

async function accounts() {
  // 1) 显式选中 ChatGPT 订阅模型：跟随该模型账号，而不是首个账号
  let store = setCase({
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    currentModel: plainModel,
    store: firstAccountStore()
  });
  let html = await render(panel, { active: true });
  assert.ok(
    !html.includes('sidebar.chatgptQuotaUnavailable'),
    'loaded windows must not fall back to unavailable'
  );
  assert.ok(
    html.includes('personalization.codexUsagePrimaryWindow'),
    'primary window label rendered'
  );
  assert.ok(
    html.includes('personalization.codexUsageSecondaryWindow'),
    'secondary window label rendered'
  );
  assert.ok(
    html.includes('input.usedPercent(42%)'),
    'primary percent rounded from real composable'
  );
  assert.ok(html.includes('input.usedPercent(81%)'), 'secondary percent rendered');
  assert.ok(html.includes('sidebar.chatgptAccount(a1b2c3)'), 'explicit model account hash');
  assert.ok(!html.includes('first01'), 'explicit model must not fall back to the first account');
  assert.ok(html.includes('sidebar.resetAt('), 'reset time uses sidebar.resetAt');
  assert.ok(!html.includes('sidebar.resetAt(—)'), 'reset_at converts to a real local datetime');
  assert.ok(/sidebar\.resetAt\([^)]*\d[^)]*\)/.test(html), 'reset time contains a date/time value');
  assert.deepEqual(store.quotaCalls, [['acc1', false]], 'queries the selected model account only');
  // 显式 OAuth 模型直接查目标账号，不需要拉账号列表（fetchAccounts 只在回退时调用）
  assert.deepEqual(store.accountsCalls, [], 'explicit OAuth account needs no account list');
  // 大面板给两个窗口都画额度条：track 两份，宽度等于已用百分比
  assert.equal(
    (html.match(/chatgpt-quota__track/g) || []).length,
    2,
    'each window renders a quota bar'
  );
  assert.ok(html.includes('width:42%'), 'primary quota bar width equals used percent');
  assert.ok(html.includes('width:81%'), 'secondary quota bar width equals used percent');

  // 2) 普通模型（未使用 ChatGPT）：回退首个已登录订阅账号
  store = setCase({
    currentModelKey: 'plain-model',
    allModels: [plainModel, oauthModel],
    currentModel: oauthModel, // fallback 陷阱：必须按 currentModelKey 精确查找
    store: firstAccountStore()
  });
  html = await render(panel, { active: true });
  assert.ok(
    html.includes('sidebar.chatgptAccount(first01)'),
    'plain model falls back to the first account'
  );
  assert.ok(html.includes('input.usedPercent(12%)'), 'fallback account primary percent');
  assert.ok(html.includes('input.usedPercent(34%)'), 'fallback account secondary percent');
  assert.ok(!html.includes('a1b2c3'), 'plain model never shows the OAuth model account');
  assert.deepEqual(store.quotaCalls, [['account-first', false]], 'fallback account is queried');
  assert.ok(store.accountsCalls.length >= 1, 'fallback drives account-list loading');
  assert.ok(html.includes('width:12%'), 'fallback quota bar width equals used percent');
  assert.ok(html.includes('width:34%'), 'fallback second quota bar width');

  // 3) 提供商同步模型带陌生 providerId：不得当成 OAuth 账号
  store = setCase({
    currentModelKey: 'synced-model',
    allModels: [syncedModel],
    store: firstAccountStore()
  });
  html = await render(panel, { active: true });
  assert.ok(
    html.includes('sidebar.chatgptAccount(first01)'),
    'unknown providerId falls back to the first account'
  );
  assert.ok(
    !JSON.stringify(store.quotaCalls).includes('provider-x'),
    'never queries a provider id as a quota account'
  );
  assert.deepEqual(
    store.quotaCalls,
    [['account-first', false]],
    'only the fallback account is queried'
  );

  // 4) 没有任何订阅账号：不可用，且不查询
  store = setCase({ currentModelKey: 'plain-model', allModels: [plainModel], store: makeStore() });
  html = await render(panel, { active: true });
  assert.ok(html.includes('sidebar.chatgptQuotaUnavailable'), 'no account shows unavailable');
  assert.ok(!html.includes('input.usedPercent'), 'no account shows no quota numbers');
  assert.deepEqual(store.quotaCalls, [], 'no account triggers no quota request');

  // 5) loading / error
  setCase({
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: makeStore({
      entries: { acc1: { ...accountEntries.acc1, usage: null, loading: true } },
      defaultAccountId: 'account-first'
    })
  });
  assert.ok(
    (await render(panel, { active: true })).includes('sidebar.chatgptQuotaLoading'),
    'loading state'
  );

  setCase({
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: makeStore({
      entries: { acc1: { ...accountEntries.acc1, usage: null, loading: false, error: 'boom' } },
      defaultAccountId: 'account-first'
    })
  });
  assert.ok(
    (await render(panel, { active: true })).includes('sidebar.chatgptQuotaUnavailable'),
    'error state'
  );

  // 6) 非管理员：applicable=false，不发查询
  store = setCase({
    isAdmin: false,
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: firstAccountStore()
  });
  html = await render(panel, { active: true });
  assert.ok(html.includes('sidebar.chatgptQuotaUnavailable'), 'non-admin sees unavailable');
  assert.ok(!html.includes('input.usedPercent'), 'non-admin sees no quota numbers');
  assert.ok(!html.includes('first01'), 'non-admin sees no account hash');
  assert.deepEqual(store.quotaCalls, [], 'non-admin performs no quota request');

  // 7) 抽屉收起（active=false）不查询
  store = setCase({
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: firstAccountStore()
  });
  await render(drawer, { ...drawerProps, collapsed: true });
  assert.deepEqual(store.quotaCalls, [], 'collapsed drawer does not query quota');
}

async function layout() {
  // 宿主机：Token 统计在上、ChatGPT 额度在下，性能/应用额度/网络资源整块移除
  let store = setCase({
    isHostMode: true,
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: firstAccountStore()
  });
  const hostHtml = await render(drawer, drawerProps);
  assert.ok(hostHtml.includes('sidebar.tokenStats'), 'host keeps token stats');
  assert.ok(hostHtml.includes('sidebar.chatgptQuota'), 'host adds ChatGPT quota');
  assert.ok(
    hostHtml.indexOf('sidebar.tokenStats') < hostHtml.indexOf('sidebar.chatgptQuota'),
    'host stacks token stats above the ChatGPT quota'
  );
  for (const key of ['sidebar.performanceStats', 'sidebar.quotaStats', 'sidebar.resourceStats']) {
    assert.ok(!hostHtml.includes(key), `host drops ${key}`);
  }
  assert.ok(hostHtml.includes('input.usedPercent(42%)'), 'host drawer renders real quota numbers');
  assert.deepEqual(store.quotaCalls, [['acc1', false]], 'host drawer queries the selected account');

  // docker：布局与改动前一致（四格都在），不出现 ChatGPT 额度
  store = setCase({
    isHostMode: false,
    currentModelKey: 'gpt-5-codex',
    allModels: [oauthModel],
    store: firstAccountStore()
  });
  const dockerHtml = await render(drawer, drawerProps);
  for (const key of [
    'sidebar.tokenStats',
    'sidebar.performanceStats',
    'sidebar.quotaStats',
    'sidebar.resourceStats'
  ]) {
    assert.ok(dockerHtml.includes(key), `docker keeps ${key}`);
  }
  assert.ok(
    !dockerHtml.includes('sidebar.chatgptQuota'),
    'docker layout unchanged (no ChatGPT quota)'
  );
  assert.deepEqual(store.quotaCalls, [], 'docker mode never queries ChatGPT quota');
}

(async () => {
  await accounts();
  await layout();
  console.log(
    'Passed ChatGPT quota panel: account selection (explicit model / first-account fallback / no account), host & docker layout, used percentages, reset time, loading/error states.'
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
