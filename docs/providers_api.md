# Providers API 契约（2026-09-24；多连接 / 多账号 2026-10-29）

> 后端实现：`server/providers.py` + `modules/provider_manager.py`（Codex 账号：
> `server/codex_auth.py` + `utils/api_client/codex/accounts.py`）。
> 权限：全部端点需登录 + **管理员**（host / docker 模式管理员），非管理员 403。
> 目录数据：`config/providers_catalog.json`；凭证与模型缓存：`<DATA_DIR>/providers.json`（0600，永不在 API 输出明文 key）；Codex 账号凭证：`<DATA_DIR>/codex_auth.json`（主账号）+ `<DATA_DIR>/codex_accounts/<connection_id>.json`，账号索引 `<DATA_DIR>/codex_accounts.json`（只存哈希/时间，不存 token），模型缓存 `<DATA_DIR>/codex_models/<connection_id>.json`（主账号沿用旧 `codex_models.json`）。

## 多连接模型（2026-10-29）

- 同一目录提供商可添加多个**独立 API Key 连接**；每条连接有独立模型列表，可独立刷新/断开。
- **内部连接 id 永不复用**：新连接 id 形如 `<catalog_id>-<8位十六进制>`（随机生成），删除后不会再次生成。模型 key 为 `{连接id}/{model_id}`，所以旧对话/旧模型配置在连接删除后只会解析失败，绝不会静默使用新 Key。
- **展示名按序号派生**：序号 0 = 目录原名，1 = `原名-1`，2 = `原名-2`…；序号在创建时分配并持久化，删除不改剩余编号，新增取当前最小空闲序号（删除 `原名-1` 后新连接重新占 `原名-1`；再添加则占下一个空闲号，如 `原名-3`）。序号只影响展示与 `provider_name` 分组，不参与请求路由。
- **旧版单连接数据**（`providers.json` v1）一次性迁移：原记录键（如 `deepseek`）保留为内部连接 id、展示序号 0，旧模型 key（如 `deepseek/deepseek-chat`）继续有效；迁移幂等。
- **不存删除墓碑、不存 Key 历史**；重复活跃 Key（同一目录 + 同一 api_key）返回 `duplicate_connection`。
- `auth=none` 的本地条目（Ollama/LM Studio）同一提供商仅允许一条连接（无独立 Key），已有连接时目录行 `can_add=false`。

### Codex（ChatGPT 订阅）多账号

- 每个 ChatGPT 账号一条连接：`id` 为 `openai-codex`（旧凭证迁移的主账号）或 `openai-codex-<8位十六进制>`（新增），展示名 `OpenAI（ChatGPT 订阅）-<稳定账号短哈希>`。
- 哈希源是账号身份（id_token 的 `chatgpt_account_id` / `sub` / `email`），**绝不哈希 token**；6 位前缀碰撞时只对新账号加长，已有账号哈希保持稳定。
- 各账号凭证、token 刷新、模型缓存完全隔离；重复登录同一账号更新原凭证（不新建、不改连接 id/哈希）。
- 同一时刻只允许一个登录流程：已有 pending 流程时登录起点返回 409 `login_in_progress`，绝不取消/覆盖别人的登录。

## 提供商目录与连接

### GET /api/providers/catalog

提供商页主数据源。输出两类行（前端按 `entry_kind` 分类，不按 `connected` 布尔）：

1. `entry_kind="catalog"`（`connection=false`）：目录服务商入口行，用于「连接 / 添加另一个 Key / 添加另一个账号」。已有连接（含凭证暂缺的已登记账号）时 `has_connections=true`、`connection_count>0`，入口仍保留（`can_add` 指示是否允许继续添加）。
2. `entry_kind="connection"`（`connection=true`）：每一条已登记记录，一个 API Key 一行、一个 ChatGPT 账号一行，带独立 `id`、`name`（展示名）、模型统计、`models_error`、`api_key_masked` 等。凭证暂缺的订阅账号仍输出，但 `connected=false`、`state="credential_missing"`、`credential_missing=true`，模型统计为 0；前端显示「凭证缺失」并允许通过 logout 移除登记。

```json
{
  "success": true,
  "catalog": [
    {
      "id": "deepseek",
      "catalog_id": "deepseek",
      "kind": "catalog",
      "entry_kind": "catalog",
      "connection": false,
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com",
      "icon": "deepseek-color.svg",
      "icon_url": "/static/icons/providers/deepseek-color.svg",
      "icon_mono": false,
      "auth": "api_key",
      "key_url": "https://platform.deepseek.com/api_keys",
      "badge": null,
      "local": false,
      "protocol_note": null,
      "connected": false,
      "has_connections": true,
      "connection_count": 2,
      "can_add": true
    },
    {
      "id": "deepseek-1a2b3c4d",
      "catalog_id": "deepseek",
      "kind": "connection",
      "entry_kind": "connection",
      "connection": true,
      "name": "DeepSeek-1",
      "base_name": "DeepSeek",
      "display_index": 1,
      "base_url": "https://api.deepseek.com",
      "auth": "api_key",
      "api_key_masked": "sk-ab...mnop",
      "models_count": 2,
      "models_fetched_at": "2026-10-29T...",
      "models_error": null,
      "connected": true
    }
  ]
}
```

ChatGPT 订阅连接行额外带 `account_hash`，`auth="codex_oauth"`。

字段说明：
- `auth`：`api_key`（填密钥连接）/ `none`（本地无密钥，Ollama/LM Studio）/ `codex_oauth`（ChatGPT 订阅 OAuth，连接流程走现有 `/api/codex/*`，**不要**调 connect）
- `icon_mono`：mono 图标（currentColor 黑），深色主题需 invert 处理（前端 `<img>` 加 filter 或内联 SVG）
- `protocol_note`：`chat_completions_only` 表示多协议网关仅 Chat Completions 子集可用（OpenCode Zen/Go）
- `badge`：目录自带的展示徽标（如 "API 密钥" / "ChatGPT 订阅 · OAuth" / "仅 Chat Completions" / "本地"），原文输出、前端按原文展示（i18n 由目录数据保证——目前仅中文语义短标签）

### POST /api/providers/connect

目录条目连接：
```json
{ "catalog_id": "deepseek", "api_key": "sk-..." }
```

自定义提供商连接：
```json
{
  "provider_id": "myprovider",
  "name": "我的提供商",
  "base_url": "https://api.example.com/v1",
  "api_key": "sk-...",
  "headers": { "X-Custom": "v" }
}
```

响应：
```json
{
  "success": true,
  "provider": { "...见下方 provider summary..." },
  "models_count": 12,
  "models_error": null
}
```

失败：`success: false` + `error`（`unknown_catalog_id` / `invalid_provider_id` / `invalid_base_url` / `provider_id_exists` / `api_key_required` / `codex_oauth_use_codex_api` / `duplicate_connection`）。HTTP 400。

注意：连接成功但 `/models` 拉取失败时 `success: true` 且带 `models_error`（凭证已保存，可稍后刷新重试）。目录条目每次成功调用都会新建一条独立连接（`duplicate_connection` 除外），响应的 `provider.id` 即新连接 id。

自定义提供商的 `provider_id` **只是基础标识**（展示/配置用，活跃期间重复提交返回 `provider_id_exists`）：后端保存与路由用的内部 id 是 `custom-<provider_id>-<8位十六进制>`；删除后用同一 `provider_id` 重建会分配新内部 id，旧 `{旧内部id}/{model}` 只会解析失败，不会静默使用新 Key。存量单连接记录仍保留旧键（如 `myprovider`）作为内部 id，旧引用继续有效。

无密钥本地提供商（`auth=none`，Ollama / LM Studio）内部存 `not-required` 占位值，但 `api_key_masked` 返回 `null`，不把占位值当凭证展示。

### provider summary（连接/列表/刷新返回的 provider 对象）

```json
{
  "id": "deepseek-1a2b3c4d",
  "catalog_id": "deepseek",
  "requested_id": "deepseek",
  "name": "DeepSeek-1",
  "base_name": "DeepSeek",
  "display_index": 1,
  "base_url": "https://api.deepseek.com",
  "api_key_masked": "sk-ab...mnop",
  "has_custom_headers": false,
  "enabled": true,
  "connected_at": "2026-09-24T...",
  "models": [{ "id": "deepseek-chat", "name": "DeepSeek V4 Pro", "context_length": 131072 }],
  "models_fetched_at": "2026-09-24T...",
  "models_error": null
}
```

### GET /api/providers

`{ "success": true, "providers": [provider summary...] }`

### POST /api/providers/<connection_id>/refresh

重新拉取该连接（一个 Key）的模型。响应 `{ success, models_count, provider, models_error? }`；连接不存在 404 / 拉取失败 success=false（旧模型列表保留）。

> 注：ChatGPT 订阅连接（`auth=codex_oauth`）的刷新走
> `POST /api/codex/models/refresh?account_id=<connection_id>`（见 server/codex_auth.py），
> 响应同样携带 `models_count`（含 ETag 304 路径，取当前缓存原始总数），
> 前端「提供商」分区统一按 `models_count` 展示「已刷新，共 N 个模型」。

### DELETE /api/providers/<connection_id>

断开该连接（删凭证+模型缓存）。`{ "success": true }`；不存在 404。断开后其模型立即从注册表消失；其它连接不受影响，剩余连接的展示序号不变。ChatGPT 订阅连接断开走 `POST /api/codex/logout`（body `{"account_id": ...}`）。

## Codex 账号（多账号）

全部端点同样要求登录 + 管理员。账号选择参数：query `?account_id=<connection_id>` 或 JSON body `account_id`；缺省 = 主账号（旧凭证迁移的 `openai-codex`，否则最早添加且仍保留的账号）。

- `GET /api/codex/accounts` → `{success, accounts:[{id, hash, display_name, connected, state, credential_missing, is_legacy, is_primary, created_at, last_login_at, models}], primary_account_id}`（**不返回 token / ChatGPT account_id 原文**）。`state=credential_missing` 表示凭证文件暂不可用：登记身份/哈希保留、可诊断，但不参与请求；正常读取不会把暂缺当登出，恢复后用同一连接 id（只有显式 logout 才删登记）。
- `GET /api/codex/status?account_id=` → 该账号连接状态 + 模型摘要，响应回带 `account_id`/`account_hash`/`account_name`/`is_primary`，前端据此丢弃晚到的旧账号响应；无任何账号时返回 200 `{connected:false, account_id:null}`
- `POST /api/codex/login/start` / `POST /api/codex/login/device/start` → 启动登录；已有 pending 流程时返回 409 `login_in_progress`（不取消/覆盖既有流程）。登录完成由账号身份决定更新原账号还是新建；poll 完成时返回 `connection_id`/`account_hash`
- `POST /api/codex/login/poll` / `cancel` → 同上（poll 完成后台刷新该次登录落到的账号模型）。取消与凭证落盘互斥：取消先到则不写凭证；提交临界区已开始时取消等待并保留已完成的登录结果，不产生“取消后仍写凭证”的中间态。
- `POST /api/codex/logout`（body/query `account_id`）→ 删除该账号凭证+索引条目，并清理该账号模型缓存（主账号只清模型段，保留全局 proxy/client_version）；返回新的 `primary_account_id` 与 `models_cache_cleared`。凭证删除失败（权限/占用）返回 500 `{success:false,error:"logout_failed",account_id,detail}`，登记与凭证保持原样；只有显式 logout 才删除登记，不做删除墓碑。
- `POST /api/codex/models/refresh?account_id=` → 刷新该账号模型缓存
- `GET /api/codex/usage?account_id=` / `GET /api/codex/reset-credits?account_id=` / `POST /api/codex/reset-credits/consume`（body 必带 `account_id` + `credit_id`）→ 均绑定所选账号；未知账号 404 `account_not_found`，绝不静默回落到其它账号
- `GET/POST /api/codex/settings` → 全局 proxy / client_version（**不随账号变化**）

## 自定义模型（custom_models.json 的 UI 化）

### GET /api/custom-models

```json
{
  "success": true,
  "models": [
    {
      "model_name": "my-model",
      "display_name": "...",
      "description": "...",
      "url": "https://api.example.com",
      "apikey": "sk-... 或 ${ENV_VAR}",
      "model_id": "qwen3-coder",
      "visible": true,
      "multimodal": "none|image|video|image,video",
      "reasoning_capability": "fast|thinking|fast,thinking",
      "reasoning_effort": true,
      "context_window": 262144,
      "max_output_tokens": 65536,
      "thinkmode_status": { "type": "param_toggle|switch_model", "...": "..." },
      "extra_parameter": {}
    }
  ],
  "source_path": "/.../custom_models.json"
}
```

apikey 原样返回（可能含明文或 `${ENV_VAR}` 引用）——管理面板内展示/编辑，这是管理员工具。

### POST /api/custom-models

Body 为一个完整模型条目（字段白名单见后端 `_ALLOWED_CUSTOM_FIELDS`）。必填：`model_name`（不允许 `/\` 和空白字符）、`url`、`model_id`、`apikey`。
409 `model_name_exists`；400 `<field>_required`。

### PUT /api/custom-models/<model_name>

整体替换该条目（body 同 POST；`model_name` 可改名，新名冲突 409）。404 `model_not_found`。

### DELETE /api/custom-models/<model_name>

删除条目。404 `model_not_found`。

## 模型注册表（现有端点的扩展）

### GET /api/v1/models

provider 同步模型与手写 custom / Codex 模型同构输出，新增字段：

- `provider_type`：`"provider"`（同步模型）/ 空（手写 custom）
- `provider_id`、`provider_name`：**按连接分组**（`provider_id` = 连接 id，一个 API Key / 一个 ChatGPT 账号一个组；`provider_name` = 带展示序号/账号哈希的展示名）
- key 形如 `{连接id}/{model_id}`（如 `deepseek/deepseek-chat`；OpenRouter 模型可能含多层斜杠 `openrouter/openai/gpt-4o`）；ChatGPT 订阅主账号仍为 `openai-codex/{slug}`，新增账号为 `openai-codex-<hash>/{slug}`

同步模型默认：`fast_only: true`、`supports_thinking: false`、`multimodal: "none"`、context_window 取自 /models 响应（可能为 null）。

所选模型 key 就是请求路由依据：provider 模型按 key 所属连接的 base_url/api_key 请求；Codex 模型由 profile 的 `responses_account_id` 精确路由到该账号凭证与模型缓存。标题/子智能体/审核等辅助调用同样按 key 解析 profile，因此严格使用所选连接。

Chat Completions 公共请求入口在快速模式下，为实际请求模型名末段以 `qwen` 开头（忽略大小写）的模型默认添加顶层 `enable_thinking: false`，覆盖主模型、标题、思考摘要及其他共用调用。显式 `extra_params.enable_thinking` 优先；思考模式、非 Qwen 模型及 Responses 请求不自动添加该字段。上游是否支持并执行该开关需按具体网关验证。

## 用户级模型可见性

- 字段：`personalization.hidden_models`（字符串数组，注册表 model key；后端已注册进 `DEFAULT_PERSONALIZATION_CONFIG` 与 sanitize 白名单）
- 读写：走现有 `GET/POST /api/personalization`（前端 store 的 `updateField({ hidden_models })` 机制）
- 语义：仅影响**当前用户**的模型选择器显示；管理员配置的提供商/模型对全员可见，用户各自决定自己看到哪些
- 前端过滤点：对话页模型菜单、模型偏好默认模型选择、标题模型选择、子智能体/审核智能体模型选择（所有用注册表列表的下拉）
