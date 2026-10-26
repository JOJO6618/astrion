# Chat 共用能力接入修复（2026-10-26）

## 实际修改

- Agent 与 Chat 共用 `modules.external_session.resolve_context_headers`：当前对话决定稳定外部会话 ID，Chat 使用账号偏好目录读取原有 `external_session_header` 开关。沿用 OpenCode 端点限制和 opt-out，不创建工作区。提供商静态请求头继续链式合并。
- Agent 与 Chat 共用随机等待文案模块，Chat 轮询替换快照保留同一占位消息的文案；Agent 恢复运行快照也补齐文案。
- Agent 与 Chat 共用重试／工具参数／致命错误提示格式。Chat 消费本次绝对游标之后的错误事件，不等待最终失败；最终事件与 result.error 不重复弹窗。初始加载已失败任务仍补查一次最终错误。
- 设置页挂载移至 Agent 的 isConnected 加载门槛之前，各设置分区自身加载数据，生命周期跳过 Agent 资源初始化。保留角色判定、深链和返回 Chat 行为。

## 已执行的离线验证

```sh
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/cache/chat-fix-tests" \
  /opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-26_Chat共用能力修复' -p 'test_*.py'
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/cache/chat-fix-tests" \
  /opt/homebrew/bin/python3.11 -B -m unittest discover \
  -s 'test/2026-10-25_Chat模式' -p 'test_*.py'
node 'test/2026-10-26_Chat共用能力修复/frontend.cjs'
node 'test/2026-10-25_Chat模式/frontend.cjs'
node 'test/2026-10-25_Chat模式/live_display.cjs'
node 'test/2026-10-25_Chat模式/shared_ui.cjs'
ASTRION_IGNORE_DOTENV=1 ASTRION_DATA_ROOT="$PWD/cache/chat-fix-tests" \
  /opt/homebrew/bin/python3.11 -B 'test/2026-10-07_运行对话快照/run_regressions.py'
node 'test/2026-10-08_主子费用/avatar_tests.cjs'
```

结果：新增后端 3 项、原 Chat 后端 51 项通过；新增前端涵盖稳定／多种等待文案、重试错误、最终错误去重、受理时已失败、真实 App 模板在未连接下显示设置、设置生命周期不启动 Agent。原 Chat 11 组前端状态、真实后端投影→共用消息区、SFC／共用 UI、Agent 显示快照整套（含 27 项 projector、12 项 snapshot、6 项后端 smoke 和全部前端组）、8 组头像状态通过。

原 Chat 后端测试通过后，取消工具场景仍出现 pending task / ContextVar 不同 context 的清理警告；没有将其修饰为“零警告”，也未在此次请求头／设置修复中改动工具取消机制。

相关 Python 编译、TypeScript、定向 ESLint（零 warning）、i18n 审计、正式前端构建（含 stylelint）通过；构建保留已有 >500kB chunk 提示。

## 8091 正式页面与真实模型

测试使用实际配置的 `ocgo/deepseek-v4.1-flash`，并非 fixture 模型。

- 修复前：`conv_20261026_204953_194` 的真实请求连续 5 次 `400 MissingSessionID`，任务失败。此前 GPT 订阅模型对照的两轮对话成功，证实该失败不能笼统归为整个循环未执行。
- 修复后：重启本次启动的 8091 测试服务，Playwright 新建 `conv_20261026_214644_258`，默认 OpenCode 模型成功输出思考和 `Chat已连通。`，无 error 事件，外部会话 ID 已持久保存。等待占位显示原有文案“领域展开”，多次轮询不跳变。
- 重新生成成功保存两个 AI 版本；刷新显示最新 2/2。
- 切回 1/2 后继续发送，新的 user 节点 parent_id 确实指向第一版 AI，而非第二版；原第二版仍在整树内；刷新保留此次续聊主线及 `分支续聊成功。`。
- `/settings/chat?from=chat` 正常显示默认模型、思考、联网、指令和记忆。`/settings/providers?from=chat` 正常显示提供商页面。对应设置页面控制台 error/warning 为 0。

本地诊断证据位于 `cache/chat-real-task.json`、`cache/chat-opencode-fixed.json`、`cache/chat-settings-fixed-status.json`、`cache/chat-settings-fixed.yml`、`cache/providers-settings-check.yml`。这些是本机诊断文件，不包含凭据，不应当作产品运行态提交。

边界：未验收全部九工具、多模态、原生桌面／手机和 Chat→Agent 实际交接。错误提示按真实错误事件格式做离线回归，未为了制造真实错误而修改用户的共享账号开关或凭据。未提交或推送 Git。
