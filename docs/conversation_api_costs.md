# 对话 API 花费统计

## 范围与价格

用量面板的「总花费」汇总主智能体及归属于该对话的每个子智能体。自动审批、目标审核、工作流审核暂不计入。现有累计 token 五项仍为主智能体口径，不将子用量混入其上下文或缓存命中率。

按每次成功响应返回的真实 `usage` 计算：

```text
USD = ((输入 - 缓存命中输入) × cost.input
       + 缓存命中输入 × cost.cache_read
       + 输出 × cost.output) / 1,000,000
```

不单列缓存写入费用。冷启动豁免只影响缓存命中率，不减免费用。输出按 API 返回的 completion/output token 口径，不重复加上其中已包含的推理 token。

价格仅匹配该提供商的精确模型 ID；不借用其它提供商价格。`modules/modelsdev_registry.py` 的版本 2 瘦身缓存保留上述三项单价，源为 https://models.dev/api.json（美元／百万 token），仍沿用六小时缓存。在主／子模型请求开始前捕获价格和模型身份，版本 1 缓存会刷新升级；刷新失败保持已有数据，没有价格则标记「未计价」。

计费与凭证来源无关（2026-10-09 用户拍板）：API Key、ChatGPT 订阅（OAuth，`openai-codex`）与第三方编程套餐一律按 models.dev 的模型单价折算，金额表示「这次请求按 API 单价折算值多少」，不是服务商账单。订阅模型价格来自 models.dev 的 `openai` provider（`config/providers_catalog.json` 的 `modelsdev_key: openai`，由 `utils/api_client/codex/models.py` 注入 profile）。查不到精确模型单价（隐藏/internal 模型、无 `modelsdev_key` 的本地或特殊链路条目）时显示「未计价」，不虚构、不按零计。

## 持久化与恢复

- 模型价格：`<DATA_DIR>/modelsdev_cache.json`，保存单价、数据源与获取时间；与原有模型能力同步共用。
- 对话账本：`<工作区 data_dir>/api_costs/<conversation_id>.json`。主、子共享同一文件；`actors` 按主智能体及子任务 ID 累计，`requests` 保存请求 ID、模型／提供商、当时单价、真实用量、USD 金额和登记时间。
- 用 Decimal 累计原始 USD，人民币仅显示换算。请求 UUID 防重复；同目录共享锁和原子替换保护主／子并行写入。沿用项目单后端进程约束。
- 子智能体 `stats.token_usage` 新增 `cached_input` 累计字段；每次响应即时保存统计。两个恢复入口均继承统计，并用已提交账本修复统计文件写入前中断的记录。压缩后上下文清零，不清零历史用量和费用。
- 旧对话、旧子统计没有当时价格／缓存明细，不补算。保留已有 token 基线，新增请求开始计价；无法计价的历史或请求标记「部分统计」。成功响应若不返回 `usage`，登记未计价，不伪装为零费用。

## 工作段输出速度（2026-10-19）

速度只汇总主智能体的有效请求样本，段级取 `Σ样本输出 token / Σ样本生成秒数`，不对各轮速度做算术平均。压缩续接保留同一工作段样本，真正结束时才消费、写入锚点 user 消息 `metadata.token_speed` 并通过 `token_update` 下发；前端显示位置与完成逻辑不变。已有历史速度不回算。

- **Chat Completions**：沿用完整输出 token（包含思考），计时从首个有内容增量至流结束，排除请求前置等待及工具执行。
- **所有 Responses（含 Codex OAuth 与普通 API Key）**：样本输出为 API 的 `output_tokens − output_tokens_details.reasoning_tokens`，只衡量正文与工具调用生成。思考 token 明细在 usage 归一化中可选保留，明确的 `0` 有效，缺失不能视作 `0`；缺失、非法、超过总输出或没有非思考输出均跳过样本。
- **Responses 原始事件计时**：`utils/api_client/responses/speed.py` 在协议转换缓冲／yield 前观察 SSE。正文从首个非空 text delta 至对应 text/item done，函数调用从含工具名的 item added（或首个参数 delta）至 arguments/item done。多个区间取并集，避免并行工具重复计时；区间之间的思考与等待不计入。未单独发送 done 的网关在 completed 收尾已观测的区间；无有效正时长则跳过。
- **采样与计费分离**：`apply_usage_statistics(..., speed_sample=None)` 沿用 Chat 旧口径；显式字典使用指定样本，空字典跳过、禁止回退。费用和累计 token 始终使用完整 usage。账本保留通用 `duration_ms`／`ttft_ms`，Responses 另存 `speed_sample={output_tokens,duration_ms}`，空对象表示没有有效速度样本；它不是费用的输出 token 字段。
- **失败／重试**：失败轮不入样本，重试重新创建计时器并清空本轮 timing。原有请求 ID 防重继续生效。统计是客户端接收事件的速率，不代表提供商内部逐 token 计算速度。

回归：`/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-19_Responses输出速度 -p 'test_*.py'`。用合成 SSE、虚拟时钟和临时账本验证，不调用真实模型或读取真实凭据。

## 币种和汇率

外观设置的 `display_currency` 可选 USD、CNY，默认 USD；保存到用户共享的 `personalization.json`。后端白名单与前端类型、默认、读取均注册。

汇率源为 https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY 。Frankfurter v1 提供参考汇率，工作日公布；按 UTC 日期每日首次读取在后台刷新，持久化到 `<DATA_DIR>/exchange_rates.json`，同时保存获取时间和源汇率日期。失败保留旧缓存，一小时内不反复重试；没有首次成功缓存时显示无法换算，不使用写死汇率。人民币明细显示实际采用的汇率和日期。

用量面板打开时每五秒读取既有 token-statistics 端点，更新子费用及已完成的汇率刷新；沿用辅助请求的对话 session/epoch 所有权，切对话或关闭面板停止该轮询。主请求费用同步走现有 token_update 事件。悬停金额在下方展示主＋各子明细，浮层受最大宽高限制；币种切换不改原始账本。

## 等待状态

`api_request_start` 已有事件置位 `apiRequestPending`。状态头像现在优先采用本轮明确的 API 等待态，覆盖旧工具意图、残留工具或思考字段；响应开始清除标记后恢复正常思考／工具判断，停止任务不重新出现等待提示。

正文输出期保留工作形象，但旁边不显示状态文字。正文 action 是工具意图保留的边界，正文结束后到任务终结之间也不重新显示此前工具文案；新的工具意图或 API 等待按新状态显示。人民币使用窄货币符号 `¥`，总额和各执行者明细共用同一格式，不带 `CN` 前缀。

补充回归：`node test/2026-10-08_主子费用_02/avatar_and_currency.cjs`（正文状态 8 场景、币种格式 6 场景，不启动服务）。

## 验证

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-08_主子费用/run_regressions.py
npm run build --silent 2>&1 | tail -n 5
```

回归使用临时运行目录和合成模型、用量、汇率，不读取真实对话或凭证、不启动服务。覆盖三类计费、缺失单价／usage、价格快照、多模型累计、并行写入、重复提交、子恢复、压缩、统计落盘中断修复、每日汇率及失败保留缓存、币种保存、头像等待状态，并执行现有后端冒烟。另含订阅模型按 API 单价计费、codex profile 单价注入与无单价标「未计价」。

需用户验收：工具完成后到下次响应期间显示「等待 API 响应…」；第六格及明细浮层位置／滚动；三主题和人民币切换。离线回归不等同真实提供商账单核对或网络刷新实测。
