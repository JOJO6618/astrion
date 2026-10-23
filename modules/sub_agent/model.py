from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from modules.sub_agent.task_common import (
    APIClient, SubAgentModelCallError, _SUB_AGENT_MAX_API_RETRIES,
    _SUB_AGENT_RETRY_DELAY_SECONDS, _QUESTION_ID_RE, _format_tool_result,
    resolve_locked_model_profile, logger, ma_debug, tr,
)

class SubAgentModelMixin:
    def _build_client(self) -> tuple:
        """按创建时锁定的模型 key 解析配置并初始化 APIClient（模型级创建锁）。

        模型来源唯一 = 主注册表（custom_models + codex 动态）；
        解析失败（未记录 key / key 当前不可用）明确报错，禁止静默回落。
        """
        chosen_key = self.model_key
        profile = resolve_locked_model_profile(chosen_key)

        client = APIClient(thinking_mode=(self.thinking_mode == "thinking"), web_mode=True)
        client.model_key = chosen_key
        client.project_path = str(self.manager.project_path)
        client.apply_profile(profile)
        client.reasoning_effort = self.task_record.get("reasoning_effort")
        # 外部会话标识（x-opencode-session）：随子对话生命周期稳定，压缩后重置
        client.extra_headers_resolver = self._resolve_external_session_headers
        return client, chosen_key


    async def _call_model(
        self,
        client: APIClient,
        model_key: str,
        tools: List[Dict[str, Any]],
    ) -> tuple:
        """调用模型并解析 assistant 消息。

        失败时抛出 SubAgentModelCallError，携带 received_any 标记区分
        「请求阶段失败（可重试）」与「输出期间断开（直接失败）」。
        """
        assistant_message = ""
        reasoning = ""
        tool_calls: List[Dict[str, Any]] = []
        usage = None
        # 是否已收到任何实质内容（文本/思考/工具调用）
        received_any = False
        # 已发出「正在调用」进度事件的 tool_call index（避免重复发）
        calling_emitted: Set[int] = set()

        stream = client.chat(self.messages, tools=tools, stream=True)
        try:
            async for chunk in stream:
                if self._cancelled:
                    # 硬取消：立即抛出 CancelledError，确保不会返回半成品的 tool_calls 继续执行
                    raise asyncio.CancelledError()
                if chunk.get("error"):
                    error_info = chunk.get("error")
                    is_network = True
                    if isinstance(error_info, dict):
                        error_text = (
                            error_info.get("error_message")
                            or error_info.get("error_text")
                            or str(error_info)
                        )
                        # status_code 为空 = 网络类断流（连接错误/超时/远端断开）；
                        # 有值（4xx 等 HTTP 业务错误）不参与有接收重试放宽
                        is_network = error_info.get("status_code") is None
                    else:
                        error_text = str(error_info)
                    # 收集本轮已推送「正在调用」进度的工具，重试前需标记取消
                    calling_tools = [
                        {"id": tc.get("id"), "name": tc.get("function", {}).get("name", "")}
                        for tc in tool_calls
                        if tc.get("id") and tc.get("function", {}).get("name")
                    ]
                    raise SubAgentModelCallError(
                        tr("sub_agent_task2.api_call_failed", error=error_text),
                        received_any=received_any,
                        is_network_error=is_network,
                        calling_tools=calling_tools,
                    )
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    assistant_message += delta["content"]
                    received_any = True
                if delta.get("reasoning_content"):
                    reasoning += delta["reasoning_content"]
                    received_any = True
                elif delta.get("reasoning_details"):
                    received_any = True
                    rd = delta["reasoning_details"]
                    if isinstance(rd, list):
                        reasoning += "".join(str(d.get("text") or "") for d in rd)
                    elif isinstance(rd, str):
                        reasoning += rd
                    elif isinstance(rd, dict):
                        reasoning += str(rd.get("text") or "")

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index")
                    if idx is None:
                        continue
                    received_any = True
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
                    # 工具名与 id 就位后立刻发出「正在调用」进度事件，让前端在参数
                    # 流式生成期间就能显示该工具条目（与主智能体的「正在调用工具」对齐）。
                    # 与后续 running/completed 事件共用同一 tool_call id，前端按 id 更新同一条目
                    if (
                        idx not in calling_emitted
                        and existing["function"]["name"]
                        and existing.get("id")
                    ):
                        calling_emitted.add(idx)
                        self.emit("progress", {
                            "id": existing["id"],
                            "tool": existing["function"]["name"],
                            "status": "calling",
                            "args": {},
                            "ts": int(time.time() * 1000),
                        })

                from utils.token_usage import extract_usage_payload
                normalized_usage = extract_usage_payload(chunk)
                if normalized_usage:
                    usage = normalized_usage
        except (asyncio.CancelledError, SubAgentModelCallError):
            raise
        except Exception as exc:
            # chat 流本身抛出的漏网异常（如底层连接错误未被转为 error chunk 时）
            calling_tools = [
                {"id": tc.get("id"), "name": tc.get("function", {}).get("name", "")}
                for tc in tool_calls
                if tc.get("id") and tc.get("function", {}).get("name")
            ]
            raise SubAgentModelCallError(
                tr("sub_agent_task2.api_call_exception", error=exc),
                received_any=received_any,
                is_network_error=True,
                calling_tools=calling_tools,
            ) from exc
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    logger.warning("Child model stream close failed: %s", exc)

        # Responses（通用）：读取并清空本轮加密 reasoning items（回填 messages 持久化，下次请求原样回传）
        responses_reasoning_items = getattr(client, "last_responses_reasoning_items", None)
        if responses_reasoning_items is not None:
            client.last_responses_reasoning_items = None
        return assistant_message, reasoning, tool_calls, usage, responses_reasoning_items


    def _parse_args(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        raw = tool_call.get("function", {}).get("arguments") or "{}"
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw": raw}


    def _load_external_session_id(self) -> Optional[str]:
        """从子对话文件恢复外部会话标识（重启/恢复场景）；不存在返回 None。"""
        try:
            if self.conversation_file.exists():
                data = json.loads(self.conversation_file.read_text(encoding="utf-8"))
                value = data.get("external_session_id")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception:
            pass
        return None


    def _resolve_external_session_headers(self, base_url: Optional[str]) -> Dict[str, str]:
        """APIClient extra_headers_resolver：为本子智能体对话解析 x-opencode-session 头。

        个人空间开关开启且端点为 opencode.ai 时返回稳定 session ID；首次请求
        惰性生成，随 _persist_conversation 落盘，深度压缩后重置。
        任何异常返回空 dict，不影响主请求。
        """
        try:
            from modules.external_session import (
                external_session_header_enabled,
                is_opencode_endpoint,
                new_external_session_id,
            )

            base_dir = getattr(self.manager, "data_dir", None)
            if not external_session_header_enabled(base_dir):
                return {}
            if not is_opencode_endpoint(base_url):
                return {}
            if not self._external_session_id:
                self._external_session_id = new_external_session_id()
            return {"x-opencode-session": self._external_session_id}
        except Exception:
            return {}
