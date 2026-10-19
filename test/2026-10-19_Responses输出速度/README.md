# Responses 正文与工具调用输出速度

2026-10-19 用户确认：所有 Responses 只统计正文＋工具调用生成的速度，排除思考；Chat Completions 保持原口径。

## 运行

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-19_Responses输出速度/run_regressions.py
/opt/homebrew/bin/python3.11 -B test/2026-10-08_主子费用/run_regressions.py
```

`support.py` 使用测试目录内临时运行路径并设置 `ASTRION_IGNORE_DOTENV=1`。测试不读取真实凭据／对话、不启动服务、不发送模型或价格网络请求。

## 覆盖

- 长思考与等待、完成尾段不进入 Responses 速度分母；正文和工具原始事件按区间并集计时。
- 参数缓冲与 null 清洗保持原行为，速度在协议缓冲前观察，工具名与参数生成时间均可计入。
- 并行工具不重复累计，中途推理切断计时，没有独立 done 时由 completed 收尾，断流不出样本。
- 推理 token 明细在提取／二次归一化后仍保留，明确为零有效；缺失、非法、纯推理与无有效计时跳过样本。
- 主流式函数实际传递完整 usage 与独立速度样本，Chat 无覆盖，重试不沿用失败轮的 usage／计时。
- 费用与累计用量仍含全部输出，速度样本单独进账本；请求防重与多轮加权汇总正常。
- 既有 usage 提取与 Responses／Codex 协议转换回归。

模拟例：总输出 1000 token，思考 900 token，正文与工具生成时间 2 秒，则显示样本速度 50 tokens/s；计费与累计输出仍为 1000 token。

本次只改后端统计，前端位置和文案保持现有行为。历史已保存速度不回算。离线合成事件回归不能代替真实提供商网络接收速率验证。
