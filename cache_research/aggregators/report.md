# 聚合层调研报告：聚合 API / 中转服务 / coding plan 的「缓存命中 token」字段透传情况

- 撰写时间：2026-08-29
- 调研人：子智能体 #3（聚焦聚合层）
- 配套调研（其他子智能体负责）：官方海外 API（OpenAI/Anthropic/DeepSeek 等）、官方国内 API
- **重要说明**：本领域大量结论来自 GitHub issue、论坛/社区讨论而非官方文档。每条结论都标注了证据等级：
  - **官方文档**：服务方官方文档/博客
  - **官方源码**：服务方开源仓库源码（本文直接读取了 new-api 的 `relay/channel/openai/helper.go`）
  - **Issue 讨论**：GitHub issue / 论坛讨论（含用户实测）
  - **第三方调研**：独立第三方测评/文档（如 cuihuan/awesome-ai-gateway 的逐 commit 源码审查）
  - **社区讨论**：LINUX DO、Cursor 论坛、Reddit 等社区帖子
  - **推测**：无直接证据，基于已有事实的合理推断；此类结论已明确标注「推测」
  - 未找到明确证据的，一律写「未找到证据」。

---

## 1. 总览对照表

| 服务 | 是否透传/保留缓存字段 | 字段格式 / 重命名情况 | 流式中的表现 | 计费显示 | 证据等级 | 来源 |
|---|---|---|---|---|---|---|
| **OpenRouter** | ✅ 保留并**统一规范化**为 OpenAI 风格 | `usage.prompt_tokens_details.cached_tokens` + 自有扩展 `cache_write_tokens`、`cache_discount`、`cost`、`cost_details` | 需 `stream_options.include_usage=true`；末 chunk 带回 usage（官方格式）；**OpenRouter 自身的响应缓存 HIT 时 usage 全为 0** | ✅ `usage.cost` 会按缓存读取折扣计价；`cache_discount` 表示本 generation 的缓存折扣；Activity 页与 `/api/v1/generation` 可查 | 官方文档 | [OpenRouter chat completion 文档](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)、[Prompt Caching 教程博客](https://openrouter.ai/blog/tutorials/prompt-caching-sticky-routing)、[Response caching 文档](https://openrouter.ai/docs/guides/features/response-caching) |
| **opencode（开源 agent）** | 客户端**解析**用法字段（含缓存），但 TUI 默认不显示 | `session.tokens_cache_read` / `info.tokens.cache.read` | 已知 bug：OpenAI-compatible 流式路径下 `tokens_cache_read` 恒为 0（上游明明返回了 `cached_tokens`），#33997 | opencode 内部按模型计费；TUI 不展示缓存明细（有多个第三方插件补足） | 官方源码(基于 issue 定位) + Issue 讨论 | [anomalyco/opencode#33997](https://github.com/anomalyco/opencode/issues/33997)、[#34296](https://github.com/anomalyco/opencode/issues/34296)、[#13003](https://github.com/anomalyco/opencode/issues/13003) |
| **opencode Zen（PAUG 网关）** | 见下；同时提供 OpenAI 兼容 / Anthropic 兼容 / Gemini 兼容端点；**官方价格表单独列出 Cached Read / Cached Write 两列**（按模型计费，说明其必然解析上游缓存字段） | 端点协议原生格式（`v1/chat/completions` 走 OpenAI 格式，`v1/messages` 走 Anthropic 格式） | 未找到官方对流式 usage 的专门描述 | ✅ 官方按 Cached Read/Write 定价 | 官方文档（价格表）+ 第三方（Bifrost 文档，见下） | [opencode.ai/docs/zen](https://opencode.ai/docs/zen)、[docs.getbifrost.ai OpenCode 页](https://docs.getbifrost.ai/providers/supported-providers/opencode) |
| **opencode Go（订阅）** | 见下；「opencode go」= OpenCode Go 订阅服务（$5 首月/$10 每月），**不是**「Go 语言版本」 | 同上 | 同上 | 订阅制，固定月费 + 用量限额，**不按缓存计费** | 官方文档 | [opencode.ai/docs/go](https://opencode.ai/docs/go)、[opencode.ai/zh/go](https://opencode.ai/zh/go) |
| **one-api（songquanpeng）** | 大体透传上游 OpenAI 格式 usage；**计费模型不含缓存折扣**（`额度 = 分组倍率 × 模型倍率 × (提示 token + 补全 token × 补全倍率)`） | OpenAI 风格（其主干只做 OpenAI 兼容转发） | 依赖 `stream_options.include_usage`（README 中有可选 env `ENFORCE_INCLUDE_USAGE`） | ❌ 计费不区分缓存命中；缓存 token 按全价输入计 | 第三方调研（逐 commit 源码审查）+ 官方 README | [awesome-ai-gateway virtual-keys-metering](https://github.com/cuihuan/awesome-ai-gateway/blob/main/docs/virtual-keys-metering.zh-CN.md)、[one-api README](https://github.com/songquanpeng/one-api) |
| **new-api（QuantumNous）** | ✅ 转发路径基本保留缓存字段（OpenAI 渠道流式 `*usage = lastStreamResponse.Usage` 整体拷贝）；**但存在多个已证实的 bug**：自定义渠道/火山方舟流式把 `cached_tokens` 打成 0（#5672）；xAI 渠道流式转发对但内部计费 usage 损坏（#6144）；缓存命中导致输入 token 变负数（#5003/#5005）；缓存写入 token 未计费（#6353） | OpenAI 风格 `prompt_tokens_details.cached_tokens`；清理/重建 usage 时会注入大量默认字段（`text_tokens/audio_tokens/claude_cache_creation_*` 等） | 多个渠道的流式 usage 处理有 bug（见上）；「透传模式」直连上游→字段原样 | ⚠️ 内部计费有 `CacheRatio` + `CacheCreationRatio`（5m/1h 拆分），但多个 bug 导致缓存计费错误甚至倒扣 | 官方源码 + Issue 讨论 + 第三方调研 | new-api#6144、#5672、#5003、#6353；源码 `relay/channel/openai/helper.go`；awesome-ai-gateway 文档 |
| **one-hub（MartialBE）** | ✅ 基本透传；**曾被证实 Responses API 的 `cached_tokens` 因 `omitempty` 标签被省略**，导致 Codex CLI 报 `missing field 'cached_tokens'`，已修复（PR #910） | OpenAI 风格 | Responses SSE 的 `input_tokens_details.cached_tokens` 曾缺失（已修复） | v0.14.26 起为 Bedrock 渠道的 Claude 增加 prompt caching 支持；计费沿用 one-api/new-api 体系 | Issue/PR 讨论 + Release 说明 | [one-hub PR #910](https://github.com/MartialBE/one-hub/pull/910)、[Release v0.14.26](https://github.com/MartialBE/one-hub/releases) |
| **国内中转站（packycode、灵眸AI 等）** | 参差不齐：宣称「官转」的站会解析并透传 usage（packycode 明说「透传用户的请求…解析 claude 传来的 usage tokens」）；部分站（逆向接口）不缓存 | Anthropic 原生格式（Claude Code 场景）或 OpenAI 风格 | 实测有的站「完整透传 `cache_creation_input_tokens` / `cache_read_input_tokens`」（灵眸AI） | ⚠️ 中转站按 usage 计费，且**默认按 5m Cache Write 计缓存**（packycode）；缓存命中占比极高（用户实测 82.9% cache read） | 社区讨论 | LINUX DO 帖、fulitimes 博客，见 §5 |
| **GitHub Copilot** | 终端用户**拿不到 per-request usage**（订阅制）。订阅用量属 token 配额制（2026-06 起转 token 计费）；企业版 REST metrics API 只给每日聚合 `prompt_tokens_sum/output_tokens_sum`，**无缓存拆分**；VS Code 的 OTLP 指标不暴露 cached input | 其内部 OpenAI 兼容后端 SSE **会**把 `prompt_tokens_details.cached_tokens` 与 DeepSeek 原生 `prompt_cache_hit_tokens` 透给客户端（社区实测，free 计划 DeepSeek） | 同上（社区实测见原始 SSE） | 订阅/token 配额内，无 per-request 缓存折扣展示 | 官方文档 + Issue/社区实测 | GitHub REST Copilot metrics 文档、microsoft/vscode#317837、obsidian-copilot discussion #2380 |
| **Cursor** | 订阅与 BYOK 的用量面板都**展示 Cache Read / Cache Write**（官方客服口径：usage 报告里显示的是「AI provider 随响应返回的精确 token」）；BYOK 直连时缓存字段来自 Anthropic/OpenAI | Anthropic/OpenAI 原生 | 多个论坛帖证实 Auto 模式曾路由到不支持缓存的模型导致 cache=0（版本问题） | ✅ 面板单列 Cache Read/Write 并计费（cache read 价约输入价 10%） | 社区讨论（官方客服回复）+ 官方文档未直接确认 | Cursor 论坛帖，见 §6 |
| **Windsurf** | 订阅/credits 制；**计量按 token 且明确区分 cache-read 单价**（如 Sonnet：input 90 credits/M、cache read 9 credits/M、output 450 credits/M），说明网关侧跟踪缓存 token | 不暴露原始 usage 给用户，走 credits 换算 | 未找到 per-request usage 暴露证据 | ✅ cache-read 以低价 credit 计费 | 第三方文档 + 官方价格说明 | flexprice.io、Windsurf 官方文档（见 §6） |
| **Augment Code** | token 计费制；官方文档明说「自动缓存稳定上下文，cached input 按供应商缓存价（约 10%）计费」，Usage 面板展示 input/output/cache read/cache write 单价 | 不暴露原始 usage 字段 | 未找到 | ✅ 缓存读取按折扣计费 | 官方文档 | [docs.augmentcode.com/models/token-based-pricing](https://docs.augmentcode.com/models/token-based-pricing) |
| **Cloudflare AI Gateway** | 作为透明代理转发（推测透传 usage）；**官方文档未明确描述缓存 usage 字段的保留/规范化**；其自带「响应缓存」是网关级缓存（`cf-aig-cache-status: HIT/MISS`），与 prompt cache 是两回事；社区实测 `cache_control` 请求体能透传 | 上游协议原样 | 未找到官方文档 | 网关自己的日志/analytics 记录 token usage 供计费统计，不向调用方展示 | 官方文档（缓存功能）+ Issue 讨论（cache_control 透传） | Cloudflare AI Gateway docs、openclaw#46709 |
| **Portkey** | ✅ **明确规范化到 OpenAI 格式并保留缓存字段**：`prompt_tokens = input + cache_read + cache_creation`，`cached_tokens` 出现在 `prompt_tokens_details`|（Bedrock 场景有明确文档） | Portkey 透传模式下响应按供应商原样；其观测端展示 `cached_tokens` | ✅ 定价公式单独处理 base input / cache read / cache write | 官方文档 | [Portkey Bedrock Prompt Caching](https://docs.portkey.ai/docs/integrations/llms/bedrock/prompt-caching)、[Portkey docs](https://docs.portkey.ai/docs/integrations/llms/openai/prompt-caching-openai) |
| **LiteLLM** | ✅ OpenAI 兼容端点规范化到 OpenAI 风格 `prompt_tokens_details.cached_tokens`，同时在同一 usage 对象中保留 Anthropic 原生 `cache_creation_input_tokens` / `cache_read_input_tokens`；**但 Anthropic `/v1/messages` 透传路径不把原生字段映射到 `cached_tokens`，导致指标/计费不识别缓存（bug #27763）** | 双格式并存（OpenAI 风格 + Anthropic 原生） | 流式 usage 合成有历史 bug（如 synth chunk 的 `choices` 非空）；默认不强制 include_usage | ⚠️ 有独立 cache read/write 单价，但多个计费 bug：缓存 token 按全价算（#26807，多收 1.67×）、cache write 未计入（#33772）等 | 官方文档 + Issue 讨论 + 第三方调研 | litellm docs Prompt Caching、#27763、#26807、#33772、awesome-ai-gateway |
| **Vercel AI Gateway（顺带）** | 面板正确展示 cache read，但**缓存 token 按全价输入计费**（Kimi 案例 6× 成本） | 上游协议原样 | 未细查 | ⚠️ 计费不应用缓存折扣（issue 讨论） | Issue 讨论 | [vercel/ai#13907](https://github.com/vercel/ai/issues/13907) |

---

## 2. OpenRouter（重点）

**结论先行**：OpenRouter 是少数把「缓存命中 token」做成**一等公民**的聚合层——它把各上游（Anthropic/OpenAI/Gemini/DeepSeek…）的缓存字段**统一规范化**成 OpenAI 风格的 `prompt_tokens_details.cached_tokens`，并增加自有扩展字段 `cache_write_tokens`（缓存写入）与 `cache_discount`（本次缓存折扣金额）。

### 2.1 usage 字段是否原样透传 / 规范化成什么
- 官方 API 参考（`ResponseUsage` 类型）：
  - `usage.prompt_tokens` / `completion_tokens` / `total_tokens`
  - `usage.prompt_tokens_details.cached_tokens`（"Tokens cached by the endpoint"）+ 可选 `cache_write_tokens`（"Tokens written to cache (models with explicit caching)"）
  - 另有 `completion_tokens_details.reasoning_tokens`、`cost`、`cost_details`（含 `upstream_inference_prompt_cost` 等）、`is_byok`、`server_tool_use_details` 等 OpenRouter 扩展。
- 官方示例：`"usage": { "prompt_tokens": 10339, "completion_tokens": 60, "total_tokens": 10399, "prompt_tokens_details": { "cached_tokens": 10318, "cache_write_tokens": 0 } }`。
- 也就是说：**Anthropic 的 `cache_read_input_tokens` 会被折算进 `cached_tokens`**（并参与折扣计费）。OpenRouter 官方博客明确说明：缓存读取价格约为正常输入价的 0.1×–0.5×（Anthropic/DeepSeek/Qwen 0.1×，OpenAI 0.25×–0.5×……）。
- 没有找到 OpenRouter 会把 Anthropic 原生 `cache_creation_input_tokens` 原样透传的证据——它统一到 OpenAI 风格。OpenRouter 自己的扩展字段就叫 `cache_write_tokens`。

### 2.2 流式响应
- 与 OpenAI 相同：需 `stream_options: { include_usage: true }`，最后一个 SSE chunk 带 `usage`（官方博客称**每个响应都包含** `usage.prompt_tokens_details` 的 `cached_tokens`/`cache_write_tokens`）。
- 注意：OpenRouter 官方「Response caching（响应缓存）」是**另一回事**——它缓存的是整条响应（`X-OpenRouter-Cache-Status: HIT/MISS` 头）；**HIT 时返回的 usage 是 `prompt_tokens: 0, completion_tokens: 0, total_tokens: 0`**（官方文档示例）。实验时不要把「OpenRouter 响应缓存」当成「prompt cache」。

### 2.3 计费显示
- `usage.cost` 体现缓存折扣后的实际金额；`usage.cost_details` 细分上游各项成本；`cache_discount` 表示本 generation 因缓存省下/付出的金额（写入缓存的那一轮可能为负折扣，因为写缓存更贵）。
- Activity 页面与 `GET /api/v1/generation` 可逐条查看 `cached_tokens` / `cache_write_tokens` / `cache_discount`。
- 社区实测（2026-07，china-llm.com）：GLM-5 经 OpenRouter 重复调用返回 3200 cached tokens、价格降 75%；同时**同一前缀 DeepSeek 经 OpenRouter 报 0 cached tokens**（原生端点几分钟内有 98% 命中）——**说明 OpenRouter 某些模型/上游不保留缓存，不能一概而论**。Paul's Programming Notes 也实测 Kimi K3 的缓存折扣「过不了 OpenRouter」。
- 第三方安全测评（Tarun Chitra 文章）指出：存在供应商「把缓存 token 按全额重新计价」的多收费现象，OpenRouter 本身对上游的缓存识别并不总是生效——意味着 **`cached_tokens` 字段是否存在、是否 >0，可作为判断上游是否真正给了缓存折扣的观测点**。

**证据等级**：缓存字段设计=官方文档；折扣细节=官方博客；个别模型缓存不过网关=第三方实测；上游「repricing」问题=第三方文章。

### 2.4 来源
- https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion （官方，ResponseUsage 定义/示例）
- https://openrouter.ai/blog/tutorials/prompt-caching-sticky-routing （官方，缓存字段与折扣）
- https://openrouter.ai/docs/guides/features/response-caching （官方，响应缓存 HIT 时 usage 归零）
- https://china-llm.com/blog/openrouter-prompt-caching （第三方实测，2026-07-28）
- https://www.paulsprogrammingnotes.com/2026/08/kimi-k3-cache-discount-openrouter.html （第三方实测）

---

## 3. opencode / opencode Zen / opencode Go

### 3.1 先说清楚「opencode go」是什么（任务要求查清）
- `opencode`（sst/opencode，现仓库 `anomalyco/opencode`，作者 Anomaly，前 SST 团队）是**用 Go 写的开源 terminal coding agent**（MIT）。
- **「opencode go」= OpenCode Go**，是 Anomaly 推出的**低价订阅服务**（首月 $5，之后 $10/月），提供一批开源/开源权重 coding 模型（Kimi、GLM、MiniMax、DeepSeek、Qwen、Grok、GPT-5.6 Luna 等）。**它不是「Go 语言版本的 opencode」，而是「一个叫 Go 的订阅套餐」**。它诞生背景是 Anthropic 2026-01 禁止第三方工具使用 Claude 订阅凭据后，Anomaly 顺势推出的三个订阅产品之一：**Go（$10/月开源模型）**、**Zen（按量付费网关）**、Black（企业网关）。
- 官方描述：Go 是面向国际用户的低成本订阅，通过 OpenAI 兼容 / Anthropic 兼容端点提供（Docker 文档确认：`openai_chatcompletions`，base URL 为 opencode.ai 的 Go 端点；MiniMax/Qwen 等走 Anthropic 客户端）。**订阅制=固定月费+用量限额，不按 token/缓存计费**，因此对「缓存命中计费」不敏感——用户看不到用量明细。
- Zen 才是按量付费：`https://opencode.ai/zen/v1/chat/completions`（OpenAI 兼容）、`/v1/messages`（Anthropic 兼容）、`/v1/responses`（OpenAI Responses）、Gemini 风格端点。

### 3.2 Zen 是否保留/计费缓存字段
- **官方价格表（opencode.ai/docs/zen）对每个模型单独列出 `Cached Read` 和 `Cached Write` 两列单价**（如 MiniMax M3：Input $0.30/M、Output $1.20/M、Cached Read $0.06/M；Claude Sonnet：Cached Read $0.20/M、Cached Write $2.50/M；Qwen 3.7 Plus：Cached Read $0.04、Cached Write $0.50）。**既然按缓存读取/写入单独定价，Zen 网关必然解析上游响应里的缓存 usage 字段**——这是「Zen 保留缓存字段」的最强官方证据（间接）。
- 第三方佐证——Bifrost 的 OpenCode provider 文档（docs.getbifrost.ai，Bifrost 用同一套 OpenCode Zen/Go provider 实现）：
  - OpenCode 返回 `usage.prompt_tokens` / `usage.completion_tokens` / `usage.total_tokens` / **`usage.prompt_tokens_details.cached_tokens`** / `usage.completion_tokens_details.reasoning_tokens`。
  - 「有些模型上报 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，Bifrost 会把这些映射成标准 `cached_tokens` 参与定价计算」。
  - 「缓存行为取决于底层供应商；有的模型（如 Go 上的 DeepSeek V4 Flash）可能根本不缓存」。
- opencode 客户端侧：`packages/llm/src/protocols/openai-chat.ts` 的 `mapUsage` 会映射 `prompt_tokens_details.cached_tokens`（issue #33997 里确认）；会话级字段 `session.tokens_cache_read`、`info.tokens.cache.read`。**但存在一个已知 bug：OpenAI-compatible（自定义 baseURL，如 LiteLLM 代理）流式路径下 `tokens_cache_read` 恒为 0，即使上游 SSE usage chunk 里明明有 `cached_tokens`（实测 5888/6004 ≈98% 命中）**（#33997，2026-06）。即：**opencode 客户端本身对流式缓存的解析有坑，实验者别只看 opencode 的展示值**。
- opencode TUI 默认不展示缓存明细——有多个第三方插件补足（opencode-visual-cache、opencode-cache-hit、oc-plugin-caching），说明多模型（含 Zen）返回的缓存 usage 是**可达**的（插件从 opencode session API 读 `tokens`/`cost`）。

**回答五个问题**：
1. 透传？— Zen/Go 后端按协议原生透传（表单即 OpenAI/Anthropic 兼容格式）；opencode 客户端解析 `cached_tokens`（OpenAI 兼容路径有流式 bug）。
2. 规范化？— 未见 Zen 官方文档说明是否统一改名；Bifrost 实现会把 `prompt_cache_hit_tokens` 映射成 `cached_tokens`。未找到 Zen 对 Anthropic 端点的缓存字段改名证据（推测为 Anthropic 原生格式透传）。
3. 流式？— opencode #33997 证实上游流式 chunk 带 `cached_tokens`，但 opencode 展示为 0（客户端 bug）。
4. 计费？— Zen 按 Cached Read/Write 单价收费（官方价格表）；Go 订阅制不按缓存计费。
5. 来源 — 见下。

来源：https://opencode.ai/docs/zen、https://opencode.ai/docs/go、https://opencode.ai/zh/go、https://docs.getbifrost.ai/providers/supported-providers/opencode、https://github.com/anomalyco/opencode/issues/33997、#13003、#23109、#34296、https://ai.miraheze.org/wiki/OpenCode_Go（第三方介绍）、https://thomas-wiegold.com/blog/opencode-go-review（第三方评测）

---

## 4. 开源自建中转网关：one-api / new-api / one-hub

### 4.1 one-api（songquanpeng）
- 主干是「OpenAI 兼容格式 `chat/completions` 转发」，上游 OpenAI 协议响应的 usage 基本原样转发；但**计费模型完全没有缓存折扣**：官方 FAQ 的额度公式 = 分组倍率 × 模型倍率 ×（提示 token 数 + 补全 token 数 × 补全倍率）。
- 第三方逐 commit 源码审查（cuihuan/awesome-ai-gateway, 2026-07-29）结论：
  - one-api 的 `quota = ceil((promptTokens + completionTokens*completionRatio) * ratio)`，**全仓没有 cache read/write 单价**（六网关对比中 one-api/Kong/Higress 是仅有的三家无独立缓存价格的）。
  - one-api 最新 commit 停留在 2025-02-21（v0.6.10），计量代码比 new-api 旧约 17 个月；流式 usage 缺失时用 tiktoken 兜底重算（但仅对 gpt-3.5/4 前缀建了真编码器，Claude/Gemini 流会退回 gpt-3.5-turbo 编码器）。
  - 用户视角：**客户端拿到的 usage 里缓存字段大概率保留（OpenAI 渠道），但账单不会给缓存折扣**。
- 流式：README 有可选环境变量 `ENFORCE_INCLUDE_USAGE`（是否强制在 stream 下返回 usage）。
- 未找到 one-api 专门讨论 cached_tokens 透传的 issue（搜索「one-api cached_tokens」无直接命中；其 issue #204 是登录 token 缓存导致额度超额，与 prompt cache 无关，不采用）。

**结论**：one-api = usage 大体透传、缓存字段保留与否取决于客户端是否请求 include_usage；**计费无缓存折扣**。证据等级：第三方源码审查 + 官方 README。

### 4.2 new-api（QuantumNous，one-api 的主要活跃 fork，包装「官转」最多的底座）
- **转发路径**：OpenAI 渠道流式 `handleLastResponse` 里 `*usage = lastStreamResponse.Usage`（**整体拷贝**，`cached_tokens` 保留）——本文直接读取源码 `relay/channel/openai/helper.go` 确认。非流式 `xAIHandler` 直接 `return xaiResponse.Usage, nil`。
- **但有一批已证实的 bug（全部为 issue 讨论 + 部分有源码定位）**：
  1. **#6144（xAI 渠道）**：流式 handler 双路径分叉——转发给客户端的 usage 是完整的（`cached_tokens=1792` 正确返回），但内部计费用的是手动重建的残缺 usage（只拷 3 个标量），`cache_tokens` 记成 0，缓存 token 全按全价计费。非流式正常。已提交修复 PR #6145（`*usage = *xAIResp.Usage` 整体拷贝）。**「客户端看到的 usage 是对的，网关自己计费是错的」的典型例子**。
  2. **#5672（自定义渠道/火山方舟）**：流式模式下 `usage.prompt_tokens_details.cached_tokens` 恒为 0，`prompt_tokens` 从 3513 膨胀到 4540（+29%），`reasoning_tokens` 被清零，并被注入大量默认字段（`text_tokens:0, audio_tokens:0, claude_cache_creation_*:0` 等）。非流式正常。已关闭（not planned）。
  3. **#5003 / #5005（缓存命中→输入 token 为负数）**：上游按 Anthropic 排除语义返回（cache read 已从输入中排除），new-api 又减了一次，输入算出 −16,638，账单反而「倒贴」给用户（第三方文档给出可复算算术）。重视用户实测「站长亏损」。
  4. **#6353（Claude 缓存写入 token 未计费）**：5m/1h TTL 拆分缺席时级联 bug 把 cache creation 值清零，最贵的写入 token 打了 100% 折。开放中。
  5. **#1103（Gemini reasoning 未计费，开放 14 个月）**：`completion_tokens`（124）不含 `reasoning_tokens`（1097），90% 输出 token 未计费（属推理字段，非缓存，顺带记录）。
- **透传模式**：new-api 的 issue 模板明确写「透传模式会直接转发请求，请自行确认上游行为；开启透传后的转发相关反馈不接受 issue」→ **存在「透传（直连上游）」开关，开启后缓存字段随上游原样返回**；反之普通中继模式会走上面的 usage 规范化逻辑（可能补默认字段、改计数）。
- **计费**：`service/text_quota.go`（OpenAI 语义）`promptQuota = (PromptTokens - CacheTokens) + CacheTokens * CacheRatio`，并有 `CacheCreationRatio`（5m/1h 拆分）——**new-api 是少数原生支持缓存折扣计费的开源网关**，但 bug 多。

**结论**：new-api「会」保留缓存字段（多个渠道/修复后），但**流式+自定义渠道/部分内置渠道历史上会丢/损坏缓存字段或计费错误**；实验透过 new-api 必须同时看「客户端收到的 usage」与「网关消费日志/账单」两处。证据等级：官方源码（helper.go + issue 中源码定位）+ issue 讨论 + 第三方调研（awesome-ai-gateway）。

### 4.3 one-hub（MartialBE，one-api 的另一活跃 fork）
- 与 new-api 同源（都 fork 自 one-api）；能力上对齐 new-api 的缓存计费方向（README 称「支持更多模型」）。
- **直接证据：PR #910（2026-01，由 done-hub 转来）——「修复 Responses API cached_tokens 字段缺失问题」**：原代码对 `ResponsesUsageInputTokensDetails.CachedTokens` 用了 `omitempty` 标签，**值为 0 时字段被省略**，导致 Codex CLI 解析 `response.completed` 事件时报 `missing field 'cached_tokens'` 并无限重试。修复=移除 omitempty 保证零值也输出。→ **说明网关在 Responses 路径会把 `cached_tokens` 弄丢（至少历史版本）**。
- Release v0.14.26：「为通过 AWS Bedrock 渠道访问的 Claude 模型添加 prompt caching 支持」（PR #850）→ one-hub 主动做缓存透传/支持。
- 计费沿用 one-api/new-api 体系（new-api 特性 `CacheRatio` 等是否完全同步需逐个版本核对，未找到独立证据）。

**结论**：one-hub 基本透传，但历史上有 Responses API 丢 `cached_tokens` 的 bug 并已修复；实验者用 Codex Responses 端点时建议对照上游原始响应。证据等级：PR 讨论 + Release 说明。

### 4.4 来源汇总
- https://github.com/songquanpeng/one-api （README：额度公式、ENFORCE_INCLUDE_USAGE）
- https://github.com/QuantumNous/new-api/issues/6144 、#5672 、#5003 、#5005 、#6353 、#1103
- https://raw.githubusercontent.com/QuantumNous/new-api/main/relay/channel/openai/helper.go （源码）
- https://github.com/MartialBE/one-hub/pull/910 、https://github.com/MartialBE/one-hub/releases （v0.14.26）
- https://github.com/cuihuan/awesome-ai-gateway/blob/main/docs/virtual-keys-metering.zh-CN.md （第三方逐 commit 审查，2026-07-29；含上述 issue 的状态核实与可复算算术）

---

## 5. 国内常见中转/拼车 API 站（packycode、灵眸AI 等）与缓存计费讨论

### 5.1 packycode（PackyAPI，自称「官转」）
- LINUX DO 官方商家帖（2025-07）：「Packycode 的计费保持和官网的 api 计费方式一样」「**我们会透传用户的请求（保护隐私），最后解析 claude 传过来的 usage tokens，我们默认使用 5m Cache Writes 做 cache 的计费**」——**明说基于上游 usage 计费、缓存按 5m cache write 计费**。同时有用户问「Claude code 拼车的时候，背后是 Claude code 的池子，不会没有办法命中 cache 吗」——官方回复大意：全局用 Claude Code 的话缓存命中由 Claude Code 自管，实际消耗不大。
- GitHub 宣传页（2026）：PackyAPI 主站按量付费、计费对标 Claude/OpenAI 官网价格；Codex 有独立包月站。
- 用户实测（什么值得买/其他帖）：Claude Code 场景 cache read 占输入大头（另一帖统计 82.9% cache read / 15.6% cache write / 1.5% fresh input）；**cache 命中基本决定中转实际价格**。

### 5.2 灵眸AI 等（社区实测透传）
- fulitimes 博客（2026，Claude Code 缓存指南）：「实测灵眸AI **完整透传** `cache_creation_input_tokens` 和 `cache_read_input_tokens` 这两个字段，可在后台账单中查看每次请求的 cache 命中情况」；并警告「**很多便宜平台用逆向接口，不支持 Prompt Caching**——表面价低但无缓存差距」；验证方法＝在响应 usage 里查这两个字段是否存在。
- 知乎/博客普遍教程：判断中转是否支持缓存的唯一方法是看响应 usage 里有没有 `cache_creation_input_tokens` / `cache_read_input_tokens`（Anthropic 风格）。说明**社区已把「usage 缓存字段是否透传」当作中转站质量的验收标准**。

### 5.3 结论（针对四个问题）
1. 是否透传缓存字段：**参差不齐**。口碑「官转」站大多解析上游 usage 并据此计费（packycode 明说，灵眸AI 实测透传）；逆向/低价接口通常无缓存。**没有统一规范**。
2. 规范化/改名：一般保持上游协议原生（Claude Code 场景=Anthropic 原生字段；OpenAI 兼容场景=OpenAI 风格）。
3. 流式：Claude Code 流式 usage 走 Anthropic `message_start`/`message_delta`；有 issue 表明 Claude Code 类客户端对 messageDelta 里的缓存计数有兼容问题（cline#4346 讨论 Anthropic API 在 messageDelta 增加累计缓存计数的兼容问题）。
4. 计费显示：中转站按解析后的 usage 计费并**普遍把缓存写入按 5m 档定价**（1.25×输入价），缓存读取按 0.1×；用户可看到余额消耗，部分站（如灵眸AI）后台可查 cache 命中明细。
- 证据等级：除 GitHub 宣传页外几乎全部为社区讨论/用户实测（无官方文档）。**未找到「中转站统一丢弃缓存字段」的系统性证据**；相反，多个实测表明主流中转会透传。

来源：
- https://linux.do/t/topic/771392 （Packycode 计费说明帖）
- https://linux.do/t/topic/1620430 （cache read 占比 82.9% 实测）
- https://linux.do/t/topic/2591545 （Sub2API 中转 Claude Code 消耗统计）
- https://blog.fulitimes.com/claude-code-cost-optimization （灵眸AI 透传实测、逆向接口无缓存）
- https://github.com/CherryHQ/cherry-studio/discussions/15278 （Feiyuan API「原生透传 cache_control」的站长自述，Claude 中转缓存讨论）
- https://github.com/cline/cline/issues/4346 （Anthropic messageDelta 缓存计数的客户端兼容问题）

---

## 6. 订阅制 coding plan：GitHub Copilot / Cursor / Windsurf / Augment Code

统一先回答「是否向终端用户暴露 token usage/缓存信息」：**多数不暴露原始 per-request usage，但 Cursor/Augment 等会在用量面板里展示缓存拆分明细；Copilot/Windsurf 只给聚合/credit 换算后的信息**。

### 6.1 GitHub Copilot
- 经典订阅制（token 配额）：用户拿不到 per-request usage。2026-06 起逐步转 token 计费（Medium/官方博客）。
- 企业版提供 REST Copilot usage metrics API（enterprise/org 级）：返回**每日聚合**的 `prompt_tokens_sum`、`output_tokens_sum`、`avg_tokens_per_request` 等，**没有缓存 token 拆分字段**（官方文档示例可见）。→ 官方聚合指标里**看不到 cached tokens**。
- VS Code 内 OTLP 指标：microsoft/vscode#317837 确认 **Copilot Chat 的 OTLP metrics 不暴露 cached input token usage**；但 GitHub 定价区分 normal input 与 cached input（说明**平台侧在按缓存计费**，只是不暴露给用户）。
- 有趣的实证：obsidian-copilot 的讨论（#2380）贴出免费 Copilot 计划（DeepSeek v4）的**原始 SSE**——`usage.prompt_tokens_details.cached_tokens` 和顶层 `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` **都原样出现在流里**（总计 128 cached）。→ **Copilot 的 OpenAI 兼容后端（至少 DeepSeek 路径）会把缓存字段透传给流式客户端**，尽管官方不提供 per-request 文档。该讨论同时指出 DeepSeek 的缓存折扣对 Copilot 免费用户「用不上」（因为系统提示没被缓存）。
- copilot-cli issue #3808：请求 Copilot CLI 对 Claude Sonnet 启用 Anthropic 缓存断点（当前「无可见优化」）——说明 Copilot CLI 订阅路径**目前不刻意利用/暴露 Anthropic prompt cache**。
- **结论**：Copilot=订阅+token 配额；缓存字段**不面向终端用户文档化**；企业聚合 API 无缓存拆分；底层 SSE 有透传迹象（社区实测）。证据等级：官方文档（metrics API 字段）+ issue 讨论。

来源：https://docs.github.com/rest/copilot/copilot-usage-metrics 、https://github.com/microsoft/vscode/issues/317837 、https://github.com/logancyang/obsidian-copilot/discussions/2380 、https://github.com/github/copilot-cli/issues/3808 、https://code.visualstudio.com/blogs/2026/06/17/improving-token-efficiency-in-github-copilot

### 6.2 Cursor
- 论坛官方账号（客服口径，thread「Why are cache read and write chargeable?」）：「In all cases we show the precise token consumed in Usage report **as provided by AI provider sent back with AI response**」——**用量面板展示的缓存拆分明细来自上游 API 响应原样**；「有些供应商把 cache write 算进 Input 只单列 cache read，有些（Anthropic）单独分开，我们按供应商返回的展示」。
- 订阅（Pro）与 BYOK 的用量面板都单列 **Cache Read / Cache Write**，且按缓存价计费（cache read ≈ 输入价 10%）。多篇论坛帖用「0 cache read / 0 cache write → usage 暴涨」排查 Auto 模式路由到不支持缓存的模型（版本 2.6.12 → 2.6.18 修复）。
- **注意**：这说明 Cursor 订阅计划**会展示**缓存 token 明细（这是少数订阅制里对用户可见的）；但这只是「面板展示」，非公开 API —— Cursor 不提供获取原始 usage 的 API（未找到）。
- 另一个相关实证（microsoft/vscode#312939，OpenRouter BYOK in Copilot）：**经 OpenRouter 的 Claude 在 agent 模式里 `cached_tokens` 恒 0**，与原生 Anthropic BYOK 对比 10 倍成本差异——聚合层缓存是否生效对 agent 成本影响极大。

**结论**：Cursor=订阅制但用量面板单列 cache read/write（透传自上游响应）；无公开 usage API。证据等级：社区讨论（官方客服回复）+ 论坛实测；官方文档未直接确认面板字段。

来源：https://forum.cursor.com/t/someone-please-explain-why-are-cache-read-and-write-chargeable/153538/8 、https://forum.cursor.com/t/auto-mode-not-using-prompt-caching-0-cache-read-write-sudden-usage-spike/154278 、https://forum.cursor.com/t/cache-read-token/153794 、https://github.com/microsoft/vscode/issues/312939

### 6.3 Windsurf
- credits + token 混合计费：外部模型按「模型供应商 API 价 + 20% 加成」换算 credit，**明确区分 input / cache-read / output 三种单价**（flexprice.io 整理：Claude Sonnet 4：input 90 credits/M、**cache read 9 credits/M**、output 450 credits/M；1 credit=$0.04）。→ Windsurf 计量层**按 cache-read 打折计费**，说明其网关解析并保留了缓存字段。
- 用户侧**看不到原始 usage 字段**，只能看到 credit 消耗与用量面板；Tokenminning 的 Windsurf 页提到「Quota & billing（daily/weekly quota, cache reads, enterprise ACUs）」→ 官方文档存在 cache reads 相关条目（推测在用量说明中，未逐字核验）。
- **结论**：订阅/credits 制；缓存 token 参与折扣计费（第三方资料）；未找到向用户暴露 per-request usage 的证据。证据等级：第三方价格分析 + 官方文档存在性（未逐字核验）。

来源：https://flexprice.io/blog/windsurf-ai-pricing-breakdown 、https://tokenminning.ai/ides/windsurf 、Windsurf 官方文档（quota & billing，未逐字核验）

### 6.4 Augment Code
- 官方文档（Token-Based Pricing）：「Augment **自动缓存稳定上下文**（repo index、AGENTS.md、最近文件），**cached input tokens 按供应商缓存价计费（约输入价 10%）**，服务费随缩水」；「Usage → Models 面板展示每个模型的 input/output/**cache read/cache write** 单价」。
- 定价体系：2025-10 起从 message 制改 credit 制（token 制文档较新，网页 2026 版本同时提到 token-based pricing 与 credit）。
- **结论**：订阅/credit 制，官方明确缓存读取按折扣计费并在面板展示缓存单价——但没有公开 API 暴露原始 usage 字段。证据等级：官方文档。

来源：https://docs.augmentcode.com/models/token-based-pricing 、https://www.augmentcode.com/blog/augment-codes-pricing-is-changing

---

## 7. Cloudflare AI Gateway / Portkey / LiteLLM（及顺带 Vercel AI Gateway）

### 7.1 Cloudflare AI Gateway
- 官方「Caching」文档指的是**网关级响应缓存**：按 provider+endpoint+model+auth+body 构造 SHA-256 cache key，用 `cf-aig-cache-status: HIT/MISS` 头标识；**命中时直接返回缓存响应，不再调用上游**——这是「cache 掉整条响应」，不是 prompt cache。命中响应的 usage 含义取决于缓存内容（官方未在此文档中说明 usage 归零；**与 OpenRouter 响应缓存把 usage 清零不同，Cloudflare 文档未写明**，实验时注意区分）。
- Anthropic provider 文档：给出把 base URL 指向 AI Gateway 的示例（`/ai/v1/messages`），**未提到会规范化/丢弃 Anthropic 的 `cache_read_input_tokens`**。社区（openclaw#46709）实测请求体的 `cache_control` 能透传到 gateway（bug 是在 openclaw 侧 TTL 设置，不是网关丢弃）。
- **未找到**官方文档明确说明 Cloudflare AI Gateway 对上游 usage 缓存字段的保留/改名策略——按「透明代理」设计推测为原样透传（推测，证据不足）。
- Workers AI（非网关）文档确认其在 `usage` 对象里返回 cached token 计数——但那是 Cloudflare 自营推理，不是聚合层。

**结论**：Cloudflare AI Gateway 未文档化缓存 usage 字段处理；其自带缓存是响应级缓存（有 HIT/MISS 头）；请求侧 cache_control 可达。证据等级：官方文档（缓存功能）+ issue 讨论（cache_control 透传）+ 推测（usage 透传）。

来源：https://developers.cloudflare.com/ai-gateway/features/caching 、https://developers.cloudflare.com/ai-gateway/usage/providers/anthropic 、https://github.com/openclaw/openclaw/issues/46709 、https://developers.cloudflare.com/workers-ai/features/prompt-caching

### 7.2 Portkey
- **有明确的规范化文档**（Bedrock Prompt Caching 页）：
  - 「Portkey normalizes responses to the OpenAI format」；`prompt_tokens` **包含**缓存 token：`prompt_tokens = inputTokens + cache_read_input_tokens + cache_creation_input_tokens`。
  - `cached_tokens` 出现在 usage 里（OpenAI 风格）；定价时先从 prompt_tokens 减去缓存部分，再分别按 base input / cache read（折扣价）/ cache write 计价。
- 其观测端/Inference API Responses 返回 `usage.input_tokens_details.cached_tokens`（官方 API 参考示例）。
- 自带「响应缓存（simple/semantic）」与 prompt cache 是两回事（Portkey blog 明说两者可叠加）。
- **结论**：Portkey 会保留并**主动规范化**缓存字段到 OpenAI 风格（`prompt_tokens_details.cached_tokens`），且计费按缓存分项。证据等级：官方文档。

来源：https://docs.portkey.ai/docs/integrations/llms/bedrock/prompt-caching 、https://docs.portkey.ai/docs/integrations/llms/openai/prompt-caching-openai 、https://docs.portkey.ai/docs/api-reference/inference-api/responses/retrieve-response 、https://portkey.ai/blog/openais-prompt-caching-a-deep-dive

### 7.3 LiteLLM
- 官方 Prompt Caching 文档：「For the supported providers, **LiteLLM follows the OpenAI prompt caching usage object format**」→ OpenAI 兼容 `completion()` 返回 `usage.prompt_tokens_details.cached_tokens`；同时返回对象里也带 Anthropic 原生 `cache_creation_input_tokens` / `cache_read_input_tokens`（官方示例的 Usage 对象同时含两者）。即**双格式并存**（规范化 + 保留原生）。
- `/v1/messages`（Anthropic 兼容端点）：按 Anthropic 原生返回 `cache_creation_input_tokens` / `cache_read_input_tokens`（官方 anthropic_unified 文档）。
- **已知 bug #27763**：Anthropic `/v1/messages`（含 Vertex/Bedrock 透传路径）**不会把原生 `cache_read_input_tokens` 映射成 `prompt_tokens_details.cached_tokens`**，导致 Prometheus 的 `litellm_cached_tokens_metric_total` 恒为 0、缓存命中看起来像没发生，且 `litellm_spend_metric` 可能把缓存读取按全价算。
- 计费：有 `cache_read_input_token_cost` / `cache_creation_input_token_cost` 单价，但**计费 bug 多**：litellm#26807（自定义定价路径缓存 token 按全价算，用户多付 1.67×）、#33772（OpenAI `cache_write_tokens` 未计入成本，消费远低于厂商账单）、#11364（Anthropic 缓存成本算错）、#34875（生产流式 80.7% 行成本 $0，并发竞态）。
- 流式：默认不强制 include_usage（`always_include_stream_usage` 默认关）；合成末端 usage chunk 曾有 `choices` 非空的历史 bug（#28735 等）。
- **结论**：LiteLLM 意图是「OpenAI 风格规范化 + 保留原生」，但 Anthropic 透传路径的功能与计费都有多个已知坑，实验中应同时对比原生字段与 `cached_tokens`。证据等级：官方文档 + issue 讨论 + 第三方调研。

来源：https://docs.litellm.ai/docs/completion/prompt_caching 、https://docs.litellm.ai/docs/anthropic_unified 、https://github.com/BerriAI/litellm/issues/27763 、#26807 、#33772 、#11364 、https://github.com/cuihuan/awesome-ai-gateway/blob/main/docs/virtual-keys-metering.zh-CN.md

### 7.4 Vercel AI Gateway（顺带）
- vercel/ai#13907（2026-03）：经 Vercel AI Gateway 调 `moonshotai/kimi-k2.5`，面板正确显示 Cache Read 5.8M（93.5% 命中），但**账单按全价输入计费**——真实成本 $4.00 vs 直连 $1.28（6×）。→ 网关侧「显示缓存但不应用缓存折扣」的实例。证据等级：issue 讨论。
- 来源：https://github.com/vercel/ai/issues/13907

---

## 8. 实验建议（通过聚合层验证缓存字段时的检查清单与坑）

### 8.1 该检查哪些字段（按入口格式）
- **OpenAI 兼容入口（大多数聚合层采用）**：
  - `usage.prompt_tokens_details.cached_tokens`（聚合层规范化后应在此）
  - 扩展字段：OpenRouter `cache_write_tokens`、`cache_discount`、`cost_details`；LiteLLM 同对象里还可能带 `cache_creation_input_tokens` / `cache_read_input_tokens`
  - Responses API 入口（Codex 类客户端）：`usage.input_tokens_details.cached_tokens`（one-hub 曾因 omitempty 漏掉此字段）
- **Anthropic 兼容入口（`/v1/messages`）**：`usage.input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`（+新格式 `cache_creation.ephemeral_5m/1h_input_tokens`）
- **DeepSeek/部分上游**：顶层 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`（注意有些聚合层会原样透传、有些会映射进 `cached_tokens`——Bifrost 的做法是映射）
- **同一个请求的三种视图都要抓，不能只看一种**：
  1. 客户端收到的响应 usage；
  2. 网关消费日志/账单里的 token 拆分（new-api#6144 的教训：这两者可能不一致——响应是对的、账单是坏的）；
  3. 上游（如果可直连对照）原生 usage——用于判断聚合层是「透传」「改名」还是「丢弃」。

### 8.2 已知的坑（汇总自本节调研）
1. **流式 usage 必须有 `stream_options.include_usage=true`**，否则 OpenAI Chat Completions 风格的流根本没有 usage chunk（OpenRouter 同规则；LiteLLM 有合成兜底但历史上有格式 bug）。实验脚本务必显式带该参数并对齐最后一个 chunk。
2. **网关自身另有「响应缓存」（result cache）**：OpenRouter 的 `X-OpenRouter-Cache-Status: HIT` 时 usage 全为 0；Cloudflare AI Gateway 有 `cf-aig-cache-status`；此类命中不是 prompt cache，别误读为「缓存命中 token=0」。
3. **前缀漂移/路由漂移**：聚合层多供应商路由会让同一 session 落到不同上游导致缓存失效；OpenRouter 用 `session_id` 做 sticky routing 以保缓存。实验中固定供应商（`provider` 参数）或固定 session_id 再测。
4. **客户端解析 bug 会掩盖真相**：opencode 对 OpenAI-compatible 流式 provider 的 `tokens_cache_read` 恒 0（#33997）——不要用 opencode 的展示值当结论，要看原始 SSE。
5. **显示 vs 计费分离**：new-api xAI 渠道（#6144）响应正确但账单按全价；Vercel AI Gateway（#13907）面板显示缓存但账单全价。**验证「缓存字段是否透传」和「缓存是否影响账单」是两件事**，后者在中转站/订阅网关里只能靠站方后台，无法从响应验证。
6. **供应商/模型差异**：同一聚合层下，DeepSeek 缓存可能不过网关（china-llm 实测 OR 上 0 cached）而 GLM 正常；Kimi K3 缓存折扣不经过 OpenRouter。实验要按模型逐个测，不能拿一个模型代表全部。
7. **语义差异**：Anthropic 的 `input_tokens` 是「最后一个缓存断点之后的 token」（缓存读取已排除）；OpenAI 的 `prompt_tokens` **包含**缓存读取。字段 `cached_tokens > input 总量` 只有在排除语义下才可能出现（new-api#5003 曾因此把输入算成负数）。取值与对账时务必按供应商语义。
8. **订阅制服务（Copilot/Windsurf/Augment/Cursor 订阅）没有公开 per-request usage API**：无法从响应侧做该实验；Cursor 面板展示的 cache read/write 数据点据客服称来自上游响应。若实验目标是「验证缓存命中 token」，应优先选按量 API（OpenRouter、Zen、中转站）。
9. **国内中转站验证**：Claude Code 场景看 `cache_creation_input_tokens` / `cache_read_input_tokens` 是否存在且随轮次递增（命中）；缺失=该站（逆向/无缓存）不保留缓存字段。社区普遍以「响应 usage 是否带缓存字段」作为中转是否『支持缓存计费』的验收标准。
10. **缓存写入也有计费折扣的镜像**：OpenRouter 用 `cache_write_tokens`、Anthropic 用 `cache_creation_input_tokens`（5m=1.25×、1h=2× 输入价）。实验前两轮必然出现 cache write>0、cache read=0，符合预期；别把首轮 cache read=0 当成「网关丢字段」。

### 8.3 建议的最小实验矩阵
| 层 | 建议入口 | 必查字段 | 对照 |
|---|---|---|---|
| 直连官方（对照组） | Anthropic/OpenAI/DeepSeek 原生 | `cache_read_input_tokens` / `cached_tokens` / `prompt_cache_hit_tokens` | — |
| OpenRouter | `chat/completions` + include_usage | `cached_tokens`+`cache_write_tokens`+`cost`/`cache_discount` | 与直连对照；固定 provider+session_id |
| opencode Zen/Go | `v1/chat/completions`/`v1/messages` | 协议原生缓存字段 | 与官方价格表 Cached Read 列对照 |
| new-api/one-hub | chat/completions（流式+非流式各一遍） | `cached_tokens`；同时看网关消费日志 | 非流式作为基线（历史上流式丢字段 bug 多） |
| LiteLLM | completion + /v1/messages | `cached_tokens` 与 `cache_read_input_tokens` 是否同时出现 | 抓 `litellm_cached_tokens_metric` 是否>0 |
| 国内中转站 | Anthropic 兼容 | `cache_creation/read_input_tokens` | 两轮同前缀请求，命中应>0 |

---

## 9. 一句话总结

- **透传且规范化得最好**：OpenRouter（统一 OpenAI 风格 `cached_tokens`+扩展）、Portkey（明确规范化并分项计价）、Bifrost（将 `prompt_cache_hit_tokens` 映射为 `cached_tokens`）。
- **意图透传但坑多**：new-api / one-hub（多个流式/Responses bug）、LiteLLM（Anthropic 透传路径不映射、计费 bug 多）、Cloudflare AI Gateway（未文档化，推测透传）。
- **计费不含缓存或订阅不暴露**：one-api（无缓存单价）、Copilot（聚合 API 无缓存拆分）、Windsurf/Augment（按缓存折扣计费但不暴露原始字段）、Cursor（面板展示缓存明细但没有公开 API）。
- **核心陷阱**：「客户端收到的 usage」≠「网关账单」≠「上游计费」，三者要分开验证；流式必须 `include_usage`；注意区分网关的 prompt cache（KV cache 命中）与网关的响应缓存（result cache，可能返回 usage 全 0）。

---
*报告完。所有引用为调研时（2026-08-29）可访问的 URL；证据等级逐条标注；凡「未找到证据」处均已如实说明。*