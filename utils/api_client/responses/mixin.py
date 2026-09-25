"""APIClient 的通用 Responses API 通道（2026-09-25 协议泛化）。

``chat_responses()`` 产出与 ``chat()`` 完全同形的 chunk。分发入口在
``APIClientChatMixin.chat()`` 开头（按 profile 的 ``api_protocol == "responses"``
路由，不再看 provider_type）。

auth 模式（profile["responses_auth"]）：
- 缺省/``"bearer"``：静态 api_key，``_build_headers``（含 extra_headers_resolver
  链——x-opencode-session 等动态头由此进入）；httpx trust_env 默认读环境代理
- ``"codex_oauth"``：ChatGPT 订阅 OAuth（codex 子包凭证管理：401 自愈刷新、
  chatgpt-account-id/originator 头、官方 base_instructions 注入与
  supported_reasoning_levels 档位校验、429 订阅限额文案、prompt_cache_key 亲和）

加密 reasoning 处理对全部 Responses 模型统一生效（采集 → 旁路落盘 →
下轮回插），无 per-provider 策略分叉（2026-09-25 用户拍板）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from modules.i18n import tr

from utils.api_client.error_hints import region_block_hint
from utils.api_client.responses.translate import (
    ResponsesAPIError,
    _ReasoningCollector,
    build_responses_body,
    responses_sse_to_chat_chunks,
    tool_required_map,
)


class APIClientResponsesMixin:
    # ------------------------------------------------------------ 辅助

    def _responses_session_id(self) -> str:
        """进程内固定的会话 id（codex prompt_cache_key 亲和用）。"""
        if not getattr(self, "_responses_session_id_value", None):
            self._responses_session_id_value = str(uuid.uuid4())
        return self._responses_session_id_value

    def _is_codex_oauth(self) -> bool:
        return str(getattr(self, "responses_auth", None) or "") == "codex_oauth"

    def _resolve_responses_effort(self, model_id: str) -> Optional[str]:
        """会话 reasoning_effort → Responses effort。

        codex_oauth：按 codex 模型发现的 supported_reasoning_levels 校验回落；
        通用：直接透传（网关/上游自行处理不支持的档位）。
        """
        requested = str(getattr(self, "reasoning_effort", None) or "").strip()
        if not self._is_codex_oauth():
            return requested or None
        info = None
        try:
            from utils.api_client.codex.models import get_models_manager

            info = get_models_manager().get_model_info(model_id)
        except Exception:
            pass
        supported = {
            str(lv.get("effort"))
            for lv in (info or {}).get("supported_reasoning_levels") or []
            if isinstance(lv, dict) and lv.get("effort")
        }
        if requested and (not supported or requested in supported):
            return requested
        default = str((info or {}).get("default_reasoning_level") or "").strip()
        return default or "medium"

    def _build_responses_instructions(self, model_id: str) -> str:
        """instructions 来源（profile["instructions_source"]）。

        codex_models_cache：ChatGPT 后端强制校验的官方 base_instructions；
        其他（缺省）：空——astrion 自身 system 提示由 build_responses_body 并入。
        """
        if str(getattr(self, "responses_instructions_source", None) or "") == "codex_models_cache":
            try:
                from utils.api_client.codex.models import get_models_manager
                from utils.api_client.codex.prompt import build_instructions

                return build_instructions(model_id, "", get_models_manager())
            except Exception:
                return ""
        return ""

    def _responses_error_chunk(
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
            "provider": getattr(self, "provider_id", None) or getattr(self, "provider_type", None),
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

    async def chat_responses(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[Dict, None]:
        codex_oauth = self._is_codex_oauth()
        auth = None
        if codex_oauth:
            from utils.api_client.codex.auth import CodexAuthError, get_auth_manager
            from utils.api_client.codex.models import get_models_manager

            auth = get_auth_manager()
            # 模型发现（懒加载 + 陈旧后台刷新）；失败不阻塞请求（有缓存兜底）
            try:
                await get_models_manager().ensure_fresh()
            except Exception:
                pass

        current_thinking_mode = self.get_current_thinking_mode()
        api_config = self._select_api_config(current_thinking_mode)
        base_url = str(api_config.get("base_url") or "").rstrip("/")
        model_id = api_config["model_id"]
        url = f"{base_url}/responses"

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

        effort = self._resolve_responses_effort(model_id)
        instructions = self._build_responses_instructions(model_id)

        body = build_responses_body(
            messages=messages,
            instructions=instructions,
            model_id=model_id,
            tools=tools,
            effort=effort,
            summary="detailed",
            max_output_tokens=max_tokens,
            session_id=self._responses_session_id() if codex_oauth else None,
        )

        # 请求（codex_oauth：401 自愈后整体重试一次；bearer：单次）
        max_attempts = 2 if codex_oauth else 1
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            if codex_oauth:
                try:
                    access_token = await auth.get_access_token()
                except CodexAuthError as exc:
                    yield self._responses_error_chunk(
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
                    "session_id": self._responses_session_id(),
                }
                account_id = auth.get_account_id()
                if account_id:
                    headers["chatgpt-account-id"] = account_id
            else:
                headers = self._build_headers(
                    str(api_config.get("api_key") or ""), base_url=base_url
                )
                headers["Accept"] = "text/event-stream"

            try:
                self._debug_log(
                    {
                        "event": "responses_request_prepare",
                        "model_key": getattr(self, "model_key", None),
                        "model_id": model_id,
                        "api_protocol": "responses",
                        "auth": "codex_oauth" if codex_oauth else "bearer",
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
            try:
                if codex_oauth:
                    from utils.api_client.codex.settings import resolve_proxy

                    client_ctx = httpx.AsyncClient(proxy=resolve_proxy(), timeout=300)
                else:
                    client_ctx = httpx.AsyncClient(http2=True, timeout=300)
                async with client_ctx as client:
                    async with client.stream(
                        "POST", url, json=body, headers=headers
                    ) as response:
                        if response.status_code == 401 and codex_oauth and attempt < max_attempts:
                            # token_expired：重读/刷新凭证后整体重试
                            await response.aread()
                            try:
                                await auth.handle_unauthorized()
                                continue
                            except CodexAuthError as exc:
                                yield self._responses_error_chunk(
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
                            yield self._responses_http_error(
                                response.status_code, error_text
                            )
                            return

                        async def _lines() -> AsyncGenerator[str, None]:
                            async for line in response.aiter_lines():
                                yield line

                        async for chunk in responses_sse_to_chat_chunks(
                            _lines(), collector, required_map
                        ):
                            yield chunk

            except ResponsesAPIError as exc:
                yield self._responses_error_chunk(
                    status_code=None,
                    error_text=str(exc),
                    error_type=exc.code or "responses_api_error",
                    error_message=str(exc),
                )
                return
            except httpx.ConnectError as exc:
                yield self._responses_error_chunk(
                    status_code=None,
                    error_text=f"connect_error: {exc}",
                    error_type="connection_error",
                    error_message=tr("api_client.connect_failed", error=str(exc)),
                )
                return
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                yield self._responses_error_chunk(
                    status_code=None,
                    error_text=str(exc),
                    error_type="connection_error",
                    error_message=tr("api_client.connection_lost", error=str(exc)),
                )
                return
            except Exception as exc:
                yield self._responses_error_chunk(
                    status_code=None,
                    error_text=str(exc) or repr(exc),
                    error_type="exception",
                    error_message=str(exc) or repr(exc),
                )
                return

            # 成功收尾：加密 reasoning items 落实例属性，供落盘进消息 metadata
            # （2026-09-25 泛化后统一命名 last_responses_reasoning_items）
            self.last_responses_reasoning_items = collector.items or None
            self.last_error_info = None
            return

    # ------------------------------------------------------------ 错误整形

    def _responses_http_error(self, status_code: int, error_text: str) -> Dict[str, Any]:
        error_type = None
        error_message = None
        err: Optional[Dict[str, Any]] = None
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

        # 地区封锁/拦截类人话提示（命中则覆盖误导性原始文案，如
        # ocgo 包装的 "Upstream response was not valid JSON"）
        hint = region_block_hint(status_code, error_text, err)
        if hint:
            error_type = error_type or "region_blocked"
            error_message = hint

        if status_code == 429 and self._is_codex_oauth():
            # codex 订阅限额：尽量提取重置时间友好展示
            reset_hint = ""
            for key in ("resets_at", "reset_at", "resets_in_seconds"):
                if key in error_text:
                    reset_hint = f"（详情: {error_text[:300]}）"
                    break
            error_message = (
                f"Codex 订阅限额已用尽（5 小时/每周窗口），请等待重置后重试{reset_hint}"
            ) if not error_message else error_message

        return self._responses_error_chunk(
            status_code=status_code,
            error_text=error_text,
            error_type=error_type,
            error_message=error_message
            or f"Responses 请求失败 (HTTP {status_code})",
        )
