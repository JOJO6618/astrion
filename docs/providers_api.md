# Providers API 契约（2026-09-24）

> 后端实现：`server/providers.py` + `modules/provider_manager.py`。
> 权限：全部端点需登录 + **管理员**（host / docker 模式管理员），非管理员 403。
> 目录数据：`config/providers_catalog.json`；凭证与模型缓存：`<DATA_DIR>/providers.json`（0600，永不在 API 输出明文 key）。

## 提供商目录与连接

### GET /api/providers/catalog

提供商页主数据源。预置目录 + 连接状态合并。

```json
{
  "success": true,
  "catalog": [
    {
      "id": "deepseek",
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
      "connected": true,
      "models_count": 2,
      "models_fetched_at": "2026-09-24T...",
      "models_error": null
    }
  ]
}
```

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

失败：`success: false` + `error`（`unknown_catalog_id` / `invalid_provider_id` / `invalid_base_url` / `provider_id_exists` / `api_key_required` / `codex_oauth_use_codex_api`）。HTTP 400。

注意：连接成功但 `/models` 拉取失败时 `success: true` 且带 `models_error`（凭证已保存，可稍后刷新重试）。

### provider summary（连接/列表/刷新返回的 provider 对象）

```json
{
  "id": "deepseek",
  "catalog_id": "deepseek",
  "name": "DeepSeek",
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

### POST /api/providers/<provider_id>/refresh

重新拉取模型。响应 `{ success, models_count, provider, models_error? }`；提供商不存在 404 / 拉取失败 success=false（旧模型列表保留）。

> 注：openai-codex 条目的刷新走 `POST /api/codex/models/refresh`（见 server/codex_auth.py），
> 响应同样携带 `models_count`（含 ETag 304 路径，取当前缓存原始总数），
> 前端「提供商」分区统一按 `models_count` 展示「已刷新，共 N 个模型」。

### DELETE /api/providers/<provider_id>

断开（删凭证+模型缓存）。`{ "success": true }`；不存在 404。断开后其模型立即从注册表消失。

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

- `provider_type`：`"provider"`（同步模型）/ `"codex"` / 空（手写 custom）
- `provider_id`、`provider_name`（同步模型有）
- key 形如 `{provider_id}/{model_id}`（如 `deepseek/deepseek-chat`；OpenRouter 模型可能含多层斜杠 `openrouter/openai/gpt-4o`）

同步模型默认：`fast_only: true`、`supports_thinking: false`、`multimodal: "none"`、context_window 取自 /models 响应（可能为 null）。

## 用户级模型可见性

- 字段：`personalization.hidden_models`（字符串数组，注册表 model key；后端已注册进 `DEFAULT_PERSONALIZATION_CONFIG` 与 sanitize 白名单）
- 读写：走现有 `GET/POST /api/personalization`（前端 store 的 `updateField({ hidden_models })` 机制）
- 语义：仅影响**当前用户**的模型选择器显示；管理员配置的提供商/模型对全员可见，用户各自决定自己看到哪些
- 前端过滤点：对话页模型菜单、模型偏好默认模型选择、标题模型选择、子智能体/审核智能体模型选择（所有用注册表列表的下拉）
