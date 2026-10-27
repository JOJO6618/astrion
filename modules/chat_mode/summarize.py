"""One internal summary request, without changing history or running tools."""
import time
from uuid import uuid4

from config.model_profiles import get_model_profile
from utils.api_client import APIClient
from utils.summary_request import request_summary
from utils.token_usage import extract_usage_payload
from modules.api_pricing import capture_price
from modules.chat_mode.context import ChatContext
from modules.external_session import resolve_context_headers

SUMMARY_INSTRUCTION = (
    "请总结这段对话，供后续模型准确继续。保留用户原始目标、确认的约束、关键决策、"
    "已有事实和数据、工具实际结果、尚未完成的工作与下一步。明确区分已验证与推测。"
    "不要声称执行过未执行的操作，不要执行工具或建议切换模式。只输出总结正文。"
)


async def summarize(storage, document, messages, *, instruction=SUMMARY_INSTRUCTION,
                    api_client=None, preferences_dir=None):
    metadata = document["metadata"]
    model_key = metadata.get("model_key")
    turns = metadata["chat"]["turns"]
    if not turns:
        raise ValueError("No Chat content to summarize")
    context = ChatContext(storage, document["id"], turns[-1]["id"])
    context.conversation_history = messages
    client = api_client
    if client is None:
        if preferences_dir is None:
            raise ValueError("Chat summary requires the account preferences directory")
        profile = get_model_profile(model_key)
        if not profile:
            raise ValueError("Unknown Chat summary model")
        client = APIClient(thinking_mode=bool(metadata.get("thinking_mode")), web_mode=True)
        client.model_key = model_key
        # Install dynamic headers before apply_profile chains provider headers.
        client.extra_headers_resolver = lambda base_url: resolve_context_headers(
            base_url, context, base_dir=preferences_dir,
        )
        client.apply_profile(profile)
        client.reasoning_effort = metadata.get("reasoning_effort")
    api_messages = [{"role": "system", "content": instruction}, *context.build_api_history(),
                    {"role": "user", "content": "现在请给出供后续继续使用的总结。"}]
    # Explicit accounting uses the same ledger as normal Chat/Agent calls.
    request_id = uuid4().hex
    price = capture_price(model_key)
    usage = None

    def collect_usage(chunk):
        nonlocal usage
        usage = extract_usage_payload(chunk) or usage

    started = time.monotonic()
    try:
        return await request_summary(client, api_messages, [], on_chunk=collect_usage)
    finally:
        if usage:
            context.apply_usage_statistics(usage, price=price, request_id=request_id,
                duration_ms=int((time.monotonic() - started) * 1000), speed_sample={})
        else:
            context.record_unavailable_usage(price=price, request_id=request_id)
