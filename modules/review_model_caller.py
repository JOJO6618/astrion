"""审核智能体模型调用统一出口。

三个审核智能体（approval / goal_review / workflow_review）统一经 APIClient 流式
调用：Chat Completions 与 Responses 共用同一路径，协议差异、思考段额外参数、
工具参数适配均由 APIClient 处理（2026-11-06 起不再保留裸 HTTP 分支）。

- 思考：固定 thinking_mode=True；模型没有独立思考段时 APIClient 沿用 fast 段配置。
- 推理强度：不设置 reasoning_effort，走模型／服务商默认档位（codex 按模型默认档）。
- 超时：整次请求受 asyncio.wait_for(timeout_seconds) 约束，两种协议一致。
- 计费：传入 ReviewBilling 时，每次请求用独立 UUID 写入所属对话账本
  （actor_id=review_agent:<agent_key>，kind=review_agent），不计入目标模式 Token 上限。

返回 chat.completions 的 message 形状：
    {"content": str, "reasoning_content": str, "tool_calls": [...]}
失败抛出 ReviewModelCallError（message 为可读错误描述，details 保留服务商错误码供分类）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from modules.external_session import resolve_ephemeral_headers

__all__ = [
    "ReviewModelCallError",
    "ReviewBilling",
    "review_billing_for",
    "call_review_model",
]

logger = logging.getLogger(__name__)

REVIEW_ACTOR_KIND = "review_agent"


class ReviewModelCallError(RuntimeError):
    """Preserve provider codes for goal recovery without exposing credentials."""

    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class ReviewBilling:
    """审核请求的账本归属：哪个对话、哪个审核智能体。"""

    data_dir: str
    conversation_id: str
    agent_key: str
    on_committed: Optional[Callable[[], None]] = None


def review_billing_for(web_terminal: Any, conversation_id: Optional[str], agent_key: str) -> Optional[ReviewBilling]:
    """按审核所属对话构造账本归属；对话身份缺失时返回 None（不计费、记 warning）。"""
    cid = str(conversation_id or "").strip()
    context = getattr(web_terminal, "context_manager", None)
    data_dir = getattr(context, "data_dir", None)
    if not cid or cid.startswith("temp_") or not data_dir:
        logger.warning("review agent %s request not billed: conversation identity unavailable", agent_key)
        return None

    def _refresh() -> None:
        # 只刷新正在显示该对话的上下文；费用面板另有定时读取兜底
        if getattr(context, "current_conversation_id", None) == cid:
            context.safe_broadcast_token_update()

    return ReviewBilling(str(data_dir), cid, agent_key, _refresh)


def _check_profile(profile: Dict[str, Any]) -> None:
    """Chat Completions 模型缺 url/key/model 时 APIClient 会静默不发请求，这里提前报配置错误。"""
    if str(profile.get("api_protocol") or "") == "responses":
        return  # codex_oauth 等不需要静态 key，由 Responses 通道自行校验
    segment = profile.get("thinking") or profile.get("fast") or {}
    url = str(segment.get("base_url") or "").strip()
    key = str(segment.get("api_key") or "").strip()
    model = str(segment.get("model_id") or "").strip()
    if not url or not key or key.startswith("your-") or not model:
        raise ReviewModelCallError("model profile incomplete (url/key/model)")


def _record_cost(billing: ReviewBilling, request_id: str, usage, price, started_at: float) -> None:
    from modules.api_pricing import calculate_cost
    from modules.conversation_costs import record_request

    entry = calculate_cost(usage, price)
    entry["duration_ms"] = max(0, int((time.monotonic() - started_at) * 1000))
    committed = record_request(
        billing.data_dir, billing.conversation_id, request_id,
        f"{REVIEW_ACTOR_KIND}:{billing.agent_key}", billing.agent_key, REVIEW_ACTOR_KIND, entry,
        count_goal_usage=False,
    )
    if committed and billing.on_committed:
        billing.on_committed()


async def call_review_model(
    cfg: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    timeout_seconds: int,
    *,
    billing: Optional[ReviewBilling] = None,
) -> Dict[str, Any]:
    """按配置调一次审核模型，返回 message 形状；失败抛 ReviewModelCallError。"""
    from utils.api_client import APIClient
    from utils.token_usage import extract_usage_payload

    profile = cfg.get("profile")
    if not isinstance(profile, dict):
        raise ReviewModelCallError("review agent model not configured")
    _check_profile(profile)
    model_key = str(profile.get("name") or "")

    price = None
    if billing is not None:
        from modules.api_pricing import capture_price
        try:
            price = await asyncio.to_thread(capture_price, model_key)
        except Exception:
            logger.warning("review agent price capture failed for %s", model_key, exc_info=True)
            price = {"model_key": model_key, "provider_id": profile.get("provider_id"),
                     "rates": {}, "source": None, "fetched_at": None}

    client = APIClient(thinking_mode=True, web_mode=True)
    client.model_key = model_key
    # 审核调用无对话连续性：每次请求生成一次性 session 头（需在 apply_profile 前设置，
    # 以便与 profile 静态头链式合并）
    client.extra_headers_resolver = lambda base_url: resolve_ephemeral_headers(base_url)
    client.apply_profile(profile)

    request_id = str(uuid4())
    started_at = time.monotonic()
    state = {"usage": None, "received": False, "finished": False, "chunks": 0}
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []
    succeeded = False

    async def _consume() -> None:
        stream = client.chat(messages, tools=tools, stream=True)
        try:
            async for chunk in stream:
                if not isinstance(chunk, dict):
                    continue
                state["chunks"] += 1
                if chunk.get("error"):
                    err = chunk["error"]
                    if isinstance(err, dict):
                        text = err.get("error_message") or err.get("error_text") or str(err)
                    else:
                        text = str(err)
                    raise ReviewModelCallError(text, details=err if isinstance(err, dict) else None)
                usage = extract_usage_payload(chunk)
                if usage is not None:
                    state["usage"] = usage
                for choice in chunk.get("choices") or []:
                    if choice.get("finish_reason") is not None:
                        state["finished"] = True
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content_parts.append(delta["content"])
                        state["received"] = True
                    if delta.get("reasoning_content"):
                        reasoning_parts.append(delta["reasoning_content"])
                        state["received"] = True
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index")
                        if idx is None:
                            continue
                        state["received"] = True
                        while len(tool_calls) <= idx:
                            tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                        existing = tool_calls[idx]
                        if tc.get("id"):
                            existing["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            existing["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            existing["function"]["arguments"] += fn["arguments"]
        finally:
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass

    try:
        try:
            await asyncio.wait_for(_consume(), timeout=max(1, int(timeout_seconds or 60)))
        except asyncio.TimeoutError as exc:
            raise ReviewModelCallError(
                f"review model request timed out after {int(timeout_seconds or 60)}s",
                details={"error_type": "timeout"},
            ) from exc
        if not state["chunks"]:
            raise ReviewModelCallError("review model returned no response", details={"error_type": "empty_response"})
        succeeded = True
    finally:
        # 与思考摘要同口径：成功响应、或已拿到 usage／部分输出的请求都入账；
        # 无 usage 的记为未计价。完全没有响应的认证／网络失败不登记为付费请求。
        if billing is not None and (succeeded or state["usage"] is not None
                                    or state["received"] or state["finished"]):
            try:
                await asyncio.to_thread(_record_cost, billing, request_id, state["usage"], price, started_at)
            except Exception:
                logger.warning("review agent cost record failed for %s", billing.conversation_id, exc_info=True)

    return {
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
        "tool_calls": tool_calls,
    }
