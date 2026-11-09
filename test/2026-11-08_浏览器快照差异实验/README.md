# 浏览器快照差异实验（2026-11-08）

验证内置浏览器工具「动作后只返回快照差异」的设计：增量快照大小、ref 稳定性、页面稳定判定、后处理（折叠未变化节点、列出移除节点、忽略纯焦点变化）。

这是设计验证，不是生产实现：使用 Playwright 自带 Chromium 和 `page.ariaSnapshot({ mode: 'ai', _track })`（playwright-core 1.61.1 的内部增量快照参数），没有接入 Electron 的 `webContents.debugger`。

## 运行

```bash
cd test/2026-11-08_浏览器快照差异实验
node run.mjs            # 含真实网站（需要外网）
node run.mjs --no-real  # 只跑本地夹具
```

依赖仓库根 `node_modules/playwright-core` 与本机 Playwright Chromium。结果写到 `_experiments/browser-snapshot-diff-20261108/<时间戳>/results.json`，各场景的原始增量、后处理结果与完整快照在同目录 `samples/`。

真实网站交互（需要外网，选择器失效时该场景记录 error 并继续）：

```bash
node real.mjs                         # 全部
node real.mjs --only wiki_search_type,hn_collapse
```

结果写到 `_experiments/browser-snapshot-diff-20261108/real-<时间戳>/`。`lib.mjs` 是两者共用的稳定判定与后处理（`run.mjs` 为第一轮脚本，内含同逻辑的早期版本，移除列表不限条数）。

## 内容

- `fixture/app.html`：16 个交互场景（无效点击、文字变化、菜单开关、联想输入、模态框、延迟提示、表单错误、请求后追加、标签切换、SPA 路由、整体与局部重渲染、500 项长列表、持续变化时钟、整页跳转）。
- `fixture/page2.html`：整页跳转目标。
- 真实网站：Wikipedia 搜索联想、Bilibili 搜索联想，HN / GitHub Trending / MDN 只测完整快照大小与空闲变化。
