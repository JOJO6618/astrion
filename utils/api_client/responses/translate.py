"""Chat Completions ↔ OpenAI Responses API 双向映射（纯函数，可单测）。

通用 Responses 协议转换层（2026-09-25 从 codex/translate.py 泛化搬家，
codex 子包改 re-export 保持存量导入路径不变）。适用对象：ChatGPT Codex
后端、OpenCode Go/Zen 转发的 gpt/grok 系、以及任何 OpenAI Responses
兼容网关。

映射规则要点：
- 请求：system 并入 instructions；assistant 的加密 reasoning items
  （消息字段 ``responses_reasoning_items``）原样回插；
  ``reasoning_content``（摘要）**忽略不回传**；孤儿 function_call_output
  降级为普通 user 文本；tools 拍平（可选参数改 nullable 联合类型，见
  flatten_tools）；采样参数丢弃；所有 item 不携带服务端 id。
- 响应：Responses 语义化 SSE 事件流 → Chat 增量 chunk 形状，
  与 ``APIClientChatMixin.chat()`` 的产出完全同形，下游零改动。
  摘要按 part 整段下发（剥 markdown 加粗、多段间空行分隔）；function_call
  参数在 output_item.done 落定时一次性下发并剥除 null 填充——GPT 系
  Responses 后端约束解码会强制填充所有 schema 属性（ChatGPT 后端实测 +
  OpenCode 转发 gpt-5.6-luna 实测编造可选参数值，OmniRoute #6951 同款），
  模型以 null 表达「不需要」，由 :func:`sanitize_tool_arguments` 恢复可选语义。
"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple


class ResponsesAPIError(Exception):
    """Responses 后端返回的业务错误（response.failed / error 事件）。"""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 请求映射
# ---------------------------------------------------------------------------


def _flatten_content_to_text(content: Any) -> str:
    """把多模态 content parts 拍平为纯文本（tool 结果/孤儿降级用）。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False) if content is not None else ""
    texts: List[str] = []
    for part in content:
        if not isinstance(part, dict):
            texts.append(str(part))
            continue
        ptype = part.get("type")
        if ptype == "text":
            texts.append(str(part.get("text") or ""))
        elif ptype == "image_url":
            texts.append("[图片]")
        elif ptype == "video_url":
            texts.append("[视频]")
    return "\n".join(t for t in texts if t)


def _user_content_to_parts(content: Any) -> List[Dict[str, Any]]:
    """user 消息 content → Responses input parts（文本 + 图片 data URL）。"""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    parts: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                text = str(part).strip()
                if text:
                    parts.append({"type": "input_text", "text": text})
                continue
            ptype = part.get("type")
            if ptype == "text":
                text = str(part.get("text") or "")
                if text:
                    parts.append({"type": "input_text", "text": text})
            elif ptype == "image_url":
                image = part.get("image_url") or {}
                url = image.get("url") if isinstance(image, dict) else str(image)
                if url:
                    parts.append({"type": "input_image", "image_url": url})
            elif ptype == "video_url":
                parts.append(
                    {"type": "input_text", "text": "[用户提供了视频，当前模型不支持视频输入]"}
                )
    if not parts:
        parts.append({"type": "input_text", "text": ""})
    return parts


def _strip_server_ids(item: Dict[str, Any]) -> Dict[str, Any]:
    """剥离服务端 item id（store:false 下回传 id 会触发 not found 报错）。"""
    cleaned = {k: v for k, v in item.items() if k != "id"}
    return cleaned


def split_system_messages(
    messages: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """分离 system 消息（并入 instructions）与其余消息。"""
    system_texts: List[str] = []
    rest: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system":
            content = msg.get("content")
            text = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False)
            )
            if text:
                system_texts.append(text)
        else:
            rest.append(msg)
    return "\n\n".join(system_texts), rest


def chat_request_to_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """astrion 消息列表 → Responses ``input`` items。

    注意：调用前应先经 :func:`split_system_messages` 移除 system 消息。
    """
    input_items: List[Dict[str, Any]] = []
    seen_call_ids: set = set()

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "user":
            input_items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": _user_content_to_parts(msg.get("content")),
                }
            )

        elif role == "assistant":
            # 加密 reasoning items 原样回插（剥服务端 id），置于消息本体之前
            for item in msg.get("responses_reasoning_items") or []:
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    input_items.append(_strip_server_ids(item))
            text = msg.get("content")
            if isinstance(text, list):
                text = _flatten_content_to_text(text)
            if text:
                input_items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                call_id = tc.get("id")
                fn = tc.get("function") or {}
                name = fn.get("name")
                if not call_id or not name:
                    continue
                seen_call_ids.add(call_id)
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": fn.get("arguments") or "",
                    }
                )
            # reasoning_content（摘要）刻意忽略：只用于显示，不回传

        elif role == "tool":
            call_id = msg.get("tool_call_id") or ""
            output = _flatten_content_to_text(msg.get("content"))
            if call_id and call_id in seen_call_ids:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    }
                )
            else:
                # 孤儿 tool 输出（父 assistant 未配对）降级为普通 user 文本，
                # 否则服务端 400
                name = msg.get("name") or "tool"
                input_items.append(
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": f"[工具结果（{name}）]\n{output}",
                            }
                        ],
                    }
                )

    return input_items


_NULL_HINT = "不需要时填 null，系统会视为未提供"


def _optional_params_nullable(params: Dict[str, Any]) -> Dict[str, Any]:
    """把非 required 参数改为 nullable 联合类型并追加 null 提示。

    背景（2026-09 实测 + OmniRoute #6951 同款）：GPT 系 Responses 后端对
    函数调用使用约束解码，schema 里出现的属性模型必须逐个填充、无法省略，
    不想填的只能塞零值甚至编造「合理值」（ChatGPT 后端塞 "" /0/[]，
    OpenCode 转发 gpt-5.6-luna 实测编造 max_results:50）。按官方
    structured-outputs 惯例提供 null 通道后，模型会用 null 表达「不需要」；
    响应侧由 :func:`sanitize_tool_arguments` 把 null 剥除，恢复工具声明的
    可选语义。仅处理顶层 properties。对无约束解码怪癖的上游（如 grok）
    同样安全：显式塞的 null 会被响应侧剥成省略，语义等价。
    """
    if not isinstance(params, dict):
        return params
    props = params.get("properties")
    if not isinstance(props, dict) or not props:
        return params
    required = set(params.get("required") or [])
    new_props: Dict[str, Any] = {}
    for name, spec in props.items():
        if not isinstance(spec, dict) or name in required:
            new_props[name] = spec
            continue
        spec = dict(spec)
        ptype = spec.get("type")
        if isinstance(ptype, str) and ptype != "null":
            spec["type"] = [ptype, "null"]
        elif isinstance(ptype, list) and "null" not in ptype:
            spec["type"] = [*ptype, "null"]
        desc = str(spec.get("description") or "")
        if "null" not in desc:
            spec["description"] = f"{desc}（{_NULL_HINT}）" if desc else f"（{_NULL_HINT}）"
        new_props[name] = spec
    out = dict(params)
    out["properties"] = new_props
    return out


def flatten_tools(tools: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Chat 的 tools（function 包装一层）→ Responses 的拍平形态。"""
    flattened: List[Dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        item: Dict[str, Any] = {
            "type": "function",
            "name": fn["name"],
            "description": fn.get("description") or "",
            "parameters": _optional_params_nullable(
                fn.get("parameters") or {"type": "object", "properties": {}}
            ),
        }
        if fn.get("strict") is not None:
            item["strict"] = bool(fn.get("strict"))
        flattened.append(item)
    return flattened


def tool_required_map(tools: Optional[List[Dict[str, Any]]]) -> Dict[str, set]:
    """从原始 chat 工具列表构建 {工具名: required 参数集合}（响应侧清洗用）。"""
    result: Dict[str, set] = {}
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        params = fn.get("parameters")
        required = params.get("required") if isinstance(params, dict) else None
        result[str(fn["name"])] = set(required or [])
    return result


def sanitize_tool_arguments(
    name: Optional[str],
    arguments: Any,
    required_map: Optional[Dict[str, set]],
) -> str:
    """剥除「非 required 且值为 null」的参数（约束解码的 null 填充）。

    解析失败、未知工具、无 required 信息一律原样放行；required 参数的 null
    保留（交给工具自身校验报错）。
    """
    if isinstance(arguments, str):
        text = arguments
    else:
        text = json.dumps(arguments or {}, ensure_ascii=False)
    if not name or not required_map or name not in required_map:
        return text
    try:
        args = json.loads(text)
    except Exception:
        return text
    if not isinstance(args, dict):
        return text
    required = required_map[name]
    cleaned = {
        k: v for k, v in args.items() if not (v is None and k not in required)
    }
    return json.dumps(cleaned, ensure_ascii=False)


def _clean_summary_text(text: str) -> str:
    """摘要剥 markdown 加粗标记（思考块为纯文本渲染，不解析 md）。"""
    return text.replace("**", "").strip()


def build_responses_body(
    *,
    messages: List[Dict[str, Any]],
    instructions: str,
    model_id: str,
    tools: Optional[List[Dict[str, Any]]] = None,
    effort: Optional[str] = None,
    summary: str = "detailed",
    max_output_tokens: Optional[int] = None,
    verbosity: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """组装 Responses 请求体（含全部强制约束）。"""
    system_text, rest = split_system_messages(messages)
    if system_text:
        instructions = f"{instructions}\n\n{system_text}"

    body: Dict[str, Any] = {
        "model": model_id,
        "instructions": instructions,
        "input": chat_request_to_input(rest),
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
    }
    flat_tools = flatten_tools(tools)
    if flat_tools:
        body["tools"] = flat_tools
        body["tool_choice"] = "auto"
    reasoning: Dict[str, Any] = {}
    if effort:
        reasoning["effort"] = effort
    if summary:
        reasoning["summary"] = summary
    if reasoning:
        body["reasoning"] = reasoning
    if verbosity:
        body["text"] = {"verbosity": verbosity}
    if max_output_tokens and max_output_tokens > 0:
        body["max_output_tokens"] = int(max_output_tokens)
    if session_id:
        body["prompt_cache_key"] = session_id
    return body


# ---------------------------------------------------------------------------
# 响应映射（SSE 事件流 → Chat chunk）
# ---------------------------------------------------------------------------


def _map_usage(usage: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    mapped: Dict[str, Any] = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": usage.get("total_tokens") or (input_tokens + output_tokens),
    }
    in_details = usage.get("input_tokens_details") or {}
    if isinstance(in_details, dict) and in_details.get("cached_tokens") is not None:
        mapped["prompt_tokens_details"] = {
            "cached_tokens": in_details.get("cached_tokens")
        }
    out_details = usage.get("output_tokens_details") or {}
    if isinstance(out_details, dict) and out_details.get("reasoning_tokens") is not None:
        mapped["completion_tokens_details"] = {
            "reasoning_tokens": out_details.get("reasoning_tokens")
        }
    return mapped


class _ReasoningCollector:
    """收集本轮的加密 reasoning items（供下轮回插，落盘到消息 metadata）。"""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []
        self._seen_keys: set = set()

    def add(self, item: Dict[str, Any]) -> None:
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            return
        cleaned = _strip_server_ids(item)
        key = json.dumps(cleaned, sort_keys=True, ensure_ascii=False)[:200]
        if key in self._seen_keys:
            return
        self._seen_keys.add(key)
        self.items.append(cleaned)


async def responses_sse_to_chat_chunks(
    lines: AsyncGenerator[str, None],
    reasoning_collector: Optional[_ReasoningCollector] = None,
    required_map: Optional[Dict[str, set]] = None,
) -> AsyncGenerator[Dict[str, Any]]:
    """把 Responses 的 SSE 行流翻译为 Chat 形状 chunk。

    与 ``chat()`` 产出的 chunk 完全同形；遇到 response.failed/error 事件
    抛 :class:`ResponsesAPIError`（由 mixin 统一转 error chunk）。

    两处非逐字直通的刻意设计：
    - 推理摘要按 part 缓冲，text.done 时剥 markdown 加粗后整段下发，
      多段间补空行（前端思考块是纯文本渲染 + pre-wrap）；
    - function_call 参数 delta 只缓冲不转发，output_item.done 时取完整
      arguments 清洗（剥 null 填充）后一次性下发，避免约束解码的填充值
      在流式累加中固化。
    """
    collector = reasoning_collector or _ReasoningCollector()
    item_id_to_index: Dict[str, int] = {}
    index_to_name: Dict[int, str] = {}
    next_index = 0
    got_completed = False
    arg_buffers: Dict[int, List[str]] = {}
    summary_buffers: Dict[Tuple[str, int], List[str]] = {}
    summary_emitted = False

    async for line in lines:
        if not line:
            continue
        if line.startswith(":"):
            continue  # SSE 注释/心跳
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        event = data.get("type")

        if event == "response.created":
            yield {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}

        elif event == "response.output_text.delta":
            delta_text = data.get("delta") or ""
            if delta_text:
                yield {"choices": [{"index": 0, "delta": {"content": delta_text}}]}

        elif event == "response.reasoning_summary_text.delta":
            # 摘要 delta 只缓冲不转发，text.done 时整段清洗下发
            key = (
                str(data.get("item_id") or ""),
                int(data.get("summary_index") or 0),
            )
            summary_buffers.setdefault(key, []).append(data.get("delta") or "")

        elif event == "response.reasoning_summary_text.done":
            key = (
                str(data.get("item_id") or ""),
                int(data.get("summary_index") or 0),
            )
            text = str(data.get("text") or "")
            if not text:
                text = "".join(summary_buffers.get(key) or [])
            summary_buffers.pop(key, None)
            text = _clean_summary_text(text)
            if text:
                if summary_emitted:
                    text = "\n\n" + text
                summary_emitted = True
                yield {
                    "choices": [
                        {"index": 0, "delta": {"reasoning_content": text}}
                    ]
                }

        elif event == "response.reasoning_text.delta":
            delta_text = data.get("delta") or ""
            if delta_text:
                yield {
                    "choices": [
                        {"index": 0, "delta": {"reasoning_content": delta_text}}
                    ]
                }

        elif event == "response.output_item.added":
            item = data.get("item") or {}
            if item.get("type") == "function_call":
                item_id = item.get("id") or ""
                index = next_index
                next_index += 1
                if item_id:
                    item_id_to_index[item_id] = index
                index_to_name[index] = item.get("name") or ""
                yield {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": item.get("call_id"),
                                        "type": "function",
                                        "function": {
                                            "name": item.get("name") or "",
                                            "arguments": "",
                                        },
                                    }
                                ]
                            },
                        }
                    ]
                }

        elif event == "response.function_call_arguments.delta":
            # 参数碎片只缓冲，output_item.done 取完整 arguments 清洗后统一下发
            index = item_id_to_index.get(data.get("item_id") or "")
            if index is None and next_index > 0:
                index = next_index - 1
            if index is not None:
                arg_buffers.setdefault(index, []).append(data.get("delta") or "")

        elif event == "response.output_item.done":
            item = data.get("item") or {}
            if item.get("type") == "reasoning":
                collector.add(item)
            elif item.get("type") == "function_call":
                index = item_id_to_index.get(item.get("id") or "")
                if index is None and next_index > 0:
                    index = next_index - 1
                if index is not None:
                    raw_args = item.get("arguments")
                    if not isinstance(raw_args, str) or not raw_args:
                        raw_args = "".join(arg_buffers.pop(index, []))
                    else:
                        arg_buffers.pop(index, None)
                    cleaned_args = sanitize_tool_arguments(
                        item.get("name"), raw_args, required_map
                    )
                    if cleaned_args:
                        yield {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": index,
                                                "function": {
                                                    "arguments": cleaned_args
                                                },
                                            }
                                        ]
                                    },
                                }
                            ]
                        }

        elif event == "response.completed":
            got_completed = True
            response = data.get("response") or {}
            # 兜底冲刷：未等到 text.done 的摘要缓冲（保持段间分隔）
            for key in sorted(summary_buffers.keys()):
                text = _clean_summary_text("".join(summary_buffers.pop(key)))
                if text:
                    if summary_emitted:
                        text = "\n\n" + text
                    summary_emitted = True
                    yield {
                        "choices": [
                            {"index": 0, "delta": {"reasoning_content": text}}
                        ]
                    }
            # 兜底冲刷：未等到 output_item.done 的 function_call 参数缓冲
            for index in sorted(arg_buffers.keys()):
                raw_args = "".join(arg_buffers.pop(index))
                if raw_args:
                    cleaned_args = sanitize_tool_arguments(
                        index_to_name.get(index), raw_args, required_map
                    )
                    if cleaned_args:
                        yield {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": index,
                                                "function": {
                                                    "arguments": cleaned_args
                                                },
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
            for item in response.get("output") or []:
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    collector.add(item)
            finish_reason = "tool_calls" if next_index > 0 else "stop"
            chunk: Dict[str, Any] = {
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": finish_reason}
                ]
            }
            usage = _map_usage(response.get("usage"))
            if usage:
                chunk["usage"] = usage
            yield chunk

        elif event == "response.failed":
            response = data.get("response") or {}
            error = response.get("error") or {}
            raise ResponsesAPIError(
                str(error.get("message") or "Responses 响应失败"),
                code=error.get("code"),
            )

        elif event == "error":
            raise ResponsesAPIError(
                str(data.get("message") or data.get("error") or "Responses 流式错误"),
                code=data.get("code"),
            )

    if not got_completed:
        # 流在未收到 completed 时结束：视为断流，由上层按重试策略处理
        raise ResponsesAPIError("Responses 响应流中断（未收到 completed 事件）", code="stream_incomplete")
