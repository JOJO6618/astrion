# 主／子智能体 API 花费（完整）

> 本文由 `AGENTS.md` §22 小节**原样下沉**而来（2026-10-08 压缩 AGENTS.md 时导出，内容未改写）。
> 用途：`AGENTS.md` 只保留硬约束与「改代码必须知道」，机制细节、历史沿革、逐条清单在此查阅。
> 若与代码冲突，一律以代码为准，并回来同步本文与 `AGENTS.md`。

---

## 22) 主／子智能体 API 花费（2026-10-08）

- **范围**：总花费＝主＋每个子智能体，三个审核智能体暂不计入。按非缓存输入、缓存命中输入、输出三项计价，不单列缓存写入；缓存命中率的冷启动豁免不减免费用。原有 token 五项仍保持主智能体口径。
- **价格与账本**：`modules/api_pricing.py` 捕获请求当时的精确提供商／模型价格，`modelsdev_registry.py` 版本 2 瘦身缓存保留 `cost.input/cache_read/output` 及来源、时间；不借其它提供商价格。`modules/conversation_costs.py` 在 `<工作区 data_dir>/api_costs/<conversation_id>.json` 原子保存请求及各执行者汇总，以请求 UUID 防重、Decimal 累加美元。订阅／本地单列标识；旧历史、缺单价或缺 usage 标记未计价，禁止当免费。
- **子恢复**：`modules/sub_agent/usage.py` 统一归一化、保存 `token_usage.cached_input`、继承累计统计；两个恢复入口都调用。账本可修复统计文件保存前中断，压缩只清上下文、不清历史累计。
- **币种**：外观 `display_currency`＝USD/CNY，默认 USD。人民币只转换显示，不改账本；`modules/exchange_rates.py` 每 UTC 日后台更新 Frankfurter 参考汇率到 `<DATA_DIR>/exchange_rates.json`，失败保留缓存，无首次缓存不虚构汇率。费用明细浮层＋五秒面板轮询复用现有辅助请求所有权。
- **等待态**：头像 `api_request_start` 的明确等待态优先于上次工具意图及残留流式状态；响应开始或任务终结清除，停止后不重显。
- **契约与回归**：`docs/conversation_api_costs.md`；`/opt/homebrew/bin/python3.11 -B test/2026-10-08_主子费用/run_regressions.py`，临时数据、合成模型与汇率，不读真实凭证／对话，不启动服务。网络刷新、真实账单和前端视觉需实际运行验收。

## 工作段 tokens/s（2026-10-19）

- Chat Completions 保持包含思考的完整输出口径；所有 Responses 改为 `(output_tokens − reasoning_tokens) / 正文与工具调用生成秒数`，缺推理明细或有效时长跳过、不回退。
- 计时权威为 `utils/api_client/responses/speed.py` 的原始 SSE 区间并集，不使用整段摘要／完整参数缓冲后的到达时间；排除思考、首段等待、完成尾段和工具执行，并行工具不重复累计。
- 速度独立样本经 `speed_sample` 传到 ContextManager 与账本；通用 `duration_ms`／`ttft_ms`、计费、累计 token 保留完整口径。段级按样本 token／时长加权，压缩续接与前端下发保持原有契约，历史不回算。
- 完整事件边界与空样本约定见 `docs/conversation_api_costs.md`；新增回归在 `test/2026-10-19_Responses输出速度/`。
