"""审核智能体模型调用统一出口。

三个审核智能体（approval / goal_review / workflow_review）历史上各自裸发
chat.completions HTTP 请求；接入 Responses 协议后统一收敛到本模块：

- Responses 模型（profile.api_protocol == "responses"）：走 APIClient 统一通道
  流式聚合（协议差异由 utils/api_client/responses 层处理，含 OAuth（codex）/
  静态 key 两种 auth；工具参数 nullable 适配与 sanitize 已内置）；
- Chat Completions 模型：保留既有裸 HTTP 行为（含 extra_params 降级重试一次），
  不改变既有语义。

返回 chat.completions 的 message 形状：
    {"content": str, "reasoning_content": str, "tool_calls": [...]}
失败抛出 ReviewModelCallError（message 为可读错误描述）。
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx

from modules.external_session import build_user_agent, resolve_ephemeral_headers

__all__ = ["ReviewModelCallError", "call_review_model"]


class ReviewModelCallError(RuntimeError):
    """审核模型调用失败（HTTP 错误 / 网络异常 / 流内 error chunk）。"""


async def _call_via_api_client(
    profile: Dict[str, Any],
    thinking: bool,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """经 APIClient 统一通道调用（Responses 协议模型；分发由 chat() 按 api_protocol 路由）。"""
    from utils.api_client import APIClient

    client = APIClient(thinking_mode=thinking, web_mode=True)
    client.model_key = str(profile.get("name") or "")
    client.apply_profile(profile)

    content = ""
    reasoning = ""
    tool_calls: List[Dict[str, Any]] = []
    async for chunk in client.chat(messages, tools=tools, stream=True):
        if chunk.get("error"):
            err = chunk["error"]
            if isinstance(err, dict):
                text = err.get("error_message") or err.get("error_text") or str(err)
            else:
                text = str(err)
            raise ReviewModelCallError(text)
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        if delta.get("content"):
            content += delta["content"]
        if delta.get("reasoning_content"):
            reasoning += delta["reasoning_content"]
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index")
            if idx is None:
                continue
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
    return {"content": content, "reasoning_content": reasoning, "tool_calls": tool_calls}


async def _call_regular(
    profile: Dict[str, Any],
    thinking: bool,
    extra_params: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    timeout_seconds: int,
) -> Dict[str, Any]:
    # 按思考模式选段；模型不支持 thinking 时回落 fast 段
    segment = profile.get("thinking") if thinking else None
    if not segment:
        segment = profile.get("fast") or {}
    url = str(segment.get("base_url") or "").strip()
    key = str(segment.get("api_key") or "").strip()
    model = str(segment.get("model_id") or "").strip()
    if not url or not key or not model:
        raise ReviewModelCallError("model profile incomplete (url/key/model)")

    endpoint = f"{url.rstrip('/')}/chat/completions"
    # 以「产品名/版本号」自标识；审核调用无对话连续性，每次审核生成一次性 session ID
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": build_user_agent()}
    headers.update(resolve_ephemeral_headers(url))

    req: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.0,
        **(extra_params or {}),
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        try:
            resp = await client.post(endpoint, headers=headers, json=req)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # 兼容某些模型/网关不接受额外参数：自动降级重试一次（去掉 extra_params）
            if extra_params:
                retry_req = {
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0.0,
                }
                retry_resp = await client.post(endpoint, headers=headers, json=retry_req)
                try:
                    retry_resp.raise_for_status()
                    resp = retry_resp
                except httpx.HTTPStatusError:
                    raise ReviewModelCallError(f"http {retry_resp.status_code}") from exc
            else:
                raise ReviewModelCallError(f"http {exc.response.status_code if exc.response else 'unknown'}") from exc
        except Exception as exc:
            raise ReviewModelCallError(str(exc)) from exc

    choice = ((resp.json().get("choices") or [{}])[0] or {}).get("message") or {}
    reasoning_content = (
        choice.get("reasoning_content")
        or choice.get("reasoning")
        or choice.get("thinking")
        or ""
    )
    return {
        "content": str(choice.get("content") or ""),
        "reasoning_content": str(reasoning_content or ""),
        "tool_calls": choice.get("tool_calls") or [],
    }


async def call_review_model(
    cfg: Dict[str, Any],
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    timeout_seconds: int,
) -> Dict[str, Any]:
    """按配置调一次审核模型，返回 message 形状；失败抛 ReviewModelCallError。"""
    profile = cfg.get("profile")
    if not isinstance(profile, dict):
        raise ReviewModelCallError("review agent model not configured")
    thinking = bool(cfg.get("thinking"))
    if str(profile.get("api_protocol") or "") == "responses":
        return await _call_via_api_client(profile, thinking, messages, tools)
    return await _call_regular(
        profile,
        thinking,
        dict(cfg.get("extra_params") or {}),
        messages,
        tools,
        int(timeout_seconds or 60),
    )
