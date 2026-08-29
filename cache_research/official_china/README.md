# 国内官方 LLM API「缓存命中 Token」字段对照报告

> 调研子智能体 #2 · 调研时间：2026-08-29
> 调研范围：**国内官方 API**（DeepSeek / Moonshot Kimi / 阿里通义千问 / 智谱 GLM / 字节豆包 / MiniMax / 阶跃星辰 Step / 百度文心千帆）
> 数据来源：以**各厂商官方文档**为准（文末附全部 URL）；个别引用了第三方报道处已单独标注。
> 结论确定性说明：本文所有"字段名 / 官方示例 JSON / 官方计费规则"均直接取自官方文档，可据此设计实验；**本文只做了文档调研，未实际跑请求验证**，实测时字段是否如实返回以实验为准。

---

## 一、总览表（速查）

| # | 厂商 | 官方平台 | 缓存机制 | 命中字段（usage 内位置） | 最小触发阈值 | 命中计费折扣（官方口径） |
|---|---|---|---|---|---|---|
| 1 | DeepSeek | api.deepseek.com | **自动**（无需配置） | 顶层 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`（**注意：不在 details 里**） | 文档未给出固定值 | deepseek-v4-flash 峰值：缓存命中 $0.014/M vs 未命中 $0.44/M（≈3.2%，约 1/31） |
| 2 | Moonshot Kimi | platform.moonshot.cn / platform.kimi.com | 自动（当前推荐）；历史上曾有显式 cache API | `usage.cached_tokens`（顶层，官方示例）；部分资料为 `usage.prompt_tokens_details.cached_tokens` | 文档未给出固定值（按前缀匹配） | kimi-k3 为 $0.30/M vs $3.00/M = **10%**；k2.7-code ≈20%；k2.6 ≈16.8%；k2.5 ≈16.7% |
| 3 | 通义千问 Qwen | DashScope / 阿里云百炼 | OpenAI 兼容：**隐式自动** + 显式 `cache_control`；原生 DashScope：同一套显式机制 | OpenAI 兼容：`usage.prompt_tokens_details.cached_tokens`（命中）、`...cache_creation_input_tokens`（创建）；DashScope：`usage.prompt_tokens_details.cached_tokens`（部分海外地域 `usage.cached_tokens` 顶层） | 隐式 ≥256 tokens（Qwen3.7 系列约 2000）；显式块 ≥1024 tokens | 隐式命中 20%（阿里部署常规）；显式命中 10%、创建 125%；qwen3.8-max 例外（以控制台为准） |
| 4 | 智谱 GLM | bigmodel.cn | **自动**（默认开启） | `usage.prompt_tokens_details.cached_tokens` | 智谱部署 GLM 为 512 tokens（阿里云文档口径） | 命中按标准价格 **50%**（智谱官方）；阿里云转售口径 25% |
| 5 | 字节豆包 Doubao | 火山方舟 volces.com | **仅显式**（Context API 待下线；Responses API 推荐） | `usage.prompt_tokens_details.cached_tokens` | 无自动缓存；最大缓存长度≈上下文窗口-最大输出 | 缓存输入折扣价（低于新输入）+ 存储费（元/千 token/小时） |
| 6 | MiniMax | platform.minimaxi.com（国内）/ platform.minimax.io（海外） | 自动（被动）+ 显式 Anthropic 兼容 | OpenAI 格式：`usage.prompt_tokens_details.cached_tokens`；Anthropic 格式：`usage.cache_read_input_tokens` / `cache_creation_input_tokens` | **≥512 tokens**（自动） | M3：命中 $0.12/M vs 输入 $0.60/M = **20%**；M2.7：$0.06 vs $0.30 = 20%；M2.5/M2.1：$0.03 vs $0.30 = 10%（显式写入 $0.375/M） |
| 7 | 阶跃星辰 Step | platform.stepfun.com | **自动** | 顶层 `usage.cached_tokens`（**注意：在顶层，不在 details 里**） | **≥256 tokens** | 缓存部分按该模型费用 **20%** 计费 |
| 8 | 百度文心 ERNIE | 千帆 ModelBuilder（qianfan.baidubce.com） | **自动**（默认开启，无需改代码） | `usage.prompt_tokens_details.cached_tokens` | 文档未给出固定值 | 命中按 prompt 单价 **40%** |

> ⚠️ 最容易踩坑的两点：
> 1. **DeepSeek 和 Step 的字段在 `usage` 顶层**（`prompt_cache_hit_tokens` / `cached_tokens`），其余六家在 `usage.prompt_tokens_details` 下——实验代码要同时兼容这两种形态。
> 2. **只有豆包是纯显式缓存**（需要创建缓存或传 `caching` 参数），其余六家自动缓存 + 千问可显式。不传任何参数就指望豆包返回 cached_tokens 是无效的。

---

## 二、逐家明细

### 2.1 DeepSeek（深度求索，api.deepseek.com）

- **官方文档**
  - API 参考（usage 结构）：https://api-docs.deepseek.com/api/create-chat-completion
  - 定价页（缓存命中价）：https://api-docs.deepseek.com/quick_start/pricing
- **缓存机制**：自动 context cache，无需任何配置；有"高峰期/非高峰期"分时定价（峰值=非峰值的 2 倍）。
- **usage 字段（官方 API 参考 schema 原文）**：DeepSeek 是**独立字段模型**——
  ```json
  "usage": {
    "completion_tokens": 10,
    "prompt_tokens": 16,            // = prompt_cache_hit_tokens + prompt_cache_miss_tokens
    "prompt_cache_hit_tokens": 0,   // ← 命中缓存的 token 数（顶层！）
    "prompt_cache_miss_tokens": 16, // ← 未命中缓存的 token 数（顶层！）
    "total_tokens": 26,
    "completion_tokens_details": { "reasoning_tokens": 0 }
  }
  ```
  - 官方定义原文："Number of tokens in the prompt that hits the context cache."
- **流式行为**：官方流式示例中，最后一个 chunk（`finish_reason=stop`）携带 `usage`；若设置 `stream_options.include_usage=true`，会在 `data: [DONE]` 前再补一个 `choices` 为空的 usage chunk；其余 chunk 的 `usage` 为 `null`。
- **计费折扣（官方定价页，单位 $/1M tokens）**：

  | 模型 | 输入(缓存命中) 峰值/非峰值 | 输入(缓存未命中) 峰值/非峰值 |
  |---|---|---|
  | deepseek-v4-flash | $0.014 / $0.007 | $0.44 / $0.22 |
  | deepseek-v4-pro | $0.044 / $0.022 | $1.32 / $0.66 |

  即命中≈未命中的 **1/31（≈3.2%）**，折扣力度为国内最大。峰值时段：UTC 周一至五 01:00–04:00 与 06:00–10:00。
  > 第三方报道（知乎，非官方）：DeepSeek V4-Pro 人民币口径"缓存命中 0.1 元/百万 vs 未命中 3 元/百万（差 30 倍），促销窗口 0.025 元"。此条为第三方转述，仅作参考。
- **实验要点**：读取顶层 `usage.prompt_cache_hit_tokens`；未命中时该字段为 `0`（官方示例即返回 0），不要把它当缺失。

---

### 2.2 Moonshot Kimi（platform.moonshot.cn / platform.kimi.com）

- **官方文档**
  - API 参考（Chat）：https://platform.kimi.com/docs/api/chat
  - 上下文缓存指南：https://platform.kimi.com/docs/guides/context-caching
  - 定价页：https://platform.kimi.com/docs/pricing/chat
- **缓存机制**：默认会自动缓存（官方指南："当请求包含相同前缀时自动缓存，无需手动调用；命中后自动续期"）。历史上曾公测显式缓存 API（`POST /v1/caching`，2024 年月之暗面文档），当前 platform.moonshot.cn API 列表已不含该端点，以自动缓存为准。
- **usage 字段（官方 API 参考示例原文）**：
  ```json
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 21,
    "total_tokens": 40,
    "cached_tokens": 10   // ← 命中缓存 token（顶层！官方示例原文）
  }
  ```
  官方《上下文缓存指南》（PDF）中的 usage 示例则为 `usage.prompt_tokens_details.cached_tokens`。**两处官方文档形态不一致**，实验时两个位置都要读。
- **请求参数**：官方请求体字段 `prompt_cache_key`（官方原文）——“用于缓存相似请求的响应以优化缓存命中率。对于 Coding Agent，通常是代表单个会话的 session id 或 task id；退出并恢复会话时应保持不变。对于 Kimi Code Plan，此字段为必填以提高缓存命中率。”不传时按前缀自动匹配。
- **流式行为**：官方指南明确"**流式返回时，最后一个 chunk 会携带 usage（含 cached_tokens）**"。
- **计费折扣（官方定价，$/1M）**：

  | 模型 | 输入(未命中) | 输入(命中) | 折扣 |
  |---|---|---|---|
  | kimi-k3 | $3.00 | $0.30 | **10%** |
  | kimi-k2.7-code | $0.95 | $0.19 | 20% |
  | kimi-k2.6 | $0.95 | $0.16 | ≈16.8% |
  | kimi-k2.5 | $0.60 | $0.10 | ≈16.7% |
  | moonshot-v1 系列 | — | 无 | 无缓存折扣 |

- **实验要点**：最后一轮流式 chunk 的 usage 是主战场；`cached_tokens` 与 `prompt_tokens_details.cached_tokens` 两个位置都要探测。

---

### 2.3 通义千问 Qwen（DashScope / 阿里云百炼）

- **官方文档**：阿里云百炼《上下文缓存（Context Cache）》https://help.aliyun.com/zh/model-studio/context-cache
- **缓存机制（OpenAI 兼容模式与原生 DashScope 模式已分别核实）**：
  - **隐式缓存（自动）**：对所有支持模型默认开启、不可关闭，按前缀匹配。OpenAI 兼容与 DashScope 均可命中。
  - **显式缓存（需主动开启）**：在 messages 的 content 中加 `"cache_control": {"type": "ephemeral"}`（仅此一种 type），从 messages 开头到标记位置创建缓存块；OpenAI 兼容、DashScope、Anthropic 兼容三种协议均支持。单次最多 4 个标记；向后回溯最近 20 个 content 块；最小缓存块 **1024 tokens**；有效期 **5 分钟（命中则重置）**。
- **usage 字段**：
  - OpenAI 兼容 · 隐式命中（官方示例原文）：
    ```json
    "usage": {
      "prompt_tokens": 3019,
      "completion_tokens": 104,
      "total_tokens": 3123,
      "prompt_tokens_details": { "cached_tokens": 2048 }
    }
    ```
  - OpenAI 兼容 / DashScope · 显式缓存：同时上报创建与命中（官方示例原文）：
    ```json
    // 第一次请求（创建缓存）      // 第二次请求（命中缓存）
    "cache_creation_input_tokens": 1605,   "cache_creation_input_tokens": 0,
    "cached_tokens": 0,                    "cached_tokens": 1605,
    // 均位于 usage.prompt_tokens_details 下
    ```
  - 原生 DashScope · 视觉模型海外地域（新加坡）：命中字段一度为顶层 `usage.cached_tokens`（文档注明"后续将升级至 `prompt_tokens_details.cached_tokens`"）；国内（北京）地域直接在 `usage.prompt_tokens_details.cached_tokens`。
  - Anthropic 兼容：`usage.cache_read_input_tokens`（命中，**不计入** `input_tokens`）、`usage.cache_creation_input_tokens`（创建）。
- **计费折扣（官方）**：
  - 隐式：命中 token 按输入标准价 **20%**（阿里百炼部署常规模型；`qwen3.8-max` 例外，以控制台为准）。
  - 显式：**创建**缓存 token 按标准输入价 **125%**；**命中**按 **10%**（qwen3.8-max 例外）。
- **触发阈值（官方）**：阿里云百炼部署模型的隐式缓存最少 **256 tokens**；Qwen3.7 系列约 **2000 tokens**。
- **实验要点**：多协议多形态是千问的特色——OpenAI 兼容/DashScope 看 `prompt_tokens_details`，Anthropic 兼容看 `cache_read_input_tokens`，部分海外地域 DashScope 看顶层 `cached_tokens`。本任务重点实验是 OpenAI 兼容 + 原生 DashScope 两种。

---

### 2.4 智谱 GLM（bigmodel.cn）

- **官方文档**：《上下文缓存》https://docs.bigmodel.cn/cn/guide/capabilities/cache
- **缓存机制**：**自动（隐式）缓存**，默认启用，无需手动配置；基于内容相似度/前缀自动触发。
- **usage 字段（官方原文）**："响应字段 `usage.prompt_tokens_details.cached_tokens`"——
  ```json
  "usage": {
    "prompt_tokens": …,
    "completion_tokens": …,
    "total_tokens": …,
    "prompt_tokens_details": { "cached_tokens": … }   // ← 命中缓存 token
  }
  ```
  官方示例代码取值方式：`response.usage.prompt_tokens_details.cached_tokens`（未命中时需判空/缺省为 0）。
- **计费折扣（官方）**：缓存命中 Token 按优惠价格计费，"**通常为标准价格的 50%**"；新内容按标准价、输出按标准价。GLM Coding Plan 套餐内积分抵扣口径（官方套餐页）：GLM-5.3 Input 系数 6.9 / Cached Input 系数 1.7（≈24.6%）。
- **有效期（官方）**："缓存有合理的时效性，过期后会重新计算"，未公布固定数值。
- **第三方口径**（阿里云百炼文档）：智谱部署的 GLM 触发隐式缓存最少 **512 tokens**；阿里云转售 GLM（ZHIPU/GLM-5.2 等）命中按 25%。
- **实验要点**：普通对话请求即可验证；同一 system 前缀连续请求看 `prompt_tokens_details.cached_tokens` 是否增长。

---

### 2.5 字节豆包 Doubao（火山方舟 volces.com）

- **官方文档**
  - 《上下文缓存（Context API）（待下线）》https://www.volcengine.com/docs/82379/1396491
  - 《上下文缓存》主文档 https://www.volcengine.com/docs/82379/1398933
- **缓存机制**：**仅显式缓存，无自动缓存**。两种 API：
  1. **Context API（待下线）**：先 `POST /api/v3/context/create` 创建缓存（`mode: "session"` 会话缓存 / `"common_prefix"` 前缀缓存，返回 `ctx-*` ID），再调用 `POST /api/v3/context/chat/completions`（请求体带 `context_id`）使用。TTL 可配，范围 1 小时–7 天（[3600,604800] 秒），未使用则过期、使用则重置。
  2. **Responses API（推荐）**：请求体传 `"caching": {"type": "enabled"}`（加 `"prefix": true` 为前缀缓存）创建 Session/前缀缓存，返回缓存 ID；后续用 `"previous_response_id": "<ID>"` 复用。过期时刻用 Unix 时间戳配置，最大当前时间 +604800 秒（7 天）。支持多模态与工具缓存、可手动删除任意缓存 ID。
  - 需在控制台「开通管理」→「推理(缓存)定价」开启缓存。
- **usage 字段（官方示例原文，Context Chat API 响应）**：
  ```json
  "usage": {
    "prompt_tokens": 28,
    "completion_tokens": 4,
    "total_tokens": 32,
    "prompt_tokens_details": { "cached_tokens": 18 }   // ← 缓存输入 token
  }
  ```
  创建缓存接口的响应同样带 `usage.prompt_tokens_details.cached_tokens`（首建时为 0）。
- **流式行为**：官方 SDK 示例用 `stream_options={"include_usage": True}` 后，chunk 的 `usage` 非空（含 cached_tokens）。
- **计费（官方）**：四类——新输入（标准价）；**缓存输入（折扣价，显著低于新输入）**；输出（标准价）；**存储费**（元/千 token/小时，按每自然小时缓存最大 token 量计，直到 TTL 到期或删除）。官方举例存储单价 0.000017 元/千 token/小时（示例值）；Doubao-1.5-pro-32k 示例缓存输入 1.6 元/千万 tokens。实际单价以《模型价格》页为准。
- **实验要点**：不传缓存参数直接调 `/chat/completions` 是**不会**返回缓存字段的（有自动 KV 缓存但不在 usage 中体现）；必须走 Context Chat API（`context_id`）或 Responses API（`caching`/`previous_response_id`）才能看到 `cached_tokens`。

---

### 2.6 MiniMax（platform.minimaxi.com 国内 / platform.minimax.io 海外）

- **官方文档**：https://platform.minimax.io/docs/api-reference/text-prompt-caching（国内域名为同一套文档：platform.minimaxi.com）
- **缓存机制**：两套并行——
  1. **自动缓存（被动 Prompt Caching）**：无需改调用方式。前缀匹配顺序为"工具列表 → 系统提示 → 用户消息"。有效期由系统按负载自动调整，命中则续期。
  2. **显式缓存（仅 Anthropic 兼容 API）**：在 content 中加 `"cache_control": {"type": "ephemeral"}`，**5 分钟 TTL，命中自动续期**；首次写入缓存有额外费用。
- **usage 字段**（OpenAI 格式官方示例原文）：
  ```json
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 300,
    "total_tokens": 1500,
    "prompt_tokens_details": { "cached_tokens": 800 }   // ← 自动缓存命中
  }
  ```
  Anthropic 格式（显式/自动均可出现）：
  ```json
  "usage": { "input_tokens": 108, "output_tokens": 91,
             "cache_creation_input_tokens": 0,          // 创建缓存（显式）
             "cache_read_input_tokens": 14813 }         // 命中缓存
  ```
- **触发阈值（官方）**：自动缓存适用于**输入 ≥512 tokens** 的请求。
- **计费折扣（官方 PayGo 示例）**：
  - MiniMax-M3：输入 $0.60/M，命中 $0.12/M（**20%**）；
  - MiniMax-M2.7：输入 $0.30/M，命中 $0.06/M（20%），显式写入 $0.375/M；
  - MiniMax-M2.5 / M2.1：输入 $0.30/M，命中 $0.03/M（10%），显式写入 $0.375/M。
- **支持模型**：自动缓存——M3 / M2.7 / M2.5 / M2.1 系列；显式缓存——M2.7 / M2.5 / M2.1 / M2 系列。
- **实验要点**：OpenAI 兼容接口看 `prompt_tokens_details.cached_tokens`；如果用 Anthropic 协议则看 `cache_read_input_tokens`。首次请求建立缓存（可能为 0），第二次请求读取。

---

### 2.7 阶跃星辰 Step（platform.stepfun.com）

- **官方文档**：《Prompt 缓存最佳实践》https://platform.stepfun.com/docs/zh/guides/developer/prompt-cache
- **缓存机制**：**自动**；请求超过 **256 tokens 时自动启用**，按 Prompt 前缀匹配。缓存淘汰采用 **LRU（最近最少使用）**，不设固定 TTL，高峰期缓存更容易被逐出。
- **usage 字段（官方示例原文）**——**顶层 `cached_tokens`，不在 details 里**：
  ```json
  "usage": {
    "cached_tokens": 512,      // ← 命中缓存 token（顶层！）
    "prompt_tokens": 591,
    "completion_tokens": 120,
    "total_tokens": 711
  }
  ```
  官方判定方法原文："如果 response.usage 存在 cached_tokens 字段，则表明该请求命中缓存，cached_tokens 的值即为命中的 Token 长度。"
- **流式行为**：官方 Web 搜索示例显示**每个流式 chunk 都带 usage（含 cached_tokens）**，与 OpenAI 惯例（仅末 chunk）不同，需多次读取。
- **计费折扣（官方）**：缓存部分 Token 按"**对应模型费用的 20%**"计费。
- **支持模型（官方）**：step-3.7-flash、step-3.5-flash、step-3.5-flash-2603、step-1o-turbo-vision 等（文档列出的系列）；其他模型暂不支持。
- **实验要点**：prompt 要 ≥256 tokens 才有缓存；命中读取顶层 `usage.cached_tokens`（注意与 DeepSeek 顶层字段不同名）。

---

### 2.8 百度文心 ERNIE（千帆 ModelBuilder）

- **官方文档**：《prompt cache 上线公告》https://ai.baidu.com/ai-doc/WENXINWORKSHOP/Rm6uq7jy9
- **缓存机制**：**自动**，对所有用户默认开启，无需修改代码（官方原文）。
- **usage 字段（官方响应示例原文）**：
  ```json
  "usage": {
    "prompt_tokens": 159,
    "completion_tokens": 89,
    "total_tokens": 248,
    "prompt_tokens_details": { "cached_tokens": 128 }   // ← 命中缓存 token
  }
  ```
  官方说明："当本次请求已命中缓存，usage 中返回 cached_tokens 字段……代表命中缓存的 token 数量。"（即未命中时该字段可能缺失。）
- **计费折扣（官方）**：命中缓存的 `cached_tokens` 按 `prompt_tokens` 单价的 **40%** 计算。模型示例：ERNIE-4.0-Turbo-8K 输入（命中）0.0012 元/千 tokens vs 输入（未命中）0.003 元/千 tokens，输出 0.009 元/千 tokens。
- **有效期（官方）**："系统将定期清理一段时间没有使用过的缓存"；官方同时明确"命中概率并不是 100%，即使上下文完全一致的请求也存在无法命中的概率"。
- **实验要点**：同一 prompt 连续请求（官方示例即为同样长文案换问题），观察 `prompt_tokens_details.cached_tokens`；未命中时字段可能缺失，需容错。

---

## 三、跨厂商对照（实验脚本设计要点）

### 3.1 字段位置差异（最重要）

| 厂商 | 命中字段完整路径 | 未命中时表现 |
|---|---|---|
| DeepSeek | `usage.prompt_cache_hit_tokens`（顶层，另有 `prompt_cache_miss_tokens`） | 返回 0 |
| Kimi | `usage.cached_tokens`（顶层）或 `usage.prompt_tokens_details.cached_tokens` | 文档两种示例并存 |
| Qwen（OpenAI/百炼） | `usage.prompt_tokens_details.cached_tokens`；显式另有 `cache_creation_input_tokens` | 未命中为 0/缺失 |
| Qwen（DashScope 海外部分模型） | `usage.cached_tokens`（顶层） | — |
| GLM | `usage.prompt_tokens_details.cached_tokens` | 缺失（官方例程判空） |
| 豆包 | `usage.prompt_tokens_details.cached_tokens` | 需要显式缓存才出现 |
| MiniMax | OpenAI 格式：`usage.prompt_tokens_details.cached_tokens`；Anthropic 格式：`usage.cache_read_input_tokens` / `cache_creation_input_tokens` | 首次请求命中可能为 0 |
| Step | `usage.cached_tokens`（顶层） | 未命中时无该字段（官方判定） |
| 百度千帆 | `usage.prompt_tokens_details.cached_tokens` | 缺失 |

**兼容读取建议**：统一读取器按以下优先级取值——
```
candidates = [
  usage.get("prompt_cache_hit_tokens"),                      # DeepSeek
  usage.get("cached_tokens"),                                # Kimi / Step / 部分 DashScope
  (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),  # 其余各家
  (usage.get("prompt_tokens_details") or {}).get("cache_read_input_tokens"),  # Anthropic 兼容
]
```

### 3.2 流式 usage 位置差异
- **DeepSeek**：末 chunk 或 include_usage 追加 chunk。
- **Kimi**：流式末 chunk 携带 usage。
- **Step**：每个 chunk 都可能带 usage。
- **豆包**：SDK 需 `stream_options={"include_usage": True}`。
- 其余（Qwen/GLM/MiniMax/千帆）：按 OpenAI 惯例，`stream_options.include_usage=true` 时末 chunk 带 usage；非流式直接看响应 usage。

### 3.3 缓存触发阈值
- Step：≥256 tokens；MiniMax：≥512 tokens；Qwen 隐式：≥256（部分模型更高）；全局建议：构造 **≥2048 tokens 的稳定前缀** 再测，避开各家阈值差异。

### 3.4 显式 vs 自动（决定实验脚本形态）
- 只发普通请求即可验证：DeepSeek、Kimi、Qwen（隐式）、GLM、MiniMax、Step、百度千帆。
- 必须额外走显式流程：**豆包**（先建缓存/传 caching 参数）；Qwen 如需显式命中（cache_control ephemeral，5 分钟 TTL、1024 tokens 起）也需加标记。

---

## 四、来源清单与确定性分级

| 事实 | 确定性 | 依据 |
|---|---|---|
| DeepSeek usage 顶层 `prompt_cache_hit_tokens/miss_tokens`；V4 缓存命中价（$0.014 vs $0.44 峰值） | 高（官方 API 参考与定价页原文） | https://api-docs.deepseek.com/api/create-chat-completion · /quick_start/pricing |
| Kimi `usage.cached_tokens`（顶层）；`prompt_cache_key`；流式末 chunk 带 usage；K3 命中 10%（$0.30/$3.00） | 高（官方指南/API/定价页；K3 价格亦有第三方 blog 复述一致） | https://platform.kimi.com/docs/api/chat · /docs/guides/context-caching · /docs/pricing/chat |
| Qwen OpenAI 兼容 & DashScope 隐式/显式缓存字段、1024 阈值、5min TTL、20%/10%/125% 计费 | 高（阿里云官方文档原文+示例 JSON） | https://help.aliyun.com/zh/model-studio/context-cache |
| GLM 自动缓存、`prompt_tokens_details.cached_tokens`、命中约 50% | 高（智谱官方）；智谱部署 512 阈值、25% 折扣为阿里云文档转述（中） | https://docs.bigmodel.cn/cn/guide/capabilities/cache |
| 豆包仅显式缓存、`prompt_tokens_details.cached_tokens`、TTL 1h–7d、Responses API caching 参数 | 高（火山方舟官方文档原文） | https://www.volcengine.com/docs/82379/1396491 · 1398933 |
| MiniMax 自动≥512、OpenAI/Anthropic 双字段、M3 命中 20%（$0.12/$0.60） | 高（官方文档）；RooCode issue 表格亦一致 | https://platform.minimax.io/docs/api-reference/text-prompt-caching |
| Step 自动≥256、顶层 `cached_tokens`、20% 计费、LRU | 高（官方文档原文） | https://platform.stepfun.com/docs/zh/guides/developer/prompt-cache |
| 百度千帆自动默认开启、`prompt_tokens_details.cached_tokens`、40% 计费 | 高（官方公告响应示例） | https://ai.baidu.com/ai-doc/WENXINWORKSHOP/Rm6uq7jy9 |
| DeepSeek V4-Pro 人民币缓存价（0.1 元 vs 3 元/百万） | 低-中（仅第三方知乎转述，非官方） | 第三方报道 |
| Kimi 命中价逐模型数值 | 中（官方定价页为主，第三方 blog 佐证） | platform.kimi.com pricing + 第三方 blog |

**验证状态**：以上均为**官方文档调研结论**，尚未做真实 API 请求实测。建议下一步按第三节要点构造实验脚本逐家验证字段如实返回。