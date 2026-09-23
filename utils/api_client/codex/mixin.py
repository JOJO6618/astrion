"""APIClient 的 Codex 通道：``chat_codex()`` 产出与 ``chat()`` 完全同形的 chunk。

分发入口在 ``APIClientChatMixin.chat()`` 开头（provider_type == "codex"）。
凭证、模型发现、instructions、映射全部委托 codex 子包各模块；
本文件只负责请求编排、401 自愈、错误形状对齐与 reasoning items 落属性。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from modules.i18n import tr

from utils.api_client.codex.auth import CodexAuthError, get_auth_manager
from utils.api_client.codex.models import get_models_manager
from utils.api_client.codex.prompt import build_instructions
from utils.api_client.codex.settings import RESPONSES_URL, resolve_proxy
from utils.api_client.codex.translate import (
    CodexAPIError,
    _ReasoningCollector,
    build_responses_body,
    responses_sse_to_chat_chunks,
    tool_required_map,
)


class APIClientCodexMixin:
    # ------------------------------------------------------------ 辅助

    def _codex_session_id(self) -> str:
        """进程内固定的会话 id（prompt 缓存亲和）。"""
        if not getattr(self, "_codex_session_id_value", None):
            self._codex_session_id_value = str(uuid.uuid4())
        return self._codex_session_id_value

    def _resolve_codex_effort(self, models_mgr, model_id: str) -> str:
        """会话 reasoning_effort → codex effort（按模型支持档位校验回落）。"""
        info = None
        try:
            info = models_mgr.get_model_info(model_id)
        except Exception:
            pass
        supported = {
            str(lv.get("effort"))
            for lv in (info or {}).get("supported_reasoning_levels") or []
            if isinstance(lv, dict) and lv.get("effort")
        }
        requested = str(getattr(self, "reasoning_effort", None) or "").strip()
        if requested and (not supported or requested in supported):
            return requested
        default = str((info or {}).get("default_reasoning_level") or "").strip()
        if default:
            return default
        return "medium"

    def _codex_error_chunk(
        self,
        *,
        status_code: Optional[int],
        error_text: str,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        info = {
            "status_code": status_code,
            "error_text": error_text,
            "error_type": error_type,
            "error_message": error_message or error_text,
            "model_id": getattr(self, "model_id", None),
            "model_key": getattr(self, "model_key", None),
            "provider": "codex",
        }
        self.last_error_info = info
        return {"error": info}

    @staticmethod
    def _redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
        redacted = dict(headers)
        for key in ("Authorization", "chatgpt-account-id"):
            if key in redacted:
                redacted[key] = "***"
        return redacted

    # ------------------------------------------------------------ 主入口

    async def chat_codex(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[Dict, None]:
        auth = get_auth_manager()
        models_mgr = get_models_manager()

        # 模型发现（懒加载 + 陈旧后台刷新）；失败不阻塞请求（有缓存兜底）
        try:
            await models_mgr.ensure_fresh()
        except Exception:
            pass

        current_thinking_mode = self.get_current_thinking_mode()
        api_config = self._select_api_config(current_thinking_mode)
        model_id = api_config["model_id"]

        # max_output_tokens：与 chat() 同款的预算收缩逻辑
        try:
            override_max = (
                self.thinking_max_tokens if current_thinking_mode else self.fast_max_tokens
            )
            max_tokens = int(override_max) if override_max is not None else None
        except (TypeError, ValueError):
            max_tokens = None
        budget_max_context = self.max_context_tokens or self.default_context_window
        if budget_max_context and budget_max_context > 0:
            used = max(0, int(self.current_context_tokens or 0))
            available = budget_max_context - used
            if available <= 0:
                max_tokens = 1
            elif max_tokens is not None:
                max_tokens = min(max_tokens, available)

        effort = self._resolve_codex_effort(models_mgr, model_id)
        instructions = build_instructions(model_id, "", models_mgr)

        body = build_responses_body(
            messages=messages,
            instructions=instructions,
            model_id=model_id,
            tools=tools,
            effort=effort,
            summary="detailed",
            max_output_tokens=max_tokens,
            session_id=self._codex_session_id(),
        )

        # 请求（401 自愈后整体重试一次）
        attempt = 0
        while attempt < 2:
            attempt += 1
            try:
                access_token = await auth.get_access_token()
            except CodexAuthError as exc:
                yield self._codex_error_chunk(
                    status_code=None,
                    error_text=str(exc),
                    error_type="auth_error",
                    error_message=str(exc),
                )
                return

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "OpenAI-Beta": "responses=experimental",
                "originator": "codex_cli_rs",
                "session_id": self._codex_session_id(),
            }
            account_id = auth.get_account_id()
            if account_id:
                headers["chatgpt-account-id"] = account_id

            try:
                self._debug_log(
                    {
                        "event": "codex_request_prepare",
                        "model_key": getattr(self, "model_key", None),
                        "model_id": model_id,
                        "effort": effort,
                        "attempt": attempt,
                        "input_items": len(body.get("input") or []),
                        "tools": len(body.get("tools") or []),
                        "headers": self._redact_headers(headers),
                    }
                )
            except Exception:
                pass

            collector = _ReasoningCollector()
            required_map = tool_required_map(tools)
            produced_any = False
            try:
                async with httpx.AsyncClient(
                    proxy=resolve_proxy(), timeout=300
                ) as client:
                    async with client.stream(
                        "POST", RESPONSES_URL, json=body, headers=headers
                    ) as response:
                        if response.status_code == 401 and attempt < 2:
                            # token_expired：重读/刷新凭证后整体重试
                            await response.aread()
                            try:
                                await auth.handle_unauthorized()
                                continue
                            except CodexAuthError as exc:
                                yield self._codex_error_chunk(
                                    status_code=401,
                                    error_text=str(exc),
                                    error_type="auth_error",
                                    error_message=str(exc),
                                )
                                return
                        if response.status_code != 200:
                            error_bytes = await response.aread()
                            error_text = (
                                error_bytes.decode("utf-8", errors="ignore")
                                if hasattr(error_bytes, "decode")
                                else str(error_bytes)
                            )
                            yield self._codex_http_error(
                                response.status_code, error_text
                            )
                            return

                        async def _lines() -> AsyncGenerator[str, None]:
                            async for line in response.aiter_lines():
                                yield line

                        async for chunk in responses_sse_to_chat_chunks(
                            _lines(), collector, required_map
                        ):
                            produced_any = True
                            yield chunk

            except CodexAPIError as exc:
                yield self._codex_error_chunk(
                    status_code=None,
                    error_text=str(exc),
                    error_type=exc.code or "codex_api_error",
                    error_message=str(exc),
                )
                return
            except httpx.ConnectError as exc:
                yield self._codex_error_chunk(
                    status_code=None,
                    error_text=f"connect_error: {exc}",
                    error_type="connection_error",
                    error_message=tr("api_client.connect_failed", error=str(exc)),
                )
                return
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                yield self._codex_error_chunk(
                    status_code=None,
                    error_text=str(exc),
                    error_type="connection_error",
                    error_message=tr("api_client.connection_lost", error=str(exc)),
                )
                return
            except Exception as exc:
                yield self._codex_error_chunk(
                    status_code=None,
                    error_text=str(exc) or repr(exc),
                    error_type="exception",
                    error_message=str(exc) or repr(exc),
                )
                return

            # 成功收尾：加密 reasoning items 落实例属性，供落盘进消息 metadata
            self.last_codex_reasoning_items = collector.items or None
            self.last_error_info = None
            return

    # ------------------------------------------------------------ 错误整形

    def _codex_http_error(self, status_code: int, error_text: str) -> Dict[str, Any]:
        error_type = None
        error_message = None
        try:
            parsed = json.loads(error_text)
            err = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(err, dict):
                error_type = err.get("type") or err.get("code")
                error_message = err.get("message")
            detail = parsed.get("detail") if isinstance(parsed, dict) else None
            if isinstance(detail, dict):
                error_type = error_type or detail.get("code")
                error_message = error_message or detail.get("message")
        except Exception:
            pass

        if status_code == 429:
            # 限额错误：尽量提取重置时间友好展示
            reset_hint = ""
            for key in ("resets_at", "reset_at", "resets_in_seconds"):
                if key in error_text:
                    reset_hint = f"（详情: {error_text[:300]}）"
                    break
            error_message = (
                f"Codex 订阅限额已用尽（5 小时/每周窗口），请等待重置后重试{reset_hint}"
            ) if not error_message else error_message

        return self._codex_error_chunk(
            status_code=status_code,
            error_text=error_text,
            error_type=error_type,
            error_message=error_message
            or f"Codex 请求失败 (HTTP {status_code})",
        )
