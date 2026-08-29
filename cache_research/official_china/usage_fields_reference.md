# 国内官方 LLM API 缓存字段速查（官方示例 JSON 摘录）

> 配套报告：同目录 `README.md`。以下 JSON 均为各厂商**官方文档原文示例**摘录，直接复制进实验脚本对照。

## 1. DeepSeek —— usage 顶层命中/未命中
```json
"usage": {
  "prompt_tokens": 16,
  "completion_tokens": 10,
  "total_tokens": 26,
  "prompt_cache_hit_tokens": 0,
  "prompt_cache_miss_tokens": 16,
  "completion_tokens_details": { "reasoning_tokens": 0 }
}
```

## 2. Moonshot Kimi —— usage 顶层 cached_tokens（官方示例原文）
非流式响应：
```json
"usage": { "prompt_tokens": 19, "completion_tokens": 21, "total_tokens": 40, "cached_tokens": 10 }
```
流式响应（最后一个 chunk，finish_reason=stop 时携带）：
```json
"usage": {"prompt_tokens":19,"completion_tokens":13,"total_tokens":32,"cached_tokens":12}
```
请求参数 `prompt_cache_key`（官方原文）：“用于缓存相似请求的响应以优化缓存命中率。对于 Coding Agent，通常是代表单个会话的 session id 或 task id；退出并恢复会话时应保持不变。对于 Kimi Code Plan，此字段为必填以提高缓存命中率。”
（官方《上下文缓存指南》PDF 示例亦出现 `usage.prompt_tokens_details.cached_tokens`，两处并存，实验需双读。）

## 3. 通义千问 Qwen（阿里云百炼，OpenAI 兼容）
隐式命中：
```json
"usage": { "prompt_tokens": 3019, "completion_tokens": 104, "total_tokens": 3123,
           "prompt_tokens_details": { "cached_tokens": 2048 } }
```
显式（cache_control ephemeral）：
```json
"usage": { "prompt_tokens": 2174, "completion_tokens": 0,
           "prompt_tokens_details": { "cache_creation_input_tokens": 2156, "cached_tokens": 0 } }
// 第二次请求命中：cache_creation_input_tokens=0, cached_tokens=2156
```
原生 DashScope：`usage.prompt_tokens_details['cached_tokens']`（部分海外地域视觉模型为顶层 `usage.cached_tokens`，官方注明后续升级）。

## 4. 智谱 GLM
> 官方文档只给出字段名，未公布具体示例数字，以下为字段结构示意（值用占位符）：
```json
"usage": { "prompt_tokens": <int>, "completion_tokens": <int>, "total_tokens": <int>,
           "prompt_tokens_details": { "cached_tokens": <int> } }
```

## 5. 字节豆包（火山方舟 Context Chat API）
```json
"usage": { "prompt_tokens": 28, "completion_tokens": 4, "total_tokens": 32,
           "prompt_tokens_details": { "cached_tokens": 18 } }
```
（需创建 ctx-* 缓存并传 context_id；或 Responses API 传 `"caching":{"type":"enabled"}` / `previous_response_id`。）

## 6. MiniMax
OpenAI 兼容格式：
```json
"usage": { "prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500,
           "prompt_tokens_details": { "cached_tokens": 800 } }
```
Anthropic/Messages 格式（自动或显式均可出现）：
```json
"usage": { "input_tokens": 108, "output_tokens": 91,
           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 14813 }
```

## 7. 阶跃星辰 Step —— usage 顶层 cached_tokens
```json
"usage": { "cached_tokens": 512, "prompt_tokens": 591, "completion_tokens": 120, "total_tokens": 711 }
```

## 8. 百度文心（千帆 ModelBuilder）
```json
"usage": { "prompt_tokens": 159, "completion_tokens": 89, "total_tokens": 248,
           "prompt_tokens_details": { "cached_tokens": 128 } }
```

## 统一读取优先级（实验脚本建议）
```python
usage = resp.get("usage") or {}
pdet = usage.get("prompt_tokens_details") or {}
cached = (
    usage.get("prompt_cache_hit_tokens")          # DeepSeek
    or usage.get("cached_tokens")                  # Kimi / Step / 部分 DashScope
    or pdet.get("cached_tokens")                   # Qwen/GLM/豆包/MiniMax/千帆
    or pdet.get("cache_read_input_tokens")         # Anthropic 兼容
    or 0
)
```