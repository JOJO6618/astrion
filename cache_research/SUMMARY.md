# LLM API 缓存命中 Token 字段调研 · 总汇总

> 调研时间：2026-08-29 · 目的：为「验证各家 API 是否返回缓存命中 token」实验提供字段对照
> 详细报告：`official_overseas_v2/report.md`（海外官方）、`official_china/README.md`（国内官方）、`aggregators/report.md`（聚合层）
> ⚠️ 全部为官方文档 + 社区证据调研结论，**未经实请求验证**；实验时以实际返回为准。

---

## 一、缓存命中字段总对照表

### 官方 API

| 提供商 | 命中字段完整路径 | 写入字段 | 自动/显式 | 最低门槛 | 命中价折扣 |
|---|---|---|---|---|---|
| OpenAI Chat Completions | `usage.prompt_tokens_details.cached_tokens` | `...cache_write_tokens`（GPT-5.6+） | 自动（5.6+ 可显式断点） | 1024（5.5 及更早 2048） | 读 0.1×（5.6+）/ 0.5×（老模型） |
| OpenAI Responses API | `usage.input_tokens_details.cached_tokens` | `...cache_write_tokens` | 同上 | 同上 | 同上 |
| Anthropic Claude | `usage.cache_read_input_tokens`（顶层） | `usage.cache_creation_input_tokens`（另有 `cache_creation.ephemeral_5m/1h_input_tokens` 细分） | **显式** `cache_control` | 按模型 512/1024/2048/4096 | 读 0.1×、写 1.25×（5m）/ 2×（1h） |
| Google Gemini | `usageMetadata.cachedContentTokenCount`（SDK：`cached_content_token_count`） | 无 usage 内写入字段（显式缓存按资源 TTL 计费） | 隐式自动 + 显式 cachedContents | 隐式 2048（2.5）/ 4096（3.x） | 命中 ~0.1×（2.5+） |
| xAI Grok | `usage.prompt_tokens_details.cached_tokens`（Responses：`input_tokens_details.cached_tokens`） | 无 | 自动（建议 `x-grok-conv-id`/`prompt_cache_key`） | 未公布 | 有缓存价 |
| Mistral | `usage.prompt_tokens_details.cached_tokens` | 无 | 半显式（建议 `prompt_cache_key`） | 64 tokens 起，恒为 64 的倍数 | 读 0.1× |
| **DeepSeek** | **`usage.prompt_cache_hit_tokens`（顶层！）** + `prompt_cache_miss_tokens` | 无（自动） | 自动 | 未公布 | **读 ≈0.03×（$0.014 vs $0.44，折扣最大）** |
| **Kimi / Moonshot** | **`usage.cached_tokens`（顶层）**；部分官方示例为 `prompt_tokens_details.cached_tokens`——**两处都要读** | 无 | 自动（可用请求参数 `prompt_cache_key` 提命中率） | 未公布 | 读 0.1×~0.2×（k3 为 0.1×） |
| Qwen / DashScope | `usage.prompt_tokens_details.cached_tokens`；显式另有 `cache_creation_input_tokens`；Anthropic 兼容模式为 `cache_read_input_tokens` | 显式时上报创建量 | 隐式自动 + 显式 `cache_control` | 隐式 256（部分模型 2000）/ 显式块 1024 | 隐式读 0.2×；显式读 0.1×、写 1.25× |
| 智谱 GLM | `usage.prompt_tokens_details.cached_tokens` | 无 | 自动 | 512 | 读 0.5× |
| 豆包 / 火山方舟 | `usage.prompt_tokens_details.cached_tokens` | 创建接口响应同路径 | **仅显式**（Context API / Responses API `caching` 参数） | — | 缓存输入折扣价 + 存储费 |
| MiniMax | OpenAI 模式：`prompt_tokens_details.cached_tokens`；Anthropic 模式：`cache_read_input_tokens` | Anthropic 模式：`cache_creation_input_tokens` | 自动 + 显式（Anthropic 模式） | 512 | 读 0.1×~0.2× |
| 阶跃 Step | **`usage.cached_tokens`（顶层）** | 无 | 自动 | 256 | 读 0.2× |
| 百度千帆 | `usage.prompt_tokens_details.cached_tokens` | 无 | 自动 | 未公布 | 读 0.4× |

### 聚合层 / 中转（实验时最容易踩坑的一层）

| 服务 | 缓存字段行为 | 关键坑 |
|---|---|---|
| **OpenRouter** | 规范化为 `usage.prompt_tokens_details.cached_tokens` + 扩展 `cache_write_tokens` / `cache_discount` / `cost` | ⚠️ 它另有「响应缓存」`X-OpenRouter-Cache-Status: HIT`——命中时 **usage 全为 0**，与 prompt 缓存是两回事；个别上游（如 DeepSeek）缓存不过网关 |
| **opencode Zen / Go** | Zen 价格表单列 Cached Read/Write（必然解析了上游缓存字段）；「opencode go」= **$10/月订阅服务**，非 Go 语言版 | ⚠️ opencode 客户端流式解析有 bug（#33997）：`tokens_cache_read` 恒 0——别看客户端展示值，抓原始 SSE |
| **one-api / new-api / one-hub** | 意图透传 `cached_tokens`，但流式渠道多个已证实 bug（字段清零/计费错误/负 token） | ⚠️ 客户端收到的 usage ≠ 网关账单；非流式作基线对照 |
| **国内中转站（packycode、灵眸AI 等）** | 口碑「官转」站透传 Anthropic 原生 `cache_creation/read_input_tokens` 并按 5m cache write 计费；逆向接口站无缓存 | 社区验收标准=响应 usage 里有没有这两个字段 |
| **LiteLLM / Portkey / CF AI Gateway** | LiteLLM 双格式并存但 Anthropic 透传路径有 bug；Portkey 明确规范化；CF 未文档化（推测透传） | LiteLLM `/v1/messages` 路径不映射 `cached_tokens`（#27763） |
| **订阅制（Copilot/Cursor/Windsurf/Augment）** | 无公开 per-request usage API；Cursor/Augment 面板展示 cache read/write（数据来自上游响应） | 无法从响应侧做本实验，跳过 |

---

## 二、实验用统一读取器（Python 伪代码）

```python
def extract_cache_hit(usage: dict, body: dict | None = None) -> dict:
    """按优先级从各家 usage 中提取缓存命中 token 数。"""
    u = usage or {}
    details = u.get("prompt_tokens_details") or {}
    in_details = u.get("input_tokens_details") or {}
    candidates = [
        ("prompt_cache_hit_tokens", u.get("prompt_cache_hit_tokens")),          # DeepSeek（顶层）
        ("cached_tokens@top",       u.get("cached_tokens")),                    # Kimi / Step / 部分 DashScope（顶层）
        ("prompt_tokens_details",   details.get("cached_tokens")),              # OpenAI Chat / Qwen / GLM / MiniMax / 千帆 / xAI / Mistral / OpenRouter
        ("input_tokens_details",    in_details.get("cached_tokens")),           # OpenAI/xAI Responses API
        ("cache_read_input_tokens", u.get("cache_read_input_tokens")),          # Anthropic / Bedrock / MiniMax-Anthropic / 中转站
    ]
    hit = next(((k, v) for k, v in candidates if v), (None, 0))
    # Gemini 走完全独立的 usageMetadata（camelCase），从响应体而非 usage 取
    gemini = ((body or {}).get("usageMetadata") or {}).get("cachedContentTokenCount")
    return {"hit_tokens": hit[1] or gemini or 0, "field": hit[0] or ("usageMetadata" if gemini else None)}
```

---

## 三、实验设计要点（三份报告的共同结论）

1. **两轮法**：第 1 轮建缓存（命中=0 或走写入字段），第 2 轮同前缀不同后缀（命中>0）。两轮间隔必须在缓存 TTL 内（Anthropic/Qwen 显式 = 5 分钟）。
2. **前缀 ≥2048 tokens**，避开各家阈值差异（256~4096 不等）。
3. **流式必须 `stream_options: {"include_usage": true}`**，否则 OpenAI 系协议流式响应没有 usage chunk；Kimi 流式末 chunk 带 usage；Anthropic 看 `message_start` 事件。
4. **语义差异**：OpenAI 系 `prompt_tokens` **包含**缓存部分；Anthropic `input_tokens` **不含**缓存部分（cache_read 另算）。对账时别混。
5. **区分两种「缓存」**：网关级响应缓存（result cache，命中时 usage 可能归零）≠ prompt 前缀缓存（KV cache，本实验目标）。
6. **聚合层要抓三个视图**：客户端响应 usage、网关账单/消费日志、可直连时的上游原生 usage——三者可能互不一致（new-api #6144 教训）。
7. **首轮 `cache_read=0` 是预期行为**，不是字段丢失；写入字段（`cache_creation_input_tokens` / `cache_write_tokens`）>0 反而证明缓存机制在运作。

---

## 四、详细报告索引

| 报告 | 路径 | 覆盖 |
|---|---|---|
| 海外官方 | `official_overseas_v2/report.md` | OpenAI / Anthropic / Gemini / xAI / Mistral / Bedrock / Azure |
| 国内官方 | `official_china/README.md` + `usage_fields_reference.md` | DeepSeek / Kimi / Qwen / GLM / 豆包 / MiniMax / Step / 千帆 |
| 聚合层 | `aggregators/report.md` | OpenRouter / opencode Zen·Go / one-api·new-api·one-hub / 中转站 / Copilot·Cursor·Windsurf·Augment / LiteLLM·Portkey·CF |
