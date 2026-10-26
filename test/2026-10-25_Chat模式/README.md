# 正式 Chat 模式回归

2026-10-26 本任务此前通过 51 项后端回归。本次消息显示修复复跑通过 11 组前端状态、真实 RuntimeProjection→发送/轮询→真实 ChatArea 模板、共用界面/SFC 和 12 种审批组合检查，并完成正式构建页面的六阶段、9 组 Playwright 检查。使用隔离数据与可控模型，不读取真实用户会话或调用付费模型。

## 后端

```sh
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/chat-mode-test-runtime" \
.venv/bin/python -B -m unittest discover \
  -s 'test/2026-10-25_Chat模式' -p 'test_*.py'
```

覆盖：

- `test_storage.py`：原地编辑、编辑发送、整轮重生成、最新分支/临时查看、并发与命令幂等、坏文档、全树写回保护、联网通知、摘要失效。
- `test_shared_loop.py`：真实共用模型/工具循环，下一请求读取工具期间的正文编辑和独立记忆，隐藏通知不破坏工具协议。
- `test_tasks.py`：真实 TaskManager 线程、无工作区、硬取消正文/思考保留、半截工具丢弃、完整调用取消配对、保存失败终态。
- `test_http.py`：认证、账号媒体隔离、伪装文件拒绝、bootstrap/编辑/联网、草稿 CAS。
- `test_handoff.py`：持久截止期限、暂停倒计时、拒绝幂等、旧分支不续发、冻结材料和媒体导出、取消摘要、关联 Agent 创建及重试。
- `test_lifecycle.py`：Chat 标签刷新/返回/身份、旧分支与草稿媒体引用、多实例媒体锁、统一账号额度且不创建工作区。
- `test_duplicate.py`：全树复制幂等、正文隔离、后台交接不重放、活动源拒绝复制。
- `test_compression.py`：真实树摘要提交和重用、完整工具批次边界、并发编辑拒绝旧摘要、取消保留原树。

`split_task_helpers.py` 是已执行的一次性机械提取记录，不属于测试入口，不要再次运行。

## 前端状态与共用界面

```sh
node 'test/2026-10-25_Chat模式/frontend.cjs'
node 'test/2026-10-25_Chat模式/shared_ui.cjs'
node 'test/2026-10-25_Chat模式/composer_approval.cjs'
node 'test/2026-10-25_Chat模式/live_display.cjs'
```

`frontend.cjs` 覆盖异步加载/搜索/设置/发送归属、最新分支刷新、稳定响应身份、账号对话草稿。新增草稿初始化锁检查：旧读取不能解锁新编辑器；读取失败保留本地内容并解除锁定。

`shared_ui.cjs` 验证标准消息到既有 ChatArea 的适配、工具取消结果和持久结果优先、稳定媒体 ID、隐藏通知与建议标签、6 个共用 SFC 模板编译，并以 SSR 检查空页与已有对话确实使用原侧栏、模型菜单、ChatArea 和 InputComposer。这些检查本身不验证实际视觉与触屏效果。

`composer_approval.cjs` 执行真实 InputComposer 模板和源码中的审批显示计算，覆盖 12 种模式/折叠状态/待审批组合。Chat 即使存在残留审批数据也不实例化审批组件，编辑器、发送按钮与 Token 环保留；Agent 审批组件与展开判定保留。

`live_display.cjs` 使用真实后端 RuntimeProjection，覆盖初始等待头部、实际思考/正文、工具准备/执行/续接、最终快照、原形象状态和旧分支隔离；`projection_fixture.py` 模拟真实 call/preparing/execution 身份变化，`chat_area_fixture.cjs` 执行真实消息模板。

## 构建与静态检查

```sh
./node_modules/.bin/tsc --noEmit
node scripts/i18n_audit.mjs
./node_modules/.bin/eslint \
  static/src/chat/*.ts static/src/components/chat-mode/*.vue \
  static/src/components/chat/ConversationModelHeader.vue \
  static/src/components/chat/ChatArea.vue static/src/components/chat/MinimalBlocks.vue \
  static/src/components/input/InputComposer.vue \
  static/src/components/sidebar/ConversationSidebar.vue \
  static/src/App.vue static/src/app/bootstrap.ts \
  static/src/app/computed.ts static/src/utils/avatarStatus.ts --max-warnings=0
set -o pipefail
npm run build --silent 2>&1 | tail -n 5
```

本次对修改的草稿、页面接线、实时适配及共用形象文件执行定向 ESLint，连同 TypeScript、i18n 审计及 build 通过。build 包含 stylelint 与 Vite；保留已有大 chunk 提示。上列更广范围 lint 已在本任务前轮通过。

现有 `vue-tsc` 工具链此前在启动时抛出 `Search string not found: /supportedTSExtensions.../`，未把它计作通过；采用现有正常构建、SFC 编译和 SSR 编排检查补充验证，未修改依赖或配置掩盖该问题。

## 关联 Agent 回归

本任务此前通过：

- `test/2026-10-23_运行中压缩与状态边界/`：13 项。
- `test/2026-10-22_子智能体统一/test_dispatch.py`：2 项。
- `test/2026-10-23_桌面导航重构/run_regressions.py`：35 项后端、17 组前端。
- `test/2026-10-07_运行对话快照/run_regressions.py`：整套通过。

运行上述测试同样设置 `ASTRION_IGNORE_DOTENV=1` 与隔离 `ASTRION_DATA_ROOT`；桌面/快照脚本本机使用 `/opt/homebrew/bin/python3.11 -B`。

本次还复跑原 Agent 形象回归，均通过：

```sh
node 'test/2026-10-08_主子费用/avatar_tests.cjs'
node 'test/2026-10-08_主子费用_02/avatar_and_currency.cjs'
node 'test/2026-10-23_运行中压缩与状态边界/frontend.cjs'
node 'test/2026-10-23_子智能体数量与入口/frontend.cjs'
```

## 正式构建页面的浏览器联调

按用户要求使用 Playwright MCP。服务运行真实 Chat Blueprint、TaskManager、RuntimeService、共享模型/工具循环和构建后的正式前端；只将模型替换为可控流式实现。真实 `update_memory` 写入隔离 Chat 记忆。工作区/容器资源装配被断言为禁止调用。

先构建，再在独立持久终端启动测试服务（不启动生产 full/headless，不重启现有后端）：

```sh
ASTRION_IGNORE_DOTENV=1 \
ASTRION_DATA_ROOT="$PWD/cache/chat-browser-runtime-20261026-live" \
.venv/bin/python -B 'test/2026-10-25_Chat模式/browser_server.py' \
  > cache/chat-browser-server.txt 2>&1
```

测试地址 `http://127.0.0.1:8097/chat/new`。`browser_checks.js` 是传给 Playwright MCP `browser_run_code_unsafe(filename=...)` 的函数表达式，默认执行 messages 阶段，并把执行函数保存在当前 page。随后分别用以下短调用执行余下阶段，不把整套塞进单次长工具调用：

```js
async (page) => {
  page.__chatRegressionPhase = 'branches';
  return await page.__chatRegressionRun(page);
}
```

依次选择 `branches`、`tools`、`stop`、`draft`、`mobile`。messages 和 branches 使用同一 page；重跑整套应使用新 page，避免在已编辑的版本上重复断言 2/2。脚本等待真实可编辑的 TipTap 输入框，`waitForFunction` 使用正确的三参数形式。

六阶段均已实际执行通过，共 9 组结果：

- 发送立即出现用户消息、原 Astrion 回复头部/等待占位及 API 等待状态。
- 实际思考 chunk 驱动原形象思考状态。
- 正文在运行中显示，完成后清除等待，不使用“正在回复”文案。
- 用户编辑保存不增加节点。
- 重生成 2/2、切回 1/2、刷新恢复最新 2/2。
- 真实工具执行、结果与模型续接；刷新前后均无多余工具消息块。
- 停止保存后端已收正文，清等待/形象状态，刷新只保留一个部分回复。
- 人为阻塞草稿 GET 时输入/发送保持锁定，恢复后正常可输入。
- 390px 视口发送后头部、正文、输入框可见，未挂载审批面板。

结果见 `browser-results.json`；截图 `browser-waiting.png`、`browser-mobile.png`、`browser-final.png`。六阶段捕获未处理页面异常 0、HTTP 失败响应 0；控制台查询记录在 `cache/chat-browser-final-console.txt`。早先的输入初始化竞态已修复并由 draft 阶段验证，失败的旧长脚本不计作通过证据。

## 仍需实际验收

真实模型连续对话、多模态、联网服务、真实工作区交接，以及原生桌面/手机交互与用户视觉体验尚未验收。上述浏览器检查使用可控模型和普通浏览器视口，不等同于付费模型、原生平台或全部功能验收。没有重启现有主后端或执行 Git 提交/推送；已运行进程加载后端新模块需要按正常方式重新启动。
