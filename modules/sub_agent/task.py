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

from modules.sub_agent.runtime import SubAgentRuntimeMixin
from modules.sub_agent.cancellation import SubAgentCancellationMixin
from modules.sub_agent.engine import SubAgentEngineMixin
from modules.sub_agent.communication import SubAgentCommunicationMixin
from modules.sub_agent.model import SubAgentModelMixin
from modules.sub_agent.history import SubAgentHistoryMixin

class SubAgentTask(SubAgentRuntimeMixin, SubAgentCancellationMixin, SubAgentEngineMixin,
                   SubAgentCommunicationMixin, SubAgentModelMixin, SubAgentHistoryMixin):
    """One fixed-authority child with resumable working rounds."""
    def __init__(
        self,
        manager: "SubAgentManager",
        task_record: Dict[str, Any],
        task_message: str,
        system_prompt: str,
        model_key: Optional[str],
        thinking_mode: Optional[str],
        *,
        multi_agent_state: Optional["MultiAgentState"] = None,
        display_name: Optional[str] = None,
    ):
        self.manager = manager
        self.task_record = task_record
        self.task_message = task_message
        self.system_prompt = system_prompt
        self.model_key = model_key
        self.thinking_mode = thinking_mode or "thinking"
        self.task_id = task_record["task_id"]
        self.agent_id = task_record["agent_id"]
        from modules.execution_scope import ACCESS_LEVELS, ExecutionScope
        access_level = task_record.get("access_level")
        if not isinstance(access_level, str) or access_level not in ACCESS_LEVELS or not task_record.get("workspace_root"):
            raise ValueError("子智能体缺少固定权限，请重新创建实例。")
        self.execution_scope = ExecutionScope(
            executor_kind="sub_agent", actor_id=self.task_id,
            workspace_root=Path(task_record["workspace_root"]),
            access_level=access_level, conversation_id=task_record.get("conversation_id"),
            task_id=self.task_id,
            working_dir=Path(task_record["working_dir"]) if task_record.get("working_dir") else None,
        )
        from modules.scoped_execution_policy import fixed_workspace_root, fixed_working_dir
        fixed_workspace_root(self.execution_scope)
        fixed_working_dir(self.execution_scope)
        raw_timeout = task_record.get("timeout_seconds")
        self.timeout_seconds = int(raw_timeout) if raw_timeout is not None else None
        self.working_dir = Path(task_record["working_dir"])

        self.output_file = Path(task_record["output_file"])
        self.stats_file = Path(task_record["stats_file"])
        self.progress_file = Path(task_record["progress_file"])
        self.conversation_file = Path(task_record["conversation_file"])
        # system_prompt.txt 路径（冻结的 system prompt，压缩后从此重建）
        self.system_prompt_file = Path(task_record.get("task_root", "")) / "system_prompt.txt"

        # 上下文压缩配置
        # 默认阈值 250k tokens，可由外部覆盖（如个人空间子智能体管理配置）
        self.compress_threshold_tokens: int = int(task_record.get("compress_threshold_tokens") or 250_000)
        self.max_turns = None
        self.current_context_tokens: int = 0
        self._compress_round: int = 0
        # 外部会话标识（x-opencode-session）：从已有对话文件恢复（重启/恢复场景），
        # 无则首次请求时惰性生成；深度压缩后重置为新 ID。
        self._external_session_id: Optional[str] = self._load_external_session_id()

        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_message},
        ]
        self.stats = {
            "runtime_start": time.time() * 1000,
            "runtime_seconds": 0,
            "files_read": 0,
            "write_files": 0,
            "edit_files": 0,
            "searches": 0,
            "web_pages": 0,
            "commands": 0,
            "api_calls": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0, "cached_input": 0},
            "current_context_tokens": 0,
            "compress_round": 0,
        }
        self._stdout_lines: List[str] = []
        self._cancelled = False
        self._task: Optional[asyncio.Task] = None
        # Explicit stop cancels the execution worker; the instance remains resumable.
        self._init_cancellation()

        # 多智能体模式相关字段
        self.multi_agent_state = multi_agent_state
        # display_name 不传时回退为 'Agent_{self.agent_id}'
        self.display_name = display_name or task_record["name"]
        # 多智能体运行期控制
        # 使用 asyncio.Event 在子智能体自己的事件循环内等待；
        # inject_message 可能跨线程调用，通过 loop.call_soon_threadsafe 唤醒。
        self._continue_event: Optional[asyncio.Event] = None
        self._idle = False
        self._pending_answer_question_id: Optional[str] = None
        self._answered_question_ids: Set[str] = set()
        # 纯上下文通知（不触发新一轮工作）延迟队列：当上下文末尾处于
        # assistant tool_calls 与 tool 结果之间时，直接 append 会违反 API 的
        # tool 消息顺序约束，先入队，由主循环在安全点 _flush_pending_notifications 写入。
        self._pending_notifications: List[str] = []
        self._init_round_runtime()


    def emit(self, type_: str, data: Dict[str, Any]) -> None:
        """输出一行 JSONL 到 progress 文件并缓存。"""
        if type_ == "progress" and data.get("id"):
            if data.get("status") in {"calling", "running"}:
                self._active_progress[data["id"]] = dict(data)
            elif data.get("status") in {"completed", "failed", "cancelled"}:
                self._active_progress.pop(data["id"], None)
        line = json.dumps({"type": type_, **data}, ensure_ascii=False)
        self._stdout_lines.append(line)
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.progress_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


    async def run(self) -> None:
        """主 LLM 循环。"""
        # 在子智能体自己的事件循环内初始化 asyncio.Event
        self._continue_event = asyncio.Event()
        if self._pending_work and not self._work_paused:
            self._continue_event.set()
        try:
            await self._run_cancellable_execution()
        except asyncio.CancelledError:
            # 硬取消（terminate）路径
            self._cancelled = True
            logger.debug(f"[SubAgent] task={self.task_id} 被取消")
            ma_debug("sub_agent_run_cancelled", task_id=self.task_id, agent_id=self.agent_id, display_name=self.display_name)
            # 写 terminated 终态快照而不是 failed：terminated 是吸收态，
            # 避免 output.json 的 failed 被其他 manager 读回后走复活路径
            self._persist_conversation(partial_summary="子智能体被手动终止")
            self._write_terminated_snapshot()
            raise
        except Exception as exc:
            logger.exception(f"[SubAgent] task={self.task_id} 执行异常")
            ma_debug("sub_agent_run_exception", task_id=self.task_id, agent_id=self.agent_id, display_name=self.display_name, error=str(exc))
            await self._write_failure(tr("sub_agent_task.execution_error", error=exc))
        finally:
            if self._mcp_manager is not None:
                await asyncio.to_thread(self._mcp_manager.close_all_clients)
            resources = getattr(self, "_execution_terminal", None)
            if resources is not None:
                resources.terminal_manager.close_all()
                self._execution_terminal = None


    def _write_terminated_snapshot(self) -> None:
        """写入 terminated 终态快照到 output.json（硬取消/自杀路径）。"""
        try:
            runtime_seconds = int(time.time() - (self.stats["runtime_start"] / 1000))
            output_data = {
                "success": False,
                "status": "terminated",
                "summary": tr("sub_agent_task.manual_terminated_summary"),
                "stats": {**self.stats, "runtime_seconds": runtime_seconds},
            }
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 写入终结快照失败: {exc}")


    def cancel(self) -> None:
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()
