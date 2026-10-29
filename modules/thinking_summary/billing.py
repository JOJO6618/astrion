"""Meter auxiliary summaries without changing main-agent token/speed counters."""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

from modules.api_pricing import calculate_cost, capture_price
from modules.conversation_costs import record_request
from utils.summary_request import request_summary
from utils.token_usage import extract_usage_payload


async def request_with_cost(client, messages: list[dict], *, terminal,
                            conversation_id: str, model_key: str) -> str:
    from . import log_failure

    context = terminal.context_manager
    data_dir = context.data_dir
    request_id = str(uuid4())
    try:
        price = await asyncio.to_thread(capture_price, model_key)
    except Exception:
        log_failure("capture_price")
        price = {"model_key": model_key, "provider_id": None, "rates": {},
                 "source": None, "fetched_at": None}
    usage = None
    succeeded = False
    received_content = False
    finished = False
    started_at = time.monotonic()

    def observe(chunk: dict) -> None:
        nonlocal usage, received_content, finished
        current = extract_usage_payload(chunk)
        if current is not None:
            usage = current
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            received_content = received_content or bool(
                delta.get("content") or delta.get("reasoning_content")
            )
            finished = finished or choice.get("finish_reason") is not None

    try:
        content = await request_summary(client, messages, [], on_chunk=observe)
        succeeded = True
        return content
    finally:
        # A successful but rejected/empty display phrase still incurred a model
        # request. Partial output without final usage remains explicitly unpriced.
        # Authentication/network failures before any response aren't billed here.
        if succeeded or usage is not None or received_content or finished:
            try:
                entry = calculate_cost(usage, price)
                entry["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
                committed = record_request(
                    data_dir, conversation_id, request_id,
                    "thinking_summary", "", "thinking_summary", entry,
                    count_goal_usage=False,
                )
                if committed:
                    # Reuse the complete existing payload; do not send zeroed or
                    # auxiliary counters into the main-agent token update fields.
                    context.safe_broadcast_token_update()
            except Exception:
                log_failure("record_cost")
