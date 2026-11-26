"""Capture the Python Responses mixin's HTTP requests with synthetic OAuth.

Run from the repository root with PYTHONPATH=. to print the JSON oracle,
or pass --check to verify the committed oracle. No network is used.
"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from utils.api_client import APIClient
from utils.api_client.codex.models import _profile_from_model_info


def cases():
    return [
        {
            "model": "fixture",
            "session_id": "frozen-session",
            "account": "chatgpt-fixture-a",
            "instructions": "Current provider prefix and bridge.",
            "requested_effort": "xhigh",
            "max_output_tokens": 875,
            "fast": True,
            "model_info": {
                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                "default_reasoning_level": "high",
                "service_tiers": [{"id": "priority"}],
            },
            "messages": [
                {"role": "system", "content": "Original frozen instructions.\n\nAstrion bridge.",
                 "_frozen_system_prompt": True},
                {"role": "user", "content": [{"type": "text", "text": "请原样返回你好。"}]},
            ],
            "tools": [{"type": "function", "function": {
                "name": "echo", "description": "Return the supplied text",
                "parameters": {"type": "object", "required": ["text"], "properties": {
                    "text": {"type": "string"}, "prefix": {"type": "string"},
                }},
            }}],
        },
        {
            "model": "fixture",
            "session_id": "fresh-session",
            "account": "chatgpt-fixture-b",
            "instructions": "Provider prefix and bridge.",
            "requested_effort": " low ",
            "max_output_tokens": None,
            "fast": True,
            "model_info": {"additional_speed_tiers": ["fast"]},
            "messages": [
                {"role": "system", "content": "Chat instructions."},
                {"role": "user", "content": "Follow up"},
            ],
            "tools": [],
        },
    ]


async def request_for(case):
    info = case["model_info"]
    profile = _profile_from_model_info(case["model"], info, "selected-connection", "Synthetic")
    client = APIClient(thinking_mode=True)
    client.apply_profile(profile)
    client.reasoning_effort = case["requested_effort"]
    client.chatgpt_fast_mode = case["fast"]
    client.thinking_max_tokens = case["max_output_tokens"]
    client._responses_session_id_value = case["session_id"]
    models = Mock(ensure_fresh=AsyncMock(), get_model_info=Mock(return_value=info))
    auth = Mock(get_access_token=AsyncMock(return_value="synthetic-oauth-token"),
                get_account_id=Mock(return_value=case["account"]))
    requests = []

    async def lines():
        yield 'data: {"type":"response.completed","response":{"output":[]}}'
        yield ""

    @asynccontextmanager
    async def stream(method, url, *, json, headers):
        requests.append({"method": method, "url": url, "body": json, "headers": headers})
        yield SimpleNamespace(status_code=200, aiter_lines=lines)

    @asynccontextmanager
    async def transport_context(*args, **kwargs):
        yield SimpleNamespace(stream=stream)

    with patch.object(client, "_codex_models_manager", return_value=models), \
            patch.object(client, "_codex_auth_manager", return_value=auth), \
            patch.object(client, "_build_responses_instructions", return_value=case["instructions"]), \
            patch("utils.api_client.responses.mixin.httpx.AsyncClient", transport_context), \
            patch("utils.api_client.codex.settings.resolve_proxy", return_value=None):
        chunks = [chunk async for chunk in client.chat_responses(case["messages"], tools=case["tools"])]
    assert all("error" not in chunk for chunk in chunks), chunks
    assert len(requests) == 1, requests
    return requests[0]


async def main():
    captures = [{"options": case, "request": await request_for(case)} for case in cases()]
    text = json.dumps(captures, ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        assert Path(__file__).with_name("python_requests.json").read_text(encoding="utf-8") == text
    else:
        print(text, end="")


if __name__ == "__main__":
    asyncio.run(main())
