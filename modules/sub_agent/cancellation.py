"""Cancel current child execution without terminating its persistent instance."""
from __future__ import annotations

import asyncio
import time

from modules.multi_agent.debug_logger import ma_debug


class SubAgentCancellationMixin:
    def _init_cancellation(self) -> None:
        self._execution_task = None
        self._stop_requested = False
        self._stopped_by = "user"
        self._active_tool_calls = []
        self._tool_batch_start = 0
        self._active_progress = {}
        self._engine_client = None
        self._engine_tools = None

    def request_stop(self, *, stopped_by: str = "user") -> bool:
        """Queue one cancellation on the owning loop; never edit history cross-thread."""
        if stopped_by not in {"user", "master"}:
            raise ValueError("无效的停止来源。")
        with self._work_lock:
            if self._cancelled or self.task_record.get("status") == "terminated":
                return False
            if self._stop_requested:
                return True
            if self._idle and (not self._pending_work or self._work_paused):
                return False
            self._stop_requested = True
            self._stopped_by = stopped_by
            self._work_paused = True
            self.task_record["work_paused"] = True
            self.manager._save_state()
        ma_debug("sub_agent_stop_requested", task_id=self.task_id,
                 agent_id=self.agent_id, stopped_by=stopped_by)

        def interrupt():
            if not self._stop_requested or self._cancelled:
                return
            worker = self._execution_task
            if worker is not None and not worker.done():
                # Repeated requests must not interrupt the first cancellation's cleanup.
                worker.cancel()

        loop = self._task.get_loop() if self._task is not None else self.manager._ensure_event_loop()
        loop.call_soon_threadsafe(interrupt)
        return True

    async def _run_cancellable_execution(self) -> None:
        """Keep the instance alive while only its current execution is cancelled."""
        while not self._cancelled:
            if self._stop_requested:
                self._complete_stop()
            worker = asyncio.create_task(self._run_loop())
            self._execution_task = worker
            try:
                await worker
            except asyncio.CancelledError:
                if self._cancelled or not self._stop_requested:
                    raise
                self._complete_stop()
            else:
                return
            finally:
                self._execution_task = None

    def _complete_stop(self) -> None:
        text = "被主智能体取消" if self._stopped_by == "master" else "被用户取消"
        # Only the currently committed batch can be incomplete. Never rewrite old results.
        answered = {message.get("tool_call_id") for message in self.messages[self._tool_batch_start + 1:]
                    if message.get("role") == "tool"}
        for call in self._active_tool_calls:
            call_id = call.get("id")
            if not call_id or call_id in answered:
                continue
            name = (call.get("function") or {}).get("name", "")
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
            answered.add(call_id)
            if call_id not in self._active_progress:
                self._active_progress[call_id] = {"id": call_id, "tool": name, "args": {}}
        # Includes streamed calling entries, whose partial parameters never entered history.
        for progress in list(self._active_progress.values()):
            self.emit("progress", {**progress, "status": "cancelled", "message": text,
                                   "ts": int(time.time() * 1000)})
        self._active_tool_calls = []
        self._active_progress.clear()
        self._pending_answer_question_id = None
        ma_debug("sub_agent_stop_committed", task_id=self.task_id, agent_id=self.agent_id,
                 stopped_by=self._stopped_by, message_count=len(self.messages))
        self._finish_round(text + "，本轮已停止；保留上下文，可发送消息继续。", reason="stopped")
        self._stop_requested = False
