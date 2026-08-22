/**
 * Stylelint 配置 —— 颜色 Token 栏杆（规范见 doc/frontend/color_token_spec.md）
 *
 * 作用：拦住三类"绕过设计 Token"的写法，让规范从文字变成机制。
 *   规则1  color-no-hex                      → 禁止裸十六进制色号（如 #da7756）
 *   规则2  declaration-property-value-...     → 禁止 rgb()/hsl() 字面色、禁止 var(--x, #hex) 兜底
 *   规则3  media-feature-name-disallowed-list → 禁止组件内新增 prefers-color-scheme 主题分支
 *
 * 基线策略（baseline）：
 *   - 颜色属性必须走 var(--token)。
 *   - 下方 BASELINE_EXEMPT 列出的是"立规矩之前就存在违规"的存量文件，暂时豁免。
 *   - 新文件不在名单里 → 从第一行起就受三条规则约束。
 *   - 每当治理（清理）掉一个存量文件，就把它从 BASELINE_EXEMPT 删除，它从此受约束、不再回退。
 *   - 设计上永久豁免：_tokens.scss（Token 定义源）、_virtual-monitor.scss（canvas 动画资产）。
 */

// 颜色相关属性：这些属性的值必须用 token，不能写字面色
const COLOR_PROPS =
  '/^(color|fill|stroke|background|background-color|border|border-color|border-top-color|border-right-color|border-bottom-color|border-left-color|box-shadow|outline|outline-color|text-decoration-color|caret-color|column-rule-color|stop-color)$/';

// 立规矩之前就存在硬编码颜色的存量文件（baseline 豁免，治理后逐个移除）
const BASELINE_EXEMPT = [
  'static/src/admin/AdminDashboardApp.vue',
  'static/src/admin/ApiAdminApp.vue',
  'static/src/admin/CustomToolsApp.vue',
  'static/src/admin/CustomToolsGuideApp.vue',
  'static/src/admin/PolicyApp.vue',
];

// 三条颜色规则（供默认与 override 复用）
const COLOR_RULES = {
  // 规则1：禁止裸 hex
  'color-no-hex': [true, { message: '禁止裸 hex 色号，请使用设计 Token：var(--accent) 等（见 color_token_spec.md）' }],
  // 规则2：禁止 rgb()/hsl() 字面色 + 禁止 var() 兜底色
  'declaration-property-value-disallowed-list': [
    {
      [COLOR_PROPS]: ['/rgba?\\(/', '/hsla?\\(/', '/var\\(\\s*--[^,)]+,/'],
    },
    {
      message:
        '颜色属性禁止 rgb()/hsl() 字面色或 var(--x, #hex) 兜底，请直接用 var(--token)（见 color_token_spec.md）',
    },
  ],
  // 规则3：禁止组件内手写 prefers-color-scheme 主题分支（主题切换只在 _tokens.scss 的 [data-theme] 完成）
  'media-feature-name-disallowed-list': [
    ['prefers-color-scheme'],
    { message: '禁止在组件内用 prefers-color-scheme 写主题分支，颜色随主题变化应通过 Token 自动完成' },
  ],
};

// 关闭三条规则（给存量豁免文件用）
const COLOR_RULES_OFF = {
  'color-no-hex': null,
  'declaration-property-value-disallowed-list': null,
  'media-feature-name-disallowed-list': null,
};

module.exports = {
  // 设计上永久豁免：Token 定义源 + canvas 动画资产
  ignoreFiles: [
    'static/dist/**',
    'static/src/styles/base/_tokens.scss',
    'static/src/styles/components/chat/_virtual-monitor.scss',
  ],
  // 顶层 rules 必填（stylelint 16 要求）。具体规则按文件类型在 overrides 中施加。
  rules: {},
  overrides: [
    {
      files: ['static/src/**/*.scss'],
      customSyntax: 'postcss-scss',
      rules: COLOR_RULES,
    },
    {
      files: ['static/src/**/*.vue'],
      customSyntax: 'postcss-html',
      rules: COLOR_RULES,
    },
    // baseline：存量文件暂时关闭三条规则（治理后从此数组移除该文件即可恢复约束）
    {
      files: BASELINE_EXEMPT,
      rules: COLOR_RULES_OFF,
    },
  ],
};
