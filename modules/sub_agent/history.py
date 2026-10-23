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

class SubAgentHistoryMixin:
    def _update_stats(self, name: str) -> None:
        if name == "read_file":
            self.stats["files_read"] += 1
        elif name == "write_file":
            self.stats["write_files"] += 1
        elif name == "edit_file":
            self.stats["edit_files"] += 1
        elif name in ("web_search", "extract_webpage", "save_webpage"):
            self.stats["web_pages"] += 1
        elif name == "run_command":
            self.stats["commands"] += 1

        # 工具调用后立即持久化 stats，供运行期间 get_sub_agent_status 查询
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            _elapsed = int((time.time() * 1000 - self.stats["runtime_start"]) / 1000)
            _snapshot = {**self.stats, "runtime_seconds": _elapsed, "turn_count": self.stats.get("turn_count", 0)}
            self.stats_file.write_text(json.dumps(_snapshot, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


    def _apply_usage(self, usage: Any) -> None:
        from modules.sub_agent.usage import apply_usage
        try:
            apply_usage(self, usage)
        except Exception:
            logger.exception('Sub-agent usage/cost commit failed: %s', self.task_id)


    def _rebuild_system_prompt(self) -> str:
        """从冻结的 system_prompt.txt 重新读取 system prompt（压缩后重建用）。"""
        try:
            if self.system_prompt_file.exists():
                return self.system_prompt_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 重建 system prompt 失败: {exc}")
        # 兜底用内存中的
        return self.system_prompt


    def _deep_compress_messages(self) -> bool:
        """深度压缩：把旧消息总结成一条 system 消息，重建 system prompt。

        策略（参考主智能体 compress_conversation）：
        1. 保留冻结的 system prompt（从 system_prompt.txt 读取）
        2. 把 system 之后、最近 N 条消息之前的所有消息压缩成一条 system 摘要
        3. 保留最近的若干条消息（含 tool_calls 配对）不动
        4. 重置 current_context_tokens 估算值

        返回 True 表示执行了压缩。
        """
        if len(self.messages) < 6:
            # 消息太少不压缩
            return False

        self._compress_round += 1
        self.stats["compress_round"] = self._compress_round

        # 保留最近 8 条消息不压缩（确保 tool_calls 和 tool 结果配对完整）
        keep_recent = 8
        # 找到一个安全的切割点：不能在 assistant.tool_calls 和其 tool 响应之间切
        cut_index = len(self.messages) - keep_recent
        # 向前调整，确保不在 tool_calls 配对中间切割
        while cut_index > 1:
            msg = self.messages[cut_index]
            role = msg.get("role")
            # 如果切点是 tool 消息，往前找到对应的 assistant
            if role == "tool":
                cut_index -= 1
                continue
            # 如果切点的 assistant 有 tool_calls，需要保留它和后续 tool
            if role == "assistant" and msg.get("tool_calls"):
                break
            break
        if cut_index <= 1:
            return False

        # 提取要压缩的消息（索引 1 到 cut_index-1，跳过索引 0 的 system prompt）
        old_messages = self.messages[1:cut_index]
        if not old_messages:
            return False

        # 生成压缩摘要
        summary_lines: List[str] = []
        summary_lines.append("系统提示：以下是根据之前的对话记录生成的压缩摘要，请在此基础上继续工作。")
        summary_lines.append(f"（压缩轮次：第 {self._compress_round} 次，压缩前消息数：{len(self.messages)}）")
        summary_lines.append("")

        tool_buffer: List[str] = []
        seen_tool_call_ids: Set[str] = set()

        def flush_tools():
            if not tool_buffer:
                return
            if summary_lines and summary_lines[-1] != "":
                summary_lines.append("")
            summary_lines.append("已执行的工具：")
            summary_lines.extend(f"- {entry}" for entry in tool_buffer)
            tool_buffer.clear()

        for message in old_messages:
            role = message.get("role")
            if role == "user":
                flush_tools()
                content = str(message.get("content") or "")[:500]  # 截断长内容
                summary_lines.append(f"user：{content}")
                continue
            if role == "assistant":
                content = str(message.get("content") or "")
                if content.strip():
                    flush_tools()
                    summary_lines.append(f"assistant：{content[:500]}")
                tool_calls = message.get("tool_calls") or []
                for tc in tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    if tc_id:
                        seen_tool_call_ids.add(tc_id)
                    func = tc.get("function") or {}
                    arguments = func.get("arguments")
                    args_obj = {}
                    if isinstance(arguments, str):
                        try:
                            args_obj = json.loads(arguments)
                        except Exception:
                            args_obj = {}
                    elif isinstance(arguments, dict):
                        args_obj = arguments
                    intent = args_obj.get("intent") if isinstance(args_obj, dict) else None
                    name = func.get("name") or "unknown_tool"
                    entry = intent.strip() if isinstance(intent, str) and intent.strip() else name
                    tool_buffer.append(entry)
                continue
            if role == "tool":
                tc_id = message.get("tool_call_id") or message.get("id")
                if tc_id and tc_id in seen_tool_call_ids:
                    continue
                name = message.get("name") or "unknown_tool"
                tool_buffer.append(name)
                continue
            # 其他角色
            flush_tools()
            content = str(message.get("content") or "")[:300]
            summary_lines.append(f"{role}：{content}" if role else content)

        flush_tools()
        summary_text = "\n".join(summary_lines)

        # 重建消息列表：冻结的 system prompt + 压缩摘要 system + 保留的最近消息
        # 注意：_rebuild_system_prompt 返回裸字符串，必须包装为标准消息 dict；
        # 否则下游 API 客户端（_merge_system_messages 的 msg.get("role")）会在
        # 压缩后的首次模型调用崩溃（'str' object has no attribute 'get'）。
        frozen_system = {"role": "system", "content": self._rebuild_system_prompt()}
        compressed_system = {
            "role": "system",
            "content": summary_text,
            "metadata": {
                "compression": {
                    "round": self._compress_round,
                    "compressed_count": len(old_messages),
                    "created_at": datetime.now().isoformat(),
                }
            },
        }
        recent_messages = self.messages[cut_index:]
        self.messages = [frozen_system, compressed_system] + recent_messages

        # 重置上下文 tokens 估算（压缩后无法精确知道，设为一个保守值）
        self.current_context_tokens = 0
        self.stats["current_context_tokens"] = 0

        # 压缩重写上下文后旧 session 的缓存亲和已失效，重置外部会话标识
        # （与主对话深压缩后重置 external_session_id 的语义一致）
        try:
            from modules.external_session import new_external_session_id

            self._external_session_id = new_external_session_id()
        except Exception:
            self._external_session_id = None

        logger.info(
            f"[SubAgentTask] task={self.task_id} 深度压缩完成: "
            f"压缩 {len(old_messages)} 条消息，保留 {len(recent_messages)} 条，"
            f"压缩轮次={self._compress_round}"
        )
        return True


    def _build_media_tool_content(self, result: Dict[str, Any]) -> Any:
        """把 read_mediafile 结果转成 OpenAI 多模态 content。"""
        b64 = result.get("b64")
        mime = result.get("mime")
        file_type = result.get("type")
        if not b64 or not mime:
            return None
        if file_type == "image":
            return [
                {"type": "text", "text": f"已附加图片: {result.get('path')}"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        if file_type == "video":
            return [
                {"type": "text", "text": f"已附加视频: {result.get('path')}"},
                {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        return None


    async def _write_failure(self, message: str, *, max_turns_exceeded: bool = False, timeout: bool = False) -> None:
        elapsed = time.time() - (self.stats["runtime_start"] / 1000)
        ma_debug(
            "sub_agent_write_failure",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            message=message,
            max_turns_exceeded=max_turns_exceeded,
            timeout=timeout,
            idle=self._idle,
            cancelled=self._cancelled,
        )
        self._finalize_task(False, message, elapsed, max_turns_exceeded=max_turns_exceeded, timeout=timeout, error=message)


    def _persist_conversation(self, *, partial_summary: str = "") -> None:
        """每轮结束后立即落盘子智能体对话，避免跑完了才存一次导致中间状态丢失。"""
        try:
            runtime_seconds = int((time.time() * 1000 - self.stats["runtime_start"]) / 1000)
            status = "running"
            if self._cancelled:
                status = "terminated"
            elif self._idle:
                status = "idle"
            ma_debug(
                "sub_agent_persist_conversation",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
                status=status,
                idle=self._idle,
                cancelled=self._cancelled,
            )
            conversation_data = {
                "agent_id": self.agent_id,
                "task_id": self.task_id,
                "name": self.display_name,
                "round_id": self._round_id,
                "end_reason": self.task_record.get("end_reason"),
                "working_dir": self.task_record.get("working_dir"),
                "created_at": datetime.fromtimestamp(self.stats["runtime_start"] / 1000).isoformat(),
                "updated_at": datetime.now().isoformat(),
                "status": status,
                "success": None,
                "summary": partial_summary,
                "messages": self.messages,
                "stats": {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)},
                "external_session_id": self._external_session_id,
            }
            self.conversation_file.parent.mkdir(parents=True, exist_ok=True)
            from modules.sub_agent.persistence import atomic_json
            atomic_json(self.conversation_file, conversation_data)

            output_data = {
                "success": None,
                "status": status,
                "summary": partial_summary,
                "stats": conversation_data["stats"],
            }
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.output_file, output_data)
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 增量保存失败: {exc}")


    def _finalize_task(self, success: bool, summary: str, elapsed: float, *, max_turns_exceeded: bool = False, timeout: bool = False, error: Optional[str] = None) -> None:
        runtime_seconds = int(elapsed)
        ma_debug(
            "sub_agent_finalize_task",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            success=success,
            summary=summary,
            idle=self._idle,
            cancelled=self._cancelled,
        )
        if timeout:
            status = "timeout"
        elif max_turns_exceeded:
            status = "failed"
        elif success:
            status = "completed"
        else:
            status = "failed"
        output_data = {
            "success": success,
            "status": status,
            "summary": summary,
            "error": error,
            "timeout": timeout,
            "max_turns_exceeded": max_turns_exceeded,
            "stats": {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)},
        }
        conversation_data = {
            "agent_id": self.agent_id,
            "created_at": datetime.fromtimestamp(self.stats["runtime_start"] / 1000).isoformat(),
            "completed_at": datetime.now().isoformat(),
            "success": success,
            "summary": summary,
            "messages": self.messages,
            "stats": output_data["stats"],
            "external_session_id": self._external_session_id,
        }
        stats_data = {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)}

        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
        self.stats_file.write_text(json.dumps(stats_data, ensure_ascii=False), encoding="utf-8")
        self.conversation_file.write_text(json.dumps(conversation_data, ensure_ascii=False), encoding="utf-8")

        self.emit("output", output_data)
        self.emit("conversation", conversation_data)

        self.manager._mark_task_done(self.task_id, success, summary, runtime_seconds)
        self._forward_output_to_master(summary, is_final=True)
        self.manager._save_state()
