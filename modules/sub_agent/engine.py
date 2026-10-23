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

class SubAgentEngineMixin:
    async def _run_loop(self) -> None:
        start_time = time.time()
        max_turns = self.max_turns  # None 表示无上限（个人空间可配置，默认 50）
        turn = 0

        while not self._cancelled:
            if self._stop_requested:
                raise asyncio.CancelledError()
            elapsed = time.time() - start_time
            # The previous response and its entire tool batch have finished here.
            if not self._idle and self._round_expired():
                self._finish_round("当前响应及工具调用已完整处理，单轮预算已到；可以发送消息继续。", reason="timeout")
                continue

            # 空闲实例保留上下文，只由新消息唤醒。
            if self._idle:
                event_set = False
                try:
                    if self._continue_event is None:
                        self._continue_event = asyncio.Event()
                    await asyncio.wait_for(self._continue_event.wait(), timeout=1.0)
                    event_set = True
                except asyncio.TimeoutError:
                    pass
                if self._cancelled:
                    break
                if not event_set:
                    # 只是周期性检查取消状态，没有新消息，保持 idle 继续等待
                    continue
                self._continue_event.clear()
                ma_debug(
                    "sub_agent_idle_wake",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    pending_messages=[m.get("role") for m in self.messages[-3:]],
                )
                if not self._accept_work(new_round=True):
                    continue
                self._idle = False
                self._round_started = time.monotonic()
                start_time = time.time()
                turn = 0
                # 关键修复：从 idle 唤醒后必须同步更新 MultiAgentState 状态为 running，
                # 否则 has_running_multi_agent 会误判为 false，导致主对话提前进入空闲、
                # 后续子智能体输出无法推送到主智能体。
                if self.multi_agent_state:
                    self.multi_agent_state.mark_status(self.agent_id, "running")
                    ma_debug(
                        "sub_agent_idle_wake_mark_running",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                    )
                continue

            if self._engine_client is None:
                self._engine_client = self._build_client()
            client, model_key = self._engine_client
            if self._engine_tools is None:
                self._engine_tools = await self._build_runtime_tools()
            tools = self._engine_tools

            turn += 1
            if max_turns is not None and turn > max_turns:
                await self._write_failure(tr("sub_agent_task.max_turns_exceeded"), max_turns_exceeded=True)
                return

            self.stats["api_calls"] += 1
            self.stats["turn_count"] = turn
            self.stats["runtime_seconds"] = int(elapsed)
            self.emit("stats", {**self.stats, "turn_count": turn})

            # 运行期间实时持久化 stats，供 get_sub_agent_status 查询
            try:
                self.stats_file.parent.mkdir(parents=True, exist_ok=True)
                _stats_snapshot = {**self.stats, "runtime_seconds": int(elapsed), "turn_count": turn}
                self.stats_file.write_text(json.dumps(_stats_snapshot, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

            # 多智能体模式：在模型调用前识别是否有待回答的提问
            self._pending_answer_question_id = self._peek_pending_question_id()

            # 调试：记录进入本轮模型调用前的上下文摘要
            ma_debug(
                "sub_agent_model_call_start",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
                turn=turn,
                message_count=len(self.messages),
                last_user_message=self.messages[-1].get("content", "")[:300] if self.messages and self.messages[-1].get("role") == "user" else "",
                pending_answer_question_id=self._pending_answer_question_id,
            )

            # 安全点：写入延迟的上下文通知（不触发新一轮工作，仅让下一轮模型调用看到）
            self._flush_pending_notifications()
            self._accept_work(new_round=False)
            self._pending_answer_question_id = self._peek_pending_question_id()

            # 模型请求（带失败重试，与主智能体同构：最多 5 次尝试、重试间隔 10s）。
            # 重试范围：零接收一律可重试；有接收（输出中途断开）仅网络类断流可重试，
            # 采取「清除重来」——半截内容只在 _call_model 局部变量与已推送的 calling
            # 进度事件里，转发给主智能体的输出只发生在流完整结束后，无副作用。
            assistant_message = ""
            reasoning = ""
            tool_calls = []
            usage = None
            call_error: Optional[SubAgentModelCallError] = None
            for api_attempt in range(_SUB_AGENT_MAX_API_RETRIES + 1):
                if self._cancelled:
                    raise asyncio.CancelledError()
                if api_attempt and self._round_expired():
                    break
                try:
                    from modules.api_pricing import capture_price
                    from uuid import uuid4
                    self._request_price = await asyncio.to_thread(capture_price, model_key)
                    self._cost_request_id = str(uuid4())
                    assistant_message, reasoning, tool_calls, usage, responses_reasoning_items = await self._call_model(client, model_key, tools)
                    call_error = None
                    break
                except SubAgentModelCallError as exc:
                    call_error = exc
                    can_retry = api_attempt < _SUB_AGENT_MAX_API_RETRIES and (exc.is_network_error or not exc.received_any)
                    ma_debug(
                        "sub_agent_api_attempt_failed",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                        display_name=self.display_name,
                        turn=turn,
                        attempt=api_attempt + 1,
                        max_attempts=_SUB_AGENT_MAX_API_RETRIES + 1,
                        received_any=exc.received_any,
                        can_retry=can_retry,
                        error=str(exc)[:500],
                    )
                    if not can_retry:
                        break
                    # 有接收断流重试（清除重来）：本轮已推送的「正在调用」工具进度
                    # 条目标记取消，避免前端进度弹窗永久卡在「调用中」；
                    # 重试成功后模型若再次调用会推送新的条目（新一轮工具 id 不同）。
                    for calling_tool in exc.calling_tools:
                        self.emit("progress", {
                            "id": calling_tool.get("id"),
                            "tool": calling_tool.get("name", ""),
                            "status": "cancelled",
                            "args": {},
                            "ts": int(time.time() * 1000),
                        })
                    # 重试本身也是一次额外 API 请求
                    self.stats["api_calls"] += 1
                    # 重试等待期间必须响应软停止/硬取消
                    wait_until = time.time() + _SUB_AGENT_RETRY_DELAY_SECONDS
                    while time.time() < wait_until:
                        if self._cancelled:
                            raise asyncio.CancelledError()
                        await asyncio.sleep(0.2)
                    continue

            if call_error is not None:
                if self._round_expired():
                    self._finish_round(str(call_error), reason="timeout")
                    continue
                if call_error.is_network_error or not call_error.received_any:
                    # 5 次尝试全部失败（零接收或网络断流）：子智能体不终止，转为 idle
                    # 并向 Team Leader 汇报错误，等待检查网络后重新下达指令
                    error_report = tr(
                        "sub_agent_task.model_call_failed_idle",
                        count=_SUB_AGENT_MAX_API_RETRIES + 1,
                        error=call_error,
                    )
                    ma_debug(
                        "sub_agent_api_retries_exhausted_idle",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                        display_name=self.display_name,
                        turn=turn,
                    )
                    self._finish_round(error_report, reason="error")
                    continue
                self._finish_round(str(call_error), reason="error")
                continue

            # A successful response without usage is explicitly unpriced.
            self._apply_usage(usage)

            # 上下文压缩检查：超过阈值时触发深度压缩
            if self.current_context_tokens > 0 and self.current_context_tokens >= self.compress_threshold_tokens:
                compressed = self._deep_compress_messages()
                if compressed:
                    # 写入进度文件：前端活动窗口在压缩发生的位置显示压缩提示
                    self.emit("progress", {
                        "subtype": "compression",
                        "round": self._compress_round,
                        "ts": int(time.time() * 1000),
                    })
                    ma_debug(
                        "sub_agent_deep_compressed",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                        display_name=self.display_name,
                        tokens_before=self.current_context_tokens,
                        messages_before=len(self.messages) + 1,  # +1 因为本转 assistant 还没 append
                        messages_after=len(self.messages),
                        compress_round=self._compress_round,
                    )

            # Intermediate text stays in child activity, not the parent context.
            if assistant_message.strip() and tool_calls:
                self.emit("progress", {"subtype": "output", "content": assistant_message,
                                       "is_final": False, "ts": int(time.time() * 1000)})

            final_message: Dict[str, Any] = {"role": "assistant", "content": assistant_message}
            if reasoning:
                final_message["reasoning_content"] = reasoning
            if tool_calls:
                final_message["tool_calls"] = tool_calls
            if responses_reasoning_items:
                # Responses 加密思考链（通用）：随消息持久化，下次请求由 translate 原样回传
                final_message["responses_reasoning_items"] = responses_reasoning_items
            self._active_tool_calls = tool_calls
            self._tool_batch_start = len(self.messages)
            self.messages.append(final_message)
            self._persist_conversation(partial_summary=assistant_message[:200])

            if not tool_calls:
                self._finish_round(assistant_message or "本轮已结束，模型未返回正文。")
                continue

            for tool_call in tool_calls:
                if self._cancelled or self._stop_requested:
                    raise asyncio.CancelledError()
                name = tool_call.get("function", {}).get("name", "")
                args = self._parse_args(tool_call)
                progress_id = tool_call.get("id") or f"tool_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

                self.emit("progress", {"id": progress_id, "tool": name, "status": "running", "args": args, "ts": int(time.time() * 1000)})
                try:
                    result = await self._execute_tool(name, args)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Child tool failed: %s", name)
                    result = {"success": False, "error": str(exc)}
                if (self._stop_requested or self._cancelled) and result.get("status") == "cancelled":
                    # Native commands may return a cancellation result instead of raising.
                    raise asyncio.CancelledError()
                self.emit("progress", {"id": progress_id, "tool": name, "status": "completed" if result.get("success") else "failed", "args": args, "ts": int(time.time() * 1000)})

                self._update_stats(name)

                content = _format_tool_result(name, result)
                if name.startswith("mcp__"):
                    content = self._mcp_content(result)
                elif name == "read_mediafile" and result.get("success"):
                    content = self._build_media_tool_content(result) or content
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", progress_id),
                    "content": content,
                })
                self._persist_conversation(partial_summary=assistant_message[:200])

            self._active_tool_calls = []

        # 循环结束（取消或 idle 被外部终止）后的清理
        if self._cancelled:
            if self.multi_agent_state:
                self.multi_agent_state.mark_status(self.agent_id, "terminated")
