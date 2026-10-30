# ChatGPT高速模式

2026-10-30：在推理强度下面添加「ChatGPT高速模式」，仅一个自定义开关及「模型输出更快，但会消耗更多额度」。只在 ChatGPT OAuth 模型下显示，Agent 桌面／手机与 Chat 共用组件。模型目录未开放 Fast 时禁用开关。

状态保存在 `metadata.chatgpt_fast_mode`，独立于 `run_mode` 和 `reasoning_effort`。已有对话恢复自己保存的值。Agent 新对话继承刚离开的显式同工作区对话；Chat 新空页保留刚离开对话的值。无来源或旧对话缺字段时关闭，首消息保存前端当前选择。

请求按所选账号模型目录的 `service_tiers[].id=priority` 或 `additional_speed_tiers` 包含 `fast` 判断支持。开启且受支持的 OAuth 请求发送 `service_tier=priority`；其他请求不附加该字段。子智能体、标题与思考摘要的独立客户端不自动继承。

官方实现核对：

- https://github.com/openai/codex/blob/main/codex-rs/protocol/src/config_types.rs ：`ServiceTier::Fast.request_value()` 返回 `priority`。
- https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs ：模型目录的 Fast 能力字段与服务档位检查。
- https://learn.chatgpt.com/docs/agent-configuration/speed ：Fast 的订阅支持与额度说明。

## 专项回归

```sh
/opt/homebrew/bin/python3.11 -B 'test/2026-10-30_ChatGPT高速模式/run_regressions.py'
node 'test/2026-10-30_ChatGPT高速模式/frontend.cjs'
```

实际通过：20 项后端测试、7 组前端测试。

- `test_requests.py`：真实 APIClient／Responses 请求构建与传输调用，使用合成凭据和可控 SSE；检查开／关、非 OAuth 隔离、多账号目录能力、官方旧能力字段及推理强度保持。
- `test_settings.py`：真实 ConversationManager 事务验证 Agent 设置落盘／关闭、消息保持、磁盘失败不改运行实例、活动门闸／pending 任务拒绝、目标身份与布尔校验，以及 Chat 保存和客户端装配。
- `test_inheritance.py`：显式来源开启／关闭继承、无来源不扫描最近对话、旧记录不泄漏开启值、首消息保存选择及无效类型拒绝。
- `frontend.cjs`：Chat 新对话继承和发送、旧记录、保存失败、跨导航迟到响应、Agent 空页本地选择／明确目标保存、模型目录身份，以及真实共享组件 SSR 在桌面／手机的显示条件、位置、文案和单一开关。

回归入口在临时目录配置隔离运行数据，不加载真实 `.env`、凭据或用户对话，不请求真实付费模型。

## 关联验证

已执行并通过：

- `python3.11 -B -m unittest discover -s test/2026-10-25_Chat模式 -p 'test_*.py'`：51 项；设置 `ASTRION_IGNORE_DOTENV=1` 及隔离数据根。
- `python3.11 -B -m unittest discover -s test/2026-10-29_提供商多账号 -p 'test_*.py'`：53 项；同样隔离数据根。
- `node test/2026-10-25_Chat模式/frontend.cjs`：11 组。
- `node test/2026-10-25_Chat模式/shared_ui.cjs`：共用 SFC 编译、消息适配及空／非空 Chat 页面 SSR。
- 所有修改后端文件 `py_compile`；定向 ESLint、多语言审计、TypeScript、stylelint 及 Vite 构建。
- `git diff --check`。

存量验证限制：

- `test/2026-10-16_新对话初始化/backend.py` 在导入已删除的 `create_multi_agent_conversation` 时失败；其 `frontend.cjs` 首项仍要求旧 `multi_agent` 对话类型而失败。这两个旧测试未计为通过，也未为通过测试而恢复已删除入口。当前继承／首消息保存由本目录专项回归验证。
- 51 项 Chat 既有测试虽通过，其取消场景产生 pending coroutine／ContextVar 清理警告；本轮没有修改执行权限或取消机制，未将警告表述为已解决。
- 构建保留已有的大 chunk 提示。

## 实机验收

本轮未重启正在运行的后端。后端重新加载、页面刷新后，分别在 Agent 桌面、手机和 Chat 选择 OAuth 模型检查菜单；开关后新建对话，验证开启／关闭均继承；回到旧对话验证其值；切换其他提供商验证区域隐藏。

离线测试证明请求构造和设置链路，真实账号的 Fast 授权、用量扣减与实际速度仍需用户实机验收。没有把请求参数正确等同于已测得固定加速倍率。
