"""Regenerate with `python3 -B responses_cases.py --write`, or run to verify.

The oracle executes the current Python translator and timer against synthetic
requests and SSE lines. It uses the standard library and makes no HTTP requests.
"""

import asyncio
import importlib.machinery
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("responses_fixtures.json")
sys.path.insert(0, str(ROOT))

# Load the pure translator under its real import name, bypassing the parent
# package initializer that assembles the full HTTP client.
for name, relative_path in (
    ("utils.api_client", "utils/api_client"),
    ("utils.api_client.responses", "utils/api_client/responses"),
):
    package = types.ModuleType(name)
    package.__path__ = [str(ROOT / relative_path)]
    package.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    sys.modules[name] = package

from utils.api_client.responses import translate

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect",
            "description": "synthetic fixture",
            "parameters": {
                "$schema": "ignored",
                "type": "object",
                "properties": {
                    "z": {"type": "string", "description": "已有 null 提示"},
                    "if": {"type": "boolean", "if": {"const": True}},
                    "keep": {"type": "number", "description": "required"},
                    "union": {"type": ["array"], "items": [{"type": "object", "propertyNames": {}}]},
                    "already": {"type": ["number", "null"]},
                    "always_null": {"type": "null"},
                    "raw": False,
                    "ref": {"$ref": "#/$defs/then"},
                },
                "$defs": {"then": {"type": "object", "properties": {"else": {"type": "string", "$id": "drop"}}}},
                "definitions": {"contains": {"anyOf": [{"type": "number", "minContains": 1}, True]}},
                "required": ["keep"],
            },
            "strict": False,
        },
    },
    {"name": "empty", "strict": 1},
    {"name": "default", "parameters": False},
    {"type": "function", "name": "ignored-outer-name"},
    None,
]

HISTORY = [
    None,
    {"role": "system", "content": "custom rules"},
    {"role": "system", "content": {"z": "约定", "a": [{"plan": True}]}},
    {"role": "system", "content": ""},
    {"role": "tool", "tool_call_id": "call1", "content": {"z": 2, "a": "提前到达"}},
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "混合内容"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,c3ludGhldGlj", "detail": "low"}},
            {"type": "image_url", "image_url": "https://image.example.invalid/x"},
            {"type": "image_url", "image_url": {}},
            {"type": "video_url", "video_url": "data:video/mp4;base64,eA=="},
            {"type": "unknown", "text": "drop"},
            "  字符串 ",
            False,
            [None, "a'b", "空 格"],
            " ",
        ],
    },
    {"role": "user", "content": None},
    {
        "role": "assistant",
        "content": [{"type": "text", "text": "答复"}, {"type": "video_url"}],
        "reasoning_content": "display only",
        "responses_reasoning_items": [
            {"type": "reasoning", "id": "rs1", "encrypted_content": "opaque-fixture", "summary": []},
            {"type": "message", "content": "drop"},
        ],
        "tool_calls": [
            {"id": "call1", "function": {"name": "inspect", "arguments": '{"keep":7}'}},
            {"id": "call2", "function": {"name": "empty"}},
            {"id": "invalid", "function": {}},
            {"function": {"name": "inspect"}},
        ],
    },
    {"role": "tool", "tool_call_id": "call1", "content": [{"type": "image_url"}, {"type": "text", "text": "结果"}]},
    {"role": "tool", "tool_call_id": "call2", "content": None},
    {"role": "tool", "name": "lost", "content": [17, None, {"type": "video_url"}]},
    {"role": "developer", "content": "only system handling adds a developer message"},
]


def event(at_ms, kind, **fields):
    return [at_ms, "data: " + json.dumps(dict(type=kind, **fields), ensure_ascii=False)]


REASONING = {
    "type": "reasoning",
    "id": "rs-first",
    "summary": [{"type": "summary_text", "text": "原生摘要"}],
    "encrypted_content": "ciphertext-fixture",
}
REASONING_REORDERED = {
    "encrypted_content": "ciphertext-fixture",
    "id": "rs-repeated",
    "summary": [{"text": "原生摘要", "type": "summary_text"}],
    "type": "reasoning",
}
LONG_REASONING = {"type": "reasoning", "encrypted_content": "思" * 201}


def function_item(item_id, name, **fields):
    return dict(type="function_call", id=item_id, call_id=f"call-{item_id}", name=name, **fields)


def request_cases():
    cases = {
        "ordinary": dict(model_id="gpt-6.2", messages=HISTORY, instructions="base", tools=TOOLS,
                         effort="high", verbosity="low", max_output_tokens=123, session_id="fixture-session",
                         use_developer_prompt=True),
        "frozen": dict(model_id="o4-mini", messages=[
            {"role": "system", "content": "frozen", "_frozen_system_prompt": True},
            {"role": "system", "content": "suffix"},
            {"role": "user", "content": "hello"},
        ], instructions="provider should not repeat"),
        "marker_not_first": dict(model_id="o4-mini", messages=[
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "normal", "_frozen_system_prompt": True},
        ], instructions=""),
        "absent_optional_parameters": dict(model_id="external", messages=[{"role": "system"}],
                                           instructions="", effort="", summary="", max_output_tokens=-5,
                                           verbosity="", session_id=""),
        "empty_instructions_developer_mode": dict(model_id="gpt-6.2", messages=[], instructions="",
                                                  use_developer_prompt=True, max_output_tokens=0),
        "empty_frozen_system": dict(model_id="gpt-6.2", messages=[
            {"role": "system", "content": "", "_frozen_system_prompt": True},
        ], instructions="base", use_developer_prompt=True),
    }
    return [dict(name=name, input=values, expected=translate.build_responses_body(**values))
            for name, values in cases.items()]


def policy_cases():
    profiles = [
        {},
        {"responses_auth": "codex_oauth", "model_id": "private"},
        {"provider_id": "openai-api"},
        {"provider_id": "openai-codex"},
        {"model_id": " GPT-6.2 ", "base_url": "https://proxy.example.invalid/v1"},
        {"model_id": "chatgpt-latest"},
        {"model_id": "gpt-oss-120b", "base_url": "https://proxy.example.invalid/v1"},
        {"model_id": "o1"},
        {"model_id": "o4-mini"},
        {"model_id": "o9-x"},
        {"model_id": "o10-mini"},
        {"base_url": "https://API.OPENAI.COM:443/v1"},
        {"base_url": "https://resource.openai.azure.com/openai"},
        {"base_url": "//gateway.ai.cloudflare.com/v1"},
        {"base_url": "https://evilopenai.com"},
        {"base_url": "https://api.openai.com.example.invalid"},
        {"base_url": "api.openai.com/v1"},
        {"base_url": "https://[broken.openai.com"},
        {"base_url": "https://api.openai.com@thirdparty.example.invalid"},
    ]
    return [dict(input=profile,
                 developer=translate.should_use_developer_prompt(**profile),
                 cache=translate.should_send_prompt_cache_key(**{key: value for key, value in profile.items()
                                                                 if key != "model_id"}))
            for profile in profiles]


def argument_cases():
    required = translate.tool_required_map(TOOLS)
    inputs = [
        ("inspect", ' {"z":null,"keep":null,"msg":"文本","nested":{"b":null,"a":false},"e":1e-6,"large":1e16,"zero":-0.0} '),
        ("empty", {"c": None, "z": [None, False], "a": {}}),
        ("inspect", " {invalid JSON} "),
        ("inspect", " [null, 1] "),
        ("unknown", ' {"z":null} '),
        (None, None),
        ("inspect", False),
        ("empty", "null"),
        ("inspect", '{"x":null,"x":2,"a":4,"a":null,"n":0,"b":false,"s":""}'),
    ]
    return [dict(name=name, input=value, expected=translate.sanitize_tool_arguments(name, value, required))
            for name, value in inputs]


def stream_cases():
    return {
        "tool_summary_usage": [
            [0, ""], [1, ": heartbeat"], [2, "event: response.created"], [3, "data: broken"],
            [4, "data: [1,2]"], [5, "data: null"], [6, " data: {}"], [7, "data:    "],
            event(10, "response.created"),
            event(20, "response.reasoning_text.delta", delta="raw reasoning"),
            event(30, "response.reasoning_summary_text.delta", item_id="rs-first", summary_index=0, delta="wrong fallback"),
            event(40, "response.reasoning_summary_text.done", item_id="rs-first", summary_index=0, text=" **原生摘要** "),
            event(45, "response.reasoning_summary_text.delta", item_id="a", summary_index="2", delta=" **第二段** "),
            event(46, "response.reasoning_summary_text.done", item_id="a", summary_index=2),
            event(48, "response.reasoning_summary_text.done", text="****"),
            event(50, "response.output_item.done", item=REASONING),
            event(60, "response.output_item.added", output_index=1, item=function_item("one", "inspect")),
            event(70, "response.function_call_arguments.delta", item_id="one", delta='{"z": null, '),
            event(80, "response.output_item.added", output_index=2, item=function_item("two", "empty")),
            event(90, "response.function_call_arguments.delta", item_id="two", delta='{"abandoned":0}'),
            event(100, "response.function_call_arguments.delta", item_id="one", delta='"keep": null, "a": "值"}'),
            event(110, "response.function_call_arguments.done", item_id="one"),
            event(120, "response.output_item.done", item=function_item("two", "empty", arguments='{"remove":null,"final":false}')),
            event(130, "response.output_item.done", item=function_item("one", "inspect", arguments="")),
            event(150, "response.output_text.delta", item_id="text", content_index=0, delta="正文"),
            event(180, "response.output_text.done", item_id="text", content_index=0),
            event(200, "response.completed", response={
                "output": [REASONING_REORDERED, {"type": "message", "id": "text"}, LONG_REASONING,
                           dict(LONG_REASONING, encrypted_content=LONG_REASONING["encrypted_content"] + "tail")],
                "usage": {"input_tokens": 8, "output_tokens": 5, "total_tokens": 0,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 2}},
            }),
            [201, "data: [DONE]\r"],
        ],
        "completion_flushes_in_order": [
            event(0, "response.function_call_arguments.delta", delta="unmatched"),
            event(10, "response.output_item.done", item=function_item("none", "empty", arguments="{}")),
            event(20, "response.output_item.added", item=function_item("one", "inspect")),
            event(30, "response.output_item.added", item=function_item("two", "empty")),
            event(40, "response.function_call_arguments.delta", delta='{"second":false,"z":null}'),
            event(50, "response.function_call_arguments.delta", item_id="one", delta='{"first":true,"z":null}'),
            event(60, "response.reasoning_summary_text.delta", item_id="z", summary_index=1, delta="last"),
            event(70, "response.reasoning_summary_text.delta", item_id="a", summary_index=2, delta="middle"),
            event(80, "response.reasoning_summary_text.delta", item_id="a", summary_index=0, delta=" **first** "),
            event(90, "response.completed", response={"usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}),
        ],
        "overlapping_content_and_timing": [
            event(0, "response.output_item.added", output_index=0, item={"type": "message", "id": "one"}),
            event(10, "response.refusal.delta", item_id="one", delta="refusal timer only"),
            event(20, "response.output_text.delta", item_id="one", content_index=1, delta="piece"),
            event(30, "response.refusal.done", output_index=0),
            event(40, "response.output_text.done", item_id="one", content_index=1),
            event(70, "response.output_text.delta", item_id="two", delta="unfinished"),
            event(100, "response.completed", response={
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 20,
                          "input_tokens_details": {"cached_tokens": None}, "output_tokens_details": []},
            }),
        ],
        "timing_below_reporting_threshold": [
            event(0, "response.output_text.delta", delta="tiny"),
            event(0.3, "response.output_text.done"),
            event(20, "response.completed"),
        ],
        "no_text_or_usage": [event(0, "response.completed", response=None)],
        "business_failure": [
            event(1, "response.created"),
            event(2, "response.failed", response={"error": {"code": "example_code", "message": "fixture failure"}}),
            event(3, "response.completed"),
        ],
        "failed_event_default": [event(0, "response.failed", response=None)],
        "top_level_error": [event(0, "error", code="stream_error", error={"message": "synthetic"})],
        "error_event_default": [event(0, "error")],
        "eof_after_done_without_completed": [
            event(1, "response.output_text.delta", delta="partial"),
            event(2, "response.incomplete", response={"status": "incomplete"}),
            [3, "data: [DONE]"],
        ],
    }


async def run_stream(name, source):
    collector = translate._ReasoningCollector()
    steps = []
    last_line = None
    now = 0
    error = None

    async def lines():
        nonlocal now, last_line
        for index, (at_ms, line) in enumerate(source):
            last_line = index
            now = at_ms / 1_000
            steps.append([])
            yield line
        last_line = None

    timer = translate.ResponsesOutputTimer(lambda: now)
    with patch.object(translate, "ResponsesOutputTimer", return_value=timer):
        try:
            async for chunk in translate.responses_sse_to_chat_chunks(lines(), collector, translate.tool_required_map(TOOLS)):
                steps[-1].append(chunk)
        except translate.ResponsesAPIError as exc:
            error = dict(line=last_line, code=exc.code, message=str(exc))
    return dict(name=name, lines=source, steps=steps, reasoning=collector.items, error=error)


async def build_fixtures():
    return dict(requests=request_cases(), policies=policy_cases(), tools=TOOLS,
                required={name: sorted(fields) for name, fields in translate.tool_required_map(TOOLS).items()},
                argument_cases=argument_cases(),
                streams=[await run_stream(name, source) for name, source in stream_cases().items()])


if __name__ == "__main__":
    serialized = json.dumps(asyncio.run(build_fixtures()), indent=2, ensure_ascii=False) + "\n"
    if sys.argv[1:] == ["--write"]:
        FIXTURES.write_text(serialized, encoding="utf-8")
    elif sys.argv[1:]:
        sys.exit(f"usage: {Path(__file__).name} [--write]")
    elif FIXTURES.read_text(encoding="utf-8") != serialized:
        sys.exit(f"Python oracle differs from {FIXTURES.name}. Review changes before regenerating.")
    else:
        print(f"Python oracle matches {FIXTURES.name}")
