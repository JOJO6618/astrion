# 海外官方 LLM API「缓存命中 token 字段」调研报告（v2 重试版）

> 调研时间：2026-08-29
> 调研人：子智能体 #4
> 范围：海外**官方 API**（OpenAI / Anthropic / Google Gemini / xAI Grok / Mistral AI；AWS Bedrock 与 Azure OpenAI 仅简述透传方式）
> 方法：以官方文档为准（platform.openai.com / docs.anthropic.com / platform.claude.com / ai.google.dev / docs.x.ai / docs.mistral.ai / learn.microsoft.com / aws.amazon.com 官方博客），社区与第三方内容仅作辅助并标注来源等级。
> 说明：官方文档随时间变化（2026 年的文档已覆盖 GPT-5.x、Claude Opus/Sonnet 5 等新模型），本报告同时保留「历史经典行为」（如 OpenAI 1024 阈值、Anthropic 1024/2048）与「文档当前状态」，供实验对照。

---

## 1. 总览对照表

| 提供商 | 缓存类型 / 启用方式 | 命中字段 JSON 路径（非流式） | 写入（创建）字段 | 自动 / 显式 | 最低门槛 | 官方文档链接 |
|---|---|---|---|---|---|---|
| **OpenAI** Chat Completions | prompt caching（KV cache） | `usage.prompt_tokens_details.cached_tokens` | `usage.prompt_tokens_details.cache_write_tokens`（GPT-5.6+ 上报；老模型无写入字段） | 自动（implicit）；GPT-5.6+ 可选显式 breakpoint | 历史 1024 tokens（128 递增）；当前文档：GPT-5.6+ = 1024，更早模型 = 2048 | https://platform.openai.com/docs/guides/prompt-caching |
| **OpenAI** Responses API | prompt caching | `usage.input_tokens_details.cached_tokens` | `usage.input_tokens_details.cache_write_tokens` | 自动 / 显式（`prompt_cache_options.mode` + `prompt_cache_breakpoint`） | 同上 | https://platform.openai.com/docs/guides/prompt-caching |
| **Anthropic** Claude Messages API | prompt caching（前缀缓存） | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens`（另有细分对象 `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`） | 显式：块级 `cache_control`（`{"type":"ephemeral"}`）；也提供顶层 `cache_control` 自动断点 | 因模型而异：512 / 1024 / 2048 / 4096 均有（详见 §3 表） | https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching（即 platform.claude.com/docs/en/build-with-claude/prompt-caching） |
| **Google Gemini** GLM（Generative Language API） | 隐式缓存 + 显式 Context Caching（`cachedContents` 资源） | `usageMetadata.cachedContentTokenCount`（SDK snake_case：`usage_metadata.cached_content_token_count`；另有带 modality 细分 `cacheTokensDetails`） | 无「usage 内写入字段」；显式缓存通过 `cachedContents.create` 创建资源（TTL 计费） | 隐式：2.5 及更新模型自动；显式：须先创建 CachedContent 再传 `cachedContent` | 隐式：Gemini 2.5 = 2048 tokens，Gemini 3.x = 4096 tokens（历史：2.5 Flash 曾 1024 / 2.5 Pro 曾 2048）；显式：缓存资源 ≥1 分钟 TTL，按 token·时长计费 | https://ai.google.dev/gemini-api/docs/generate-content/caching |
| **xAI** Grok | prompt caching（messages 前缀缓存） | Chat Completions：`usage.prompt_tokens_details.cached_tokens`；Responses API：`usage.input_tokens_details.cached_tokens` | 无独立字段（官方仅暴露 `cached_tokens`） | 自动；建议设 `x-grok-conv-id` / `prompt_cache_key` 提升命中率 | 官方文档未公布固定 token 门槛（按消息前缀整段匹配） | https://docs.x.ai/developers/advanced-api-usage/prompt-caching |
| **Mistral AI** | prompt caching（前缀缓存，OpenAI 兼容格式） | `usage.prompt_tokens_details.cached_tokens` | 无独立字段（未命中时该字段为 0 或省略） | 显式：须在请求中传 `prompt_cache_key` 提高命中；命中与否由服务端决定 | 缓存块 = 64 tokens；`cached_tokens` 恒为 64 的倍数；<64 token 无命中 | https://docs.mistral.ai/studio/conversations/advanced/prompt-caching |
| **AWS Bedrock** | 透传：Claude 系用 `cachePoint`（system/tools 内）；Amazon Nova 自动缓存 | `usage`（原生透传 Anthropic 的 `cacheReadInputTokens`/`cacheCreationInputTokens`；converse 返回 `usage.cacheReadInputTokens` 等；SDK 中为 `usage_metadata`） | `cacheCreationInputTokens` | 显式（cachePoint）/ Nova 自动 | Claude 按模型（同 Anthropic）；Nova 最高 20K tokens | https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock |
| **Azure OpenAI** | 透传：与 OpenAI 字段一致（prompt caching） | Chat Completions：`usage.prompt_tokens_details.cached_tokens`；Responses API：`usage.input_tokens_details.cached_tokens` | `usage.prompt_tokens_details.cache_write_tokens`（GPT-5.6+） | 自动；GPT-5.6+ 支持 breakpoint / `prompt_cache_key` | 最低 1024 tokens，前 1024 必须完全一致 | https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching |

**一句话结论**：五家海外官方 API 中，OpenAI / xAI / Mistral / Gemini 用「自动或半自动 + `cached_tokens` 类字段」，Anthropic 用「显式 cache_control + read/write 双字段」。唯一同时提供「命中 + 写入」双向拆分的官方原生字段是 **Anthropic**（`cache_read_input_tokens` / `cache_creation_input_tokens`）与 **OpenAI GPT-5.6+ / Responses API**（`cached_tokens` / `cache_write_tokens`）。

---

## 2. 各家详细信息

### 2.1 OpenAI（Chat Completions API + Responses API）

**官方文档**：https://platform.openai.com/docs/guides/prompt-caching（2026-08 抓取，官方文档原文；历史公告 https://openai.com/index/api-prompt-caching 作辅助）

**1) 是否支持 / 自动或显式**
- 支持，**默认自动启用**（implicit caching），无需改代码。
- GPT-5.6 及更新模型支持**显式缓存断点**（`prompt_cache_options.mode: "explicit"` + 块上 `prompt_cache_breakpoint: {"mode":"explicit"}`）与 `prompt_cache_key`（影响路由、帮助同前缀请求命中同一台机器）。
- 更早模型仅有隐式缓存，断点由 OpenAI 按模型间隔自动放置。

**2) 命中字段完整 JSON 路径**
- Chat Completions API：`usage.prompt_tokens_details.cached_tokens`
- Responses API：`usage.input_tokens_details.cached_tokens`
- 官方文档定价示例（Responses API 用法，摘自官方 docs 原文）：

```json
// Responses API（官方文档 "Request 1 · Response usage"）
{
  "usage": {
    "input_tokens": 12000,
    "input_tokens_details": {
      "cached_tokens": 0,
      "cache_write_tokens": 12000
    }
  }
}
// 第二次请求命中：
{
  "usage": {
    "input_tokens": 15000,
    "input_tokens_details": {
      "cached_tokens": 12000,
      "cache_write_tokens": 3000
    }
  }
}
```

- Chat Completions（经典格式，官方社区/公告示例）：`usage.prompt_tokens_details.cached_tokens`：

```json
{
  "usage": {
    "prompt_tokens": 1253,
    "completion_tokens": 72,
    "total_tokens": 1325,
    "prompt_tokens_details": {
      "cached_tokens": 1024
    },
    "completion_tokens_details": {
      "reasoning_tokens": 0
    }
  }
}
```

**3) 缓存写入/创建字段**
- 有：Responses API `usage.input_tokens_details.cache_write_tokens`；Chat Completions `usage.prompt_tokens_details.cache_write_tokens`（GPT-5.6+ 上报；更早模型不计写入费、也不上报该字段）。
- 注：官方 docs 的成本计算示例同时读取 `cached_tokens` 与 `cache_write_tokens` 计算输入成本。

**4) 流式响应（stream=true）**
- Chat Completions：usage（含 cached_tokens）只在**最后一个 chunk** 返回，且必须设置 `stream_options: {"include_usage": true}`，否则流式响应不含 usage。
- Responses API：流式下 usage 在 `response.completed` 事件中携带，字段路径不变。
- 字段路径在流式与批式下**完全一致**。

**5) 最低门槛**
- 历史（2024-10 公告，GPT-4o/o1 时代）：**≥1024 tokens** 自动缓存，命中按 **128 tokens 递增**（1024/1152/1280/1408…），缓存通常 5–10 分钟无活动后清除、最长 1 小时。
- 当前官方文档（2026-08）：GPT-5.6 及以后 = **1024 visible tokens**；GPT-5.5 及更早 = **2048 visible tokens**（个别老模型可更短）；GPT-5.6 不再按 128 取整（精确到缓存断点），旧模型上报时向下取整到 128 倍数。

**6) 计费折扣**
- 历史模型：命中 token 打 5 折（50% off）。
- 当前：GPT-5.6+ 缓存读 0.1×、缓存写 1.25×（写一次 + 读一次 = 1.35× vs 不缓存 2×）；更早模型读价为模型相关折扣、写入不额外计费。

**7) 注意事项**
- 缓存匹配的是「完整渲染前缀」：model、tools、parallel_tool_calls、格式参数等任何相关设置变化都可能破坏前缀。
- 命中不保证 100%（路由溢出、机器未持有缓存）。官方建议用 `prompt_cache_key` 提高路由一致性。
- 实验时优先用 Responses API 的 `input_tokens_details`，或 Chat Completions 的 `prompt_tokens_details`；两处都要注意老模型字段可能为 null/缺省（`cached_tokens` 为 0 也算明确返回）。

### 2.2 Anthropic Claude（Messages API）

**官方文档**：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching（与 platform.claude.com/docs/en/build-with-claude/prompt-caching 同源，2026-08 抓取）

**1) 是否支持 / 自动或显式**
- 支持，**必须显式标记**：
  - 显式块级：在 `system` / `tools` / `messages.content` 块上加 `"cache_control": {"type": "ephemeral"}`（可加 `"ttl": "1h"` 延长到 1 小时）。
  - 自动断点：在请求**顶层**加一个 `cache_control`，系统自动把断点放到最后一个可缓存块，并随对话增长前移（2026 新增的 automatic caching 模式）。
- 断点最多 4 个；缓存前缀顺序 tools → system → messages。

**2) 命中字段完整 JSON 路径**
- `usage.cache_read_input_tokens`（本次请求从缓存读取的 token 数）
- `usage.input_tokens`（未命中、实际处理的 token 数）
- 官方文档示例（1 小时 TTL 输出）：

```json
{
  "usage": {
    "input_tokens": 2048,
    "cache_read_input_tokens": 1800,
    "cache_creation_input_tokens": 248,
    "output_tokens": 503,
    "cache_creation": {
      "ephemeral_5m_input_tokens": 148,
      "ephemeral_1h_input_tokens": 100
    }
  }
}
```

**3) 缓存写入/创建字段**
- 有：`usage.cache_creation_input_tokens`（写入缓存的新 token 数）；1 小时 TTL 时另有细分对象 `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`（二者之和 = cache_creation_input_tokens）。

**4) 流式响应（stream=true）**
- usage 在 **`message_start` 事件**的 `message.usage` 中（含 input_tokens / cache_creation_input_tokens / cache_read_input_tokens）；`output_tokens` 增量在 `message_delta` 事件。字段路径与批式一致。
- 官方原文："Monitor cache performance using these API response fields, within `usage` in the response (or `message_start` event if streaming)。"

**5) 最低门槛**（当前文档，按模型）
| 模型 | 最低可缓存 token 数 |
|---|---|
| Claude Opus 5 / Fable 5 / Mythos 5 | 512 |
| Claude Mythos Preview / Opus 4.7 | 2,048 |
| Claude Opus 4.6 / 4.5 | 4,096 |
| Claude Opus 4.8 / Sonnet 5 / Sonnet 4.6 / 4.5 / Opus 4.1 / Opus 4 / Sonnet 4 | 1,024 |
| Claude Haiku 4.5 | 4,096 |
| Claude Haiku 3.5 | 2,048 |

- 历史经典值（claude-3/3.5 时代）：Sonnet/Opus = 1024、Haiku = 2048。低于门槛即使打了 cache_control 也不会缓存、不报错，只会在 usage 里两个缓存字段都为 0。

**6) 计费折扣**
- 缓存写（5m TTL）：1.25× 基础输入价；缓存写（1h TTL）：2× 基础输入价；**缓存读/刷新：0.1× 基础输入价**（约 90% 折扣）。

**7) 注意事项**
- 命中要求前缀 100% 一致（一个字符差异即 miss）；缓存生命周期 5 分钟（1h 可选），从请求开始计时。
- 思考块（thinking）不能单独打 cache_control，但可作为助手轮内容被缓存；enabling/disabling web search、citations、effort 等设置会失效部分缓存。
- 可用 `max_tokens: 0` 预热缓存（不产生输出）。
- 官方提供 cache diagnostics 接口用于排查前缀差异。

### 2.3 Google Gemini（Generative Language API / Vertex AI）

**官方文档**：https://ai.google.dev/gemini-api/docs/generate-content/caching 与 REST 参考 https://ai.google.dev/api/generate-content（2026-08 抓取）

**1) 是否支持 / 自动或显式**
- 支持两种：
  - **隐式缓存**：Gemini 2.5 及更新模型默认自动启用，请求里什么都不用加。
  - **显式 Context Caching**：用 `cachedContents.create` 创建 CachedContent 资源（含 model/contents/systemInstruction/ttl），然后在 generateContent 请求传 `cachedContent: "<cache 资源名>"` 引用。可用 OpenAI 兼容库时在 `extra_body` 传 `cached_content`。
- Vertex AI 同样支持（context caching），字段名一致。

**2) 命中字段完整 JSON 路径**
- REST：`GenerateContentResponse.usageMetadata.cachedContentTokenCount`
- SDK（snake_case，Python/Node）：`response.usage_metadata.cached_content_token_count`
- 细分字段：`usageMetadata.cacheTokensDetails[]`（按 modality 的命中 token 明细）。
- 官方 REST 参考中 UsageMetadata JSON（节选）：

```json
{
  "promptTokenCount": integer,
  "cachedContentTokenCount": integer,
  "candidatesTokenCount": integer,
  "toolUsePromptTokenCount": integer,
  "thoughtsTokenCount": integer,
  "totalTokenCount": integer,
  "promptTokensDetails": [ { "modality": "...", "tokenCount": integer } ],
  "cacheTokensDetails": [ { "modality": "...", "tokenCount": integer } ],
  "candidatesTokensDetails": [ { "modality": "...", "tokenCount": integer } ]
}
```

- 社区实测示例（Gemini 2.5，来源：discuss.ai.google.dev，等级=辅助）：显式缓存命中时 `cached_content_token_count=4115`、`cache_tokens_details=[{modality:'TEXT', token_count:4115}]`。

**3) 缓存写入/创建字段**
- usage 内**没有**缓存写入字段；「写入」体现在显式 CachedContent 资源的计费（按 token 数 × 存储时长 TTL 计费），资源元数据中有 `usageMetadata.totalTokenCount`。隐式缓存的写入由 Google 内部处理，不暴露字段。

**4) 流式响应（stream=true / streamGenerateContent）**
- `usageMetadata` 在**最后一个 chunk** 返回（REST `streamGenerateContent` 的末帧；SDK 中亦在流结束的响应对象上）。当前官方文档没有为缓存命中另设流式事件，字段路径不变。
- 社区有多起「流式末尾 usageMetadata 里 cachedContentTokenCount 缺失」的报告（discuss.ai.google.dev，等级=辅助，未 100% 确认为官方 bug），实验时建议同时打印非流式结果对照。

**5) 最低门槛**
- 隐式（当前文档表）：Gemini 2.5 Flash / 2.5 Pro = **2,048 tokens**；Gemini 3.x（3.1 Pro Preview / 3.5 / 3.6 / 3.7 Flash）= **4,096 tokens**。
- 历史（Google 官方博客 2025-05）：2.5 Flash 曾 1,024、2.5 Pro 曾 2,048——阈值随版本调整，以文档当前值为准。
- 显式缓存：无 token 下限但资源有 TTL（默认 1h，可 300s 起），按 token×时间计费；≥1 分钟 TTL。

**6) 计费折扣**
- 隐式缓存命中：按缓存价计费（Gemini 2.5 起缓存输入约为基础价 10% 档；具体以官方定价页为准）。
- 显式缓存：命中 token 折扣 90%（2.5+ 模型）/ 75%（2.0 模型），外加缓存存储费（$/token·hour）。

**7) 注意事项**
- 隐式缓存命中率不受控制、非保证；显式缓存保证计费折扣但要多维护资源生命周期（create/list/update/delete API）。
- `cachedContentTokenCount` 只统计命中的 token，`promptTokenCount` 仍含全部输入；计算未命中部分 = promptTokenCount − cachedContentTokenCount。
- 实验时注意 SDK 属性名 snake_case（`cached_content_token_count`）与 REST camelCase（`cachedContentTokenCount`）的差异。

### 2.4 xAI Grok

**官方文档**：https://docs.x.ai/developers/advanced-api-usage/prompt-caching（How it works / Usage & Pricing / Best Practices & FAQ，2026-08 抓取）

**1) 是否支持 / 自动或显式**
- 支持，**完全自动**（按 messages 数组起始匹配前缀）；建议设置 HTTP 头 `x-grok-conv-id`（或 Responses API 的 `prompt_cache_key`）提升命中率。
- 无 cache_control 之类显式标记。

**2) 命中字段完整 JSON 路径**
- Chat Completions API：`usage.prompt_tokens_details.cached_tokens`
- Responses API：`usage.input_tokens_details.cached_tokens`
- 官方示例（Chat Completions）：

```json
{
  "usage": {
    "prompt_tokens": 125,
    "completion_tokens": 48,
    "total_tokens": 173,
    "prompt_tokens_details": {
      "text_tokens": 125,
      "audio_tokens": 0,
      "image_tokens": 0,
      "cached_tokens": 98
    },
    "completion_tokens_details": {
      "reasoning_tokens": 0,
      "audio_tokens": 0,
      "accepted_prediction_tokens": 0,
      "rejected_prediction_tokens": 0
    }
  }
}
```

- 官方示例（Responses API）：`usage.input_tokens_details.cached_tokens`（路径同 OpenAI Responses API）。

**3) 缓存写入/创建字段**
- 无。官方只暴露 `cached_tokens`（≤0 表示 miss；等于 prompt_tokens 表示整段命中）。

**4) 流式响应（stream=true）**
- 官方 FAQ：**流式与非流式均支持缓存**；流式下第一个空 token 对应缓存查找与 prefill 阶段。usage 聚合到最后一个 chunk（沿用 OpenAI 风格，需 `stream_options.include_usage`）。字段路径不变。

**5) 最低门槛**
- 官方文档未公布固定 token 门槛；机制按「消息前缀精确匹配」整段生效（示例中 3 条消息被整体缓存）。实验时建议让共享前缀 ≥ 数百 token 并保持多轮对话以观察命中增长。

**6) 计费折扣**
- 缓存命中 token 按「cached prompt token 价」计费（低于常规输入价；具体比率见各模型定价页）。

**7) 注意事项**
- 命中无保证（内存压力可驱逐缓存、请求可能路由到别的机器）；换 `x-grok-conv-id` 可强制 miss，便于对照实验。
- 典型多轮：turn1 cached=0（建缓存）→ turn2 cached=前 50 → turn3 cached=前 120（官方示例）。

### 2.5 Mistral AI

**官方文档**：https://docs.mistral.ai/studio/conversations/advanced/prompt-caching 与 API 参考 https://docs.mistral.ai/api/endpoint/chat（2026-08 抓取）

**1) 是否支持 / 自动或显式**
- 支持 prompt caching；**需在请求中显式传 `prompt_cache_key`**（会话/工作流 ID）来提升命中，且请求体必须保留共享前缀（多轮重发完整历史）。命中与否由服务端决定，key 不保证命中。
- 接口格式 OpenAI 兼容（/v1/chat/completions）。

**2) 命中字段完整 JSON 路径**
- `usage.prompt_tokens_details.cached_tokens`
- 官方示例：

```json
{
  "id": "a4db7c530548494f8ff9986bcd2a7737",
  "created": 1773840064,
  "model": "mistral-large-latest",
  "usage": {
    "prompt_tokens": 1013,
    "total_tokens": 1043,
    "completion_tokens": 30,
    "prompt_tokens_details": {
      "cached_tokens": 1008
    }
  },
  "object": "chat.completion"
}
```

- 未命中时 `cached_tokens` 为 0 或字段被省略。

**3) 缓存写入/创建字段**
- 无独立写入字段；计费侧「可收费未缓存输入 = prompt_tokens − cached_tokens」。

**4) 流式响应（stream=true）**
- 官方文档未单列流式差异；API 与 OpenAI 兼容，流式下 usage（含 cached_tokens）在最后一个 chunk（需 stream_options.include_usage 等开关）。实验时建议以「最后一个 chunk 的 usage」为准核对，并用非流式对照（等级：基于兼容性推断，官方未明示）。

**5) 最低门槛**
- **缓存块 = 64 tokens**：`cached_tokens` 恒为 64 的倍数；prompt < 64 tokens 不会命中；共享前缀越长可复用越多。

**6) 计费折扣**
- 缓存命中 token 按标准输入价 **10%** 计费（官方原文）。

**7) 注意事项**
- `prompt_cache_key` 不应包含密钥/敏感数据；变更 prompt 开头部分会导致 miss；命中率可在 Admin Panel › Usage 按模型查看。

### 2.6 AWS Bedrock / Azure OpenAI（透传简述）

**AWS Bedrock**
- 透传方式：Claude 系模型在 `converse` / `invoke_model` 请求的 system/tools 内放 `{"cachePoint": {"type": "default"}}` 标记缓存点（Claude 平台另有 cache_control 等效写法）；Amazon Nova 模型则自动缓存（文本 prompt，最多 20K tokens）。
- 响应中透传 Anthropic 原样字段：`usage.cacheReadInputTokens` / `usage.cacheCreationInputTokens`（REST）/ SDK `usage_metadata` 内 `cache_read_input_tokens` / `cache_creation_input_tokens`；AWS 官方博客示例：

```json
"usage": {
  "input_tokens": 10,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 37209,
  "output_tokens": 324
}
```

- 门槛/折扣与 Anthropic 一致（Claude）；Claude 之外模型（截至 2026-06 官方/社区口径）多数尚未支持缓存（AWS re:Post 有用户确认 Nova 支持、Mistral 在 Bedrock 上不支持）。
- 官方：https://aws.amazon.com/blogs/machine-learning/effectively-use-prompt-caching-on-amazon-bedrock

**Azure OpenAI**
- 透传方式：与 OpenAI 同名请求结构与字段，模型名改为 Azure 部署名。
- 命中字段：Chat Completions `usage.prompt_tokens_details.cached_tokens`；Responses API `usage.input_tokens_details.cached_tokens`；GPT-5.6+ 另有 `usage.prompt_tokens_details.cache_write_tokens`。
- 门槛：**≥1024 tokens** 且前 1024 tokens 完全一致；GPT-5.5 及更早按 128 递增取整，GPT-5.6+ 不取整；默认自动启用，GPT-5.6+ 支持 `prompt_cache_key` / `prompt_cache_options.mode` / `prompt_cache_breakpoint`、`prompt_cache_options.ttl="30m"`。
- 官方示例：

```json
{
  "usage": {
    "prompt_tokens": 1566,
    "completion_tokens": 1518,
    "total_tokens": 3084,
    "prompt_tokens_details": {
      "audio_tokens": null,
      "cached_tokens": 1408,
      "cache_write_tokens": 0
    },
    "completion_tokens_details": { "audio_tokens": null, "reasoning_tokens": 576 }
  }
}
```

- 官方：https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/prompt-caching

---

## 3. 实验建议（按供应商）

通用思路：**连续发两条「共享固定前缀 + 不同后缀」的请求**，记录第 1 次（写入/未命中）与第 2 次（命中）的 usage 字段；前缀长度务必超出各家门槛；两次请求间隔须在缓存 TTL 内。

### OpenAI
- 构造：固定 system/developer 指令（≥1024 tokens，建议 2000+）放最前 + 每次变化的 user 后缀；两次请求只改后缀。
- 读取字段：Chat Completions `usage.prompt_tokens_details.cached_tokens`；Responses API `usage.input_tokens_details.cached_tokens`（GPT-5.6+ 可同时看 `cache_write_tokens`/`cache_read` 0.1× 计费）。
- 断言：第 2 次请求 `cached_tokens > 0`（且应为前缀长度附近，老模型按 128 取整）。
- 流式：必须 `stream_options: {"include_usage": true}`，取最后一个 chunk 的 usage。
- 建议同时打印完整 `usage` 对象，防止 SDK 解包时字段为 None。

### Anthropic Claude
- 构造：system 数组放长文本（≥对应模型门槛，如 Sonnet 4.5/5 = 1024，Haiku 4.5 = 4096），块尾加 `"cache_control": {"type": "ephemeral"}`；连续发两条相同前缀请求。
- 读取字段：`usage.cache_read_input_tokens`（命中）与 `usage.cache_creation_input_tokens`（首次写入）；`input_tokens` 为未命中部分。
- 断言：第 1 次 `cache_creation_input_tokens ≈ 前缀长`、第 2 次 `cache_read_input_tokens ≈ 前缀长`；两次都在 5 分钟（默认 TTL）内。
- 流式：读 `message_start` 事件的 `message.usage`。
- 额外可试：`max_tokens: 0` 预热 + 顶层 `cache_control`（automatic caching）两种模式各跑一轮。

### Google Gemini
- 构造：
  - 隐式：长 system_instruction / 长首条 user 消息（≥2048 tokens，Gemini 2.5；≥4096 若用 3.x），连续请求、保持前缀不变。
  - 显式：先 `cachedContents.create`（model + contents + ttl，如 300s~1h），再 generateContent 传 `cachedContent`，用于对照验证。
- 读取字段：`response.usage_metadata.cached_content_token_count`（REST 为 `usageMetadata.cachedContentTokenCount`）；明细看 `cache_tokens_details[]`。
- 断言：显式缓存第 1 次 cached=0（或写资源）、之后 cached ≈ 缓存内容 token 数；隐式缓存命中需 ≥ 门槛且不保证，多试几轮或调高峰时段。
- 流式：取流末尾 chunk 的 usageMetadata 核对；若缺失可与非流式对照。
- 注意：promptTokenCount 含缓存 token，未命中部分 = promptTokenCount − cachedContentTokenCount。

### xAI Grok
- 构造：固定 system + 固定历史轮次 + 变化的最新 user 消息；设置 `x-grok-conv-id`（Chat Completions）或 `prompt_cache_key`（Responses API）保持一致。
- 读取字段：Chat Completions `usage.prompt_tokens_details.cached_tokens`；Responses API `usage.input_tokens_details.cached_tokens`。
- 断言：多轮递增 —— turn1 cached=0 → turn2 cached=前几轮总 token → turn3 更大；若一直为 0，改用不同/省略 conv-id 强制 miss 对照已验证机制。
- 流式：可观察第一个空 token（prefill），usage 取最后 chunk。

### Mistral AI
- 构造：固定 system + 历史 + 变化的最后 user 消息，**每条请求都传相同 `prompt_cache_key`（如会话 ID）并完整重发前缀**；前缀建议 ≥128 tokens（至少 2 个 64 块）以便观察倍数。
- 读取字段：`usage.prompt_tokens_details.cached_tokens`。
- 断言：第 2 次 `cached_tokens > 0` 且为 64 的倍数；未命中时为 0 或字段缺失。计费未缓存输入 = prompt_tokens − cached_tokens（10% 折扣）。
- 流式：取最后一个 chunk 的 usage；必要时非流式对照。

### AWS Bedrock / Azure OpenAI（顺带）
- Bedrock（Claude）：system 里加 `{"cachePoint": {"type": "default"}}`，看 `usage.cacheReadInputTokens` / `cacheCreationInputTokens` 增减。
- Azure OpenAI：与 OpenAI 实验相同（≥1024 tokens、前缀不动），看 `usage.prompt_tokens_details.cached_tokens` 与 GPT-5.6+ 的 `cache_write_tokens`。

---

## 附：信息来源与等级

| 内容 | 来源 | 等级 |
|---|---|---|
| OpenAI 全部字段/门槛/计费 | platform.openai.com/docs/guides/prompt-caching（官方，2026-08 抓取） | 官方文档 |
| OpenAI 历史 1024/128 递增/5 折 | openai.com/index/api-prompt-caching（官方公告） | 官方发布 |
| Anthropic 全部字段/门槛/计费/流式 | docs.anthropic.com（= platform.claude.com）prompt-caching（官方，2026-08 抓取） | 官方文档 |
| Gemini 隐式/显式/门槛 | ai.google.dev/gemini-api/docs/generate-content/caching、ai.google.dev/api/generate-content（官方） | 官方文档 |
| Gemini usageMetadata JSON | ai.google.dev/api/generate-content（官方 REST 参考） | 官方文档 |
| Gemini 2.5 隐式缓存历史阈值 | developers.googleblog.com（Google 官方博客） | 官方发布（辅助） |
| Gemini 流式/字段缺失现象 | discuss.ai.google.dev 社区帖 | 第三方（辅助） |
| xAI 全部字段/机制/流式 FAQ | docs.x.ai/developers/advanced-api-usage/prompt-caching（官方，2026-08 抓取） | 官方文档 |
| Mistral 全部字段/64 块/10% 计费 | docs.mistral.ai/studio/conversations/advanced/prompt-caching、docs.mistral.ai/api/endpoint/chat（官方） | 官方文档 |
| Bedrock cachePoint/usage 透传 | aws.amazon.com 官方博客 + AWS re:Post + Portkey 文档 | 官方博客/第三方辅助 |
| Azure OpenAI 字段/门槛 | learn.microsoft.com（微软官方） | 官方文档 |

> 提示：以上信息抓取于 2026-08-29，部分字段/门槛随时间迭代（如 Gemini 阈值、Anthropic 门槛、OpenAI GPT-5.6 行为改动）。实验前建议按本报告给出的官方链接复核最新值；凡第三方转述均已在文中标注「等级=辅助」。