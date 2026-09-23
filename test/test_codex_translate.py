#!/usr/bin/env python3
"""translate.py 映射逻辑单测（一次性验证脚本）。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.api_client.codex.translate import (
    _ReasoningCollector,
    build_responses_body,
    chat_request_to_input,
    flatten_tools,
    responses_sse_to_chat_chunks,
    sanitize_tool_arguments,
    split_system_messages,
    tool_required_map,
)

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} {detail}")
        FAIL.append(name)


# ---------- 请求映射 ----------
print("[请求映射]")
messages = [
    {"role": "system", "content": "系统提示A"},
    {"role": "user", "content": "你好"},
    {
        "role": "assistant",
        "content": "我来看一下",
        "reasoning_content": "摘要不应回传",
        "codex_reasoning_items": [
            {"type": "reasoning", "id": "rs_x", "encrypted_content": "ENC1", "summary": []}
        ],
        "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path":"a.py"}'}}
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "文件内容"},
    {"role": "tool", "tool_call_id": "call_orphan", "name": "ghost", "content": "孤儿结果"},
    {"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]},
]

system_text, rest = split_system_messages(messages)
check("system 分离", system_text == "系统提示A" and len(rest) == 5)

items = chat_request_to_input(rest)
types = [i.get("type") for i in items]
check("reasoning 回插在 assistant 消息前", types[1] == "reasoning" and types[2] == "message" and items[2].get("role") == "assistant", str(types))
reasoning_item = items[1]
check("reasoning 剥服务端 id", "id" not in reasoning_item and reasoning_item.get("encrypted_content") == "ENC1")
fc = [i for i in items if i.get("type") == "function_call"][0]
check("function_call 映射", fc["call_id"] == "call_1" and fc["name"] == "read_file" and "id" not in fc)
fco = [i for i in items if i.get("type") == "function_call_output"]
check("配对 tool 输出", len(fco) == 1 and fco[0]["output"] == "文件内容")
orphan_texts = [p["text"] for i in items if i.get("type") == "message" and i.get("role") == "user" for p in i["content"] if "孤儿" in p.get("text", "")]
check("孤儿 tool 输出降级", len(orphan_texts) == 1)
img_parts = items[-1]["content"]
check("图片 input_image", any(p.get("type") == "input_image" and p.get("image_url", "").startswith("data:") for p in img_parts))
all_text = json.dumps(items, ensure_ascii=False)
check("摘要未回传", "摘要不应回传" not in all_text)
check("无服务端 id 残留", '"rs_x"' not in all_text)

tools = [{"type": "function", "function": {"name": "t1", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
flat = flatten_tools(tools)
check("tools 拍平", flat[0]["name"] == "t1" and "function" not in flat[0])

# 可选参数 nullable 化（codex 约束解码适配：模型无法省略属性，用 null 表达「不需要」）
rich_tools = [{"type": "function", "function": {"name": "web_search", "description": "搜索", "parameters": {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "查询词"},
        "days": {"type": "integer", "description": "最近 N 天"},
        "include_domains": {"type": "array", "description": "域名过滤", "items": {"type": "string"}},
        "already_nullable": {"type": ["string", "null"], "description": "已是 null 联合"},
    },
    "required": ["query"],
}}}]
flat_rich = flatten_tools(rich_tools)
props = flat_rich[0]["parameters"]["properties"]
check("required 参数不动", props["query"]["type"] == "string" and "null" not in props["query"]["description"])
check("可选 integer 可空化", props["days"]["type"] == ["integer", "null"] and "null" in props["days"]["description"])
check("可选 array 可空化", props["include_domains"]["type"] == ["array", "null"])
check("已 nullable 不重复", props["already_nullable"]["type"] == ["string", "null"])
check("required 列表原样", flat_rich[0]["parameters"]["required"] == ["query"])

# 响应侧 null 清洗
rmap = tool_required_map(rich_tools)
check("required_map 构建", rmap == {"web_search": {"query"}}, str(rmap))
cleaned = sanitize_tool_arguments("web_search", '{"query":"x","days":null,"include_domains":null}', rmap)
check("null 可选参数被剥除", json.loads(cleaned) == {"query": "x"}, cleaned)
kept = sanitize_tool_arguments("web_search", '{"query":null,"days":3}', rmap)
check("required 的 null 保留 / 真值保留", json.loads(kept) == {"query": None, "days": 3}, kept)
check("未知工具放行", sanitize_tool_arguments("ghost", '{"a":null}', rmap) == '{"a":null}')
check("无 required_map 放行", sanitize_tool_arguments("web_search", '{"days":null}', None) == '{"days":null}')
check("非法 JSON 放行", sanitize_tool_arguments("web_search", '{bad', rmap) == '{bad')

body = build_responses_body(
    messages=messages, instructions="BASE", model_id="gpt-6-astra",
    tools=tools, effort="high", summary="detailed", max_output_tokens=1024,
    session_id="sid",
)
check("body 硬约束", body["store"] is False and body["stream"] is True and body["include"] == ["reasoning.encrypted_content"])
check("instructions 拼接", body["instructions"].startswith("BASE") and "系统提示A" in body["instructions"])
check("reasoning 参数", body["reasoning"] == {"effort": "high", "summary": "detailed"})
check("无采样参数", "temperature" not in body and "top_p" not in body)


# ---------- 响应映射 ----------
print("[响应映射]")
sse_events = [
    {"type": "response.created"},
    # 摘要两段：走 text.done 权威全文（delta 仅缓冲）
    {"type": "response.reasoning_summary_part.added", "item_id": "rs_0", "summary_index": 0, "part": {"type": "summary_text", "text": ""}},
    {"type": "response.reasoning_summary_text.delta", "item_id": "rs_0", "summary_index": 0, "delta": "**Planning"},
    {"type": "response.reasoning_summary_text.delta", "item_id": "rs_0", "summary_index": 0, "delta": " the search**"},
    {"type": "response.reasoning_summary_text.done", "item_id": "rs_0", "summary_index": 0, "text": "**Planning the search**"},
    {"type": "response.reasoning_summary_part.done", "item_id": "rs_0", "summary_index": 0, "part": {"type": "summary_text", "text": "**Planning the search**"}},
    {"type": "response.reasoning_summary_text.done", "item_id": "rs_0", "summary_index": 1, "text": "**Executing** query"},
    {"type": "response.output_text.delta", "delta": "正文1"},
    # fc_1：delta 缓冲 + output_item.done 统一下发（含 null 填充待清洗）
    {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_a", "name": "read_file"}},
    {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"path":"a.py",'},
    {"type": "response.output_item.added", "item": {"type": "function_call", "id": "fc_2", "call_id": "call_b", "name": "write_file"}},
    {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"offset":null}'},
    {"type": "response.function_call_arguments.delta", "item_id": "fc_2", "delta": "{}"},
    {"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_a", "name": "read_file", "arguments": '{"path":"a.py","offset":null}'}},
    # fc_2 故意不给 output_item.done：走 completed 兜底冲刷路径
    {"type": "response.output_item.done", "item": {"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}},
    {"type": "response.completed", "response": {
        "output": [{"type": "reasoning", "id": "rs_1", "encrypted_content": "ENC", "summary": []}],
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                  "input_tokens_details": {"cached_tokens": 30},
                  "output_tokens_details": {"reasoning_tokens": 20}},
    }},
]


async def run_sse():
    async def lines():
        for e in sse_events:
            yield "data: " + json.dumps(e)

    collector = _ReasoningCollector()
    chunks = []
    rmap = tool_required_map([{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "offset": {"type": "integer"}}, "required": ["path"]}}}])
    async for c in responses_sse_to_chat_chunks(lines(), collector, rmap):
        chunks.append(c)
    return chunks, collector


chunks, collector = asyncio.run(run_sse())
deltas = [c["choices"][0].get("delta", {}) for c in chunks if "choices" in c]
check("role 首帧", deltas[0].get("role") == "assistant")
reasoning_texts = [d.get("reasoning_content") for d in deltas if d.get("reasoning_content")]
check("摘要两段空行分隔", reasoning_texts == ["Planning the search", "\n\nExecuting query"], str(reasoning_texts))
check("摘要已剥 md 加粗", all("**" not in t for t in reasoning_texts))
check("content 翻译", any(d.get("content") == "正文1" for d in deltas))
tc_deltas = [d["tool_calls"] for d in deltas if d.get("tool_calls")]
check("并行工具 index 归位",
      any(tc[0].get("index") == 1 and tc[0].get("id") == "call_b" for tc in tc_deltas))
args_by_index = {}
for tc in tc_deltas:
    idx = tc[0]["index"]
    frag = tc[0].get("function", {}).get("arguments", "")
    args_by_index[idx] = args_by_index.get(idx, "") + frag
check("done 路径参数清洗（null 剥除）", json.loads(args_by_index.get(0) or "{}") == {"path": "a.py"}, str(args_by_index))
check("completed 兜底冲刷路径", args_by_index.get(1) == "{}", str(args_by_index))
final = chunks[-1]
check("finish_reason=tool_calls", final["choices"][0].get("finish_reason") == "tool_calls")
check("usage 映射", final["usage"]["prompt_tokens"] == 100 and final["usage"]["prompt_tokens_details"]["cached_tokens"] == 30)
check("reasoning 收集（含去重）", len(collector.items) == 1 and collector.items[0].get("encrypted_content") == "ENC" and "id" not in collector.items[0])


async def run_broken():
    async def lines():
        yield "data: " + json.dumps({"type": "response.created"})
        yield "data: " + json.dumps({"type": "response.output_text.delta", "delta": "半截"})
    chunks = []
    try:
        async for c in responses_sse_to_chat_chunks(lines()):
            chunks.append(c)
        return chunks, None
    except Exception as e:
        return chunks, e


_, err = asyncio.run(run_broken())
check("断流抛 stream_incomplete", err is not None and getattr(err, "code", None) == "stream_incomplete")

print()
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("全部通过 ✓")
