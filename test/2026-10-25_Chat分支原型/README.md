# Chat 分支原型验证（保存语义修正版）

目标工程：`_experiments/chat-branch-prototype/`。独立模拟原型，未修改 Astrion 正式对话链路。

## 本轮实际结果

- `node --test 'test/2026-10-25_Chat分支原型/tree.test.mjs'`：23 项全部通过。
- Playwright MCP 执行修正后的 `browser-checks.js`：12 组页面交互全部通过，pageerror / console error 为 0。
- 本轮修改的 tree/store/render 和浏览器测试模块 `node --check` 通过；其他模块沿用此前语法检查结果。
- 结果明细：`browser-results.json`。截图：`desktop-classic.png`、`desktop-dark.png`、`desktop-light.png`、`mobile.png`。

本轮浏览器测试在新 context 中明确验证：

1. 用户编辑保存：仍是原 ID，4 个节点仍为4个，原后续显示完整，不出现版本切换器。
2. 历史 AI 编辑保存：原地修改，原子节点和末端不变，不出现分支点。
3. 末条 AI 编辑保存：节点身份和版本数不变，能在原路径继续对话。
4. 用户编辑发送才增加用户和 AI 节点，创建版本切换器。
5. 重新生成/重试创建 AI 兄弟节点，旧版本与失败记录保留。
6. 生成中切换不串支、停止、流式中刷新恢复、空编辑、持久化、导出和三主题/窄屏交互。

数据层还覆盖已有分支上的保存、连续保存和未改正文保存：节点数、兄弟版本顺序、父子关系不变。160 次混合操作确认只有原地保存能修改指定旧正文，发送和重生成不会覆盖旧正文。

## 复现

```sh
node --test 'test/2026-10-25_Chat分支原型/tree.test.mjs'
node _experiments/chat-branch-prototype/server.mjs
```

使用服务实际端口运行浏览器入口：

```sh
node 'test/2026-10-25_Chat分支原型/run-browser.mjs' http://127.0.0.1:5178/
```

独立 Node 浏览器入口此前在 Chromium headless shell 启动阶段返回 `icudtl.dat not found in bundle`，未进入断言；此入口尚未验证通过。已通过的通道为 Playwright MCP：页面导航到原型地址后，调用 `browser_run_code_unsafe`，`filename` 指向本目录 `browser-checks.js`。两条入口共用断言，测试 context 不清理用户原型页面的数据。

此前错误语义下创建的测试分支保留，本次未删除用户已有浏览器数据。修正后保存不再增加分支。

此回归不验证真实 API、工具协议、图片视频、压缩、正式存储、跨标签页或生产级长树性能。
