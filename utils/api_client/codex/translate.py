"""兼容 re-export：协议转换层已泛化搬家到 ``utils/api_client/responses/translate.py``
（2026-09-25 协议泛化，设计文档 docs/provider_protocol_generalization.md）。

本模块仅保持存量导入路径（``codex/mixin.py``、``test/test_codex_translate.py``）
不变；新代码请直接从 ``utils.api_client.responses.translate`` 导入。
"""

from utils.api_client.responses.translate import (  # noqa: F401
    ResponsesAPIError,
    _ReasoningCollector,
    _clean_summary_text,
    _flatten_content_to_text,
    _map_usage,
    _optional_params_nullable,
    _strip_server_ids,
    _user_content_to_parts,
    build_responses_body,
    chat_request_to_input,
    flatten_tools,
    responses_sse_to_chat_chunks,
    sanitize_tool_arguments,
    split_system_messages,
    tool_required_map,
)

# 旧名兼容：CodexAPIError 即 ResponsesAPIError
CodexAPIError = ResponsesAPIError
