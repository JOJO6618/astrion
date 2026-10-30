# 设置页与提供商体系（完整）

> 本文由 `AGENTS.md` §15 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 15) 设置页与提供商体系（2026-09-24 新增）

> 设计稿：`cache/settings-demo/index.html`（用户拍板的静态稿）；后端 API 契约：`docs/providers_api.md`。

### 15.1 个人空间拆分：/settings 全屏设置页

**机制一句话**：原个人空间抽屉（13 Tab）拆分——「个性化 + 账户」留在抽屉（PersonalizationDrawer，2 Tab），其余设置项迁到 `/settings` 全屏页（SettingsShell，5 组 14 分区）；路由沿用 workflows 已验证的手写 History API 分支（**不引 vue-router**）。

**硬约束（改代码必须知道）**：

1. **路由必须前后端双登记**（2026-09-24 踩坑：只登前端导致整页跳转 404）：后端 `server/auth.py` 页面路由组注册 `/settings` 与 `/settings/<path:section>` 返回 `index.html`（conv converter 只匹配时间戳格式 `\d{8}_\d{6}_\d{3}`，不会收容任意路径——`/workflows` 能通则是因为 `server/workflow_page.py` 专门注册过）；前端 `route.ts` 的 `isConversationIndependentRoute()` 登记 `settings` 与 `settings/*`，`bootstrapRoute` 独立路由分支写 `state.settingsRoute`（空串=不在设置视图）；进出都是整页跳转（`openSettingsPage` → `/settings`，`closeSettingsPage` → `/new`），**不做 SPA 内 pushState**——pinia store 每次重建，`activeSection` 永远从 `general` 开始（用户拍板默认落「通用」），不存在跨页会话记忆。
2. **挂载在 App.vue 的 Agent 加载门槛之前**：`<SettingsShell v-if="settingsRoute" @close="closeSettingsPage" />`；后续加载页和 Agent 内容用 `v-else-if`／`v-else` 互斥。组件在 `app/components.ts` 异步注册。SettingsShell 自己加载账号／偏好／分区数据，`lifecycle.mounted` 在设置路由只恢复桌面导航和必要监听，不启动 Agent 连接、工作区运行态或轮询。禁止以 `isConnected` 阻挡设置页挂载，否则跳过 Agent 初始化会造成永久加载（2026-10-26 已实测复现并修复）。
3. **URL 深链**：`/settings/<section>` 由 SettingsShell onMounted 解析（先 `await fetchSessionStatus()` 确保管理员判定就绪），非法分区、或非管理员访问管理员分区时不生效回默认。
4. **两宿主互斥**：SettingsShell 与 PersonalizationDrawer 都 `provide('personalizationDrawer')` 同一份 context（`usePersonalizationContext.ts`，124 键）且各自注册全局监听——**禁止同时可见**（路由层已天然互斥：抽屉只在非 settings 路由渲染，注意保持）。
5. **新增设置分区三处同步**：SettingsShell 的 NAV_GROUPS + SECTION_META、stores/settings.ts 的 SettingsSection 类型；文案走 `settings` 命名空间（zh-CN 为源）。
6. **管理员分区**：`SETTINGS_ADMIN_SECTIONS`（stores/settings.ts）= codex/admin/providers；导航过滤 + 运行时降级回退 general 由 SettingsShell 处理。
7. **侧边栏个人入口**：用户名 + 上弹二级菜单（个人空间/个性化/设置/帮助-置灰），`ConversationSidebar.vue` emit `personal(tab?)` → `openPersonalPage(tab)` 透传 → `personalization.openDrawer(tab)`（'preferences'|'account'）。

### 15.2 提供商体系（opencode 式）

**机制一句话**：设置页「提供商」分区选服务商 → 填 API key → 后端 GET 其 `/models` 自动拉取全部模型注册进模型库；「模型」分区按提供商分组做可见性开关（用户级）+ custom_models.json 的 UI 化管理。

**硬约束（改代码必须知道）**：

1. **目录与存储**：`config/providers_catalog.json`（28 条静态目录：id/name/base_url/icon/auth/protocol_note/key_url + 2026-09-25 新增 modelsdev_key/protocol_map/protocol_source + 2026-09-26 新增 static_models；**双站拆分原则=一个 base_url 一条目**，如 minimax-cn/minimax-global、zhipu-cn/zhipu-global；**编程订阅套餐条目**（badge=「编程订阅」，如 kimi-code-cn、glm-coding-cn、bailian-coding、volcengine-coding）与按量条目并列，套餐 Key 与按量 Key 不通用；`static_models`=可选静态模型清单，仅当 GET /models 失败或为空时由 `provider_manager._static_models_fallback` 回退使用——部分套餐端点官方不提供模型列表，在线发现永远优先）；凭证存 `<DATA_DIR>/providers.json`（0600 原子写，**不纳入 git**）；`modules/provider_manager.py` 是唯一权威（catalog 合并连接状态 / connect / refresh / disconnect / parse_models_payload）。
2. **协议支持 = Chat Completions + Responses 双协议（2026-09-25 泛化）**：`/models` 不标注协议，裁决走混合策略——catalog 静态 `protocol_map`（最高优先）→ models.dev `npm` 字段（`protocol_source="models.dev"` 门控，裁决唯一权威 `modules/modelsdev_registry.py::resolve_protocol`）→ 默认 chat；Responses 请求统一走通用层 `utils/api_client/responses/`（translate.py 纯协议转换 + mixin.py `chat_responses` 编排，bearer / codex_oauth 双 auth），按加密 reasoning 处理（无策略配置）。Anthropic Messages / Google 原生协议**不做适配**：模型正常注册显示，`chat()` 按 `api_protocol` 明确报错，配置页 tooltip 提示（catalog() 输出 `unsupported_protocol_count`）。`_fetch_models` 在 fetch 层剔除非聊天模型（embedding/tts 等），**解析层 `parse_models_payload` 保持纯净**（兼容 `{data:[]}` / 裸数组 / `{models:[]}` 三种形状）——过滤逻辑不要下沉到解析层。
3. **注册表三路合并**：`get_registered_model_profiles()` = 手写 custom + provider 同步 + Codex；provider 模型 key = `{provider_id}/{model_id}`（codex 为 `openai-codex/{slug}`）、`provider_type="provider"` + `api_protocol` 字段（chat_completions/responses/anthropic_messages/google），chat 链路复用 chat_completions + `profile["headers"]` 经 `extra_headers_resolver` 链式合并；get_profiles() 同时用 models.dev enrich 四字段（思考支持/图片视频多模态/上下文窗口/最大输出），查不到回退原默认。
4. **权限=管理员级**：`server/providers.py` 全部端点 `@api_login_required + @admin_api_required`；前端 SETTINGS_ADMIN_SECTIONS 含 providers；用户级 key 是后续预留方向（注册表全局单例，改动深，勿擅自做）。
5. **推理强度滑块绑思考能力（2026-09-25 用户拍板）**：`supports_reasoning_effort` 在所有路径统一跟随 `supports_thinking`——provider 同步（provider_manager.get_profiles）、手写 custom（model_profiles.py）、codex（codex/models.py 硬编码 True）三方一致；Responses 链路 effort 透传，chat 链路随请求体发 `reasoning_effort`。
6. **custom_models.json UI 化**：`/api/custom-models` CRUD 直接读写部署目录 custom_models.json（存量手写条目自动出现在 UI「自定义」分组，`${ENV_VAR}` 引用原文保留可编辑）；`_sanitize_custom_model` 白名单校验，写入始终落 `DEPLOY_CONFIG_DIR`。
6. **模型可见性=用户级**：`personalization.hidden_models`（后端只注册字段 + sanitize，**过滤在前端** stores/model.ts 做，所有选择器消费同一过滤后数据源）；与提供商管理（管理员级）是两条独立权限线，不要混。
7. **Codex 即 openai-codex**：OpenAI 拆两条——openai-api（api key）与 openai-codex（ChatGPT 订阅 OAuth，auth=codex_oauth，连接状态用现有 Codex 凭证文件判定，前端复用 `/api/codex/login/*` 既有流程，不要为 OAuth 发明新端点）。**连接管理单一入口（2026-09-25 用户拍板）**：openai-codex 的连接/断开/刷新模型只在「提供商」分区操作（登录唯一实现 = `settings/tabs/providers/useCodexLogin.ts` + `CodexConnectDialog.vue`）；设置页 Codex 分区（CodexTab.vue）只保留状态展示 + 用量/重置额度/代理等高级管理，**禁止**再内联登录/断开/刷新按钮。契约对齐：`/api/codex/models/refresh` 与普通提供商 refresh 一样返回 `models_count`（含 304 路径）。
8. **图标**：`static/icons/providers/`（61 个 SVG，来自 lobehub/icons，按 catalog 的 icon 字段引用 `/static/icons/providers/<icon>.svg`）；新增提供商先补图标再补目录。
9. **ollama/lmstudio 是 auth=none**：本地服务无需 key，connect 直接探 `/models`。
10. **models.dev 数据源（D 方案）**：`config/modelsdev_snapshot.json` 仓库瘦身快照兄底（`scripts/update_modelsdev_snapshot.py` 生成）+ 在线缓存 6h 节流刷新（connect/refresh_models 顺带）；`protocol_source` 门控必须存在（openai 官方 npm=@ai-sdk/openai、xai/openrouter 用自家包名，无脑按 npm 裁决会误判单协议 provider）。
11. **Responses 互切锁 = 协议级二分**：chat↔responses 跨协议切换 409（`server/chat/settings.py`，i18n key `protocol_switch_locked`）；responses 跨 provider 自由切（加密 reasoning 块按 `_same_responses_provider()` 判定非同 provider 时剥离回插，有损放行不锁）；老对话 profile 查不到（api_protocol=None）不锁放行。
12. **codex 身份已彻底泛化（2026-09-25，无向后兼容）**：`provider_type="codex"` 消亡，`codex/mixin.py`（chat_codex）已删，身份标识改 `responses_auth="codex_oauth"`（api_key 豁免/instructions 注入/effort 档位校验/429 订阅文案都按它判定）；老对话 `codex/xxx` model_key 直接失效需重新选择。`x-opencode-session` 头开关 `external_session_header` 默认开启（opt-out，仅 opencode.ai 域名下发）。


---

### ChatGPT高速模式（2026-10-30）

- UI：推理强度下增加「ChatGPT高速模式」，一个开关及「模型输出更快，但会消耗更多额度」。仅 `responses_auth=codex_oauth` 的模型显示；模型不支持时禁用。共用 `ChatgptFastModeSection.vue`，Agent 手机独立菜单也挂同一组件。
- 能力：`utils/api_client/codex/service_tier.py` 按所选账号模型目录的 `service_tiers[].id=priority` 或官方旧字段 `additional_speed_tiers` 包含 `fast` 判断。模型 API 与 Chat 设置目录分别下发 `is_chatgpt_model` 和 `supports_chatgpt_fast_mode`；不按模型名或提供商前缀猜测。
- 状态：唯一权威 `metadata.chatgpt_fast_mode`，布尔值，旧对话缺字段为关闭。Agent 新空页继承显式同工作区 `previous_conversation_id`，无来源关闭；Chat 空页保留刚离开对话的值。首消息捕获并保存本次选择；现有对话从 bootstrap／Chat 文档恢复，空页不受状态轮询覆盖。
- 写入：Agent `POST /api/chatgpt-fast-mode` 明确传 `conversation_id`，持显示锁→任务锁检查 pending/running/cancel_requested 和门闸，事务成功后才同步运行实例；Chat 复用设置 PATCH 及任务受理校验。运行中不可切换，保存失败不伪报成功。
- 请求：OAuth、开启且目录支持时，在共用 Responses 链路发送 `service_tier=priority`（官方 Fast.request_value 的实际取值）。关闭或非 OAuth 不发送；保持思考模式和 reasoning effort 独立。子智能体、标题、摘要等独立客户端不自动继承。
- 回归：`test/2026-10-30_ChatGPT高速模式/`；离线捕获请求体不代表真实订阅已授权或实际吞吐已达某个倍率，实机速度与视觉由用户验收。
