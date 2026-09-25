"""通用 OpenAI Responses API 适配层（2026-09-25 协议泛化）。

从 ``utils/api_client/codex/`` 泛化而来：codex 子包保留 OAuth/模型发现/
instructions 等订阅管理面，纯协议转换统一收敛到本子包。

设计文档：``docs/provider_protocol_generalization.md``。
"""

from utils.api_client.responses.translate import (
    ResponsesAPIError,
    _ReasoningCollector,
    build_responses_body,
    chat_request_to_input,
    flatten_tools,
    responses_sse_to_chat_chunks,
    sanitize_tool_arguments,
    split_system_messages,
    tool_required_map,
)

__all__ = [
    "ResponsesAPIError",
    "_ReasoningCollector",
    "build_responses_body",
    "chat_request_to_input",
    "flatten_tools",
    "responses_sse_to_chat_chunks",
    "sanitize_tool_arguments",
    "split_system_messages",
    "tool_required_map",
]
