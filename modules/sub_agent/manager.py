"""Named background agents: one context, fixed authority and resumable rounds."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from modules.sub_agent_settings import resolve_max_instances
from modules.execution_scope import ExecutionScope, child_access_prompt
from modules.scoped_execution_policy import fixed_workspace_root, fixed_working_dir
from modules.sub_agent.creation import SubAgentCreationMixin
from modules.sub_agent.persistence import atomic_json, get_registry, save_registry, STATE_FILENAME
from modules.sub_agent.stats import SubAgentStatsMixin
from modules.sub_agent.task import SubAgentTask
from utils.logger import setup_logger

from .state import TERMINAL_STATUSES

logger = setup_logger(__name__)


class SubAgentManager(SubAgentStatsMixin, SubAgentCreationMixin):
    def __init__(self, project_path: str, data_dir: str, container_session=None,
                 owner_conversation_id: str | None = None):
        self.project_path = Path(project_path).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.container_session = container_session
        self.owner_conversation_id = owner_conversation_id
        self.terminal = None
        self.host_execution_mode = "sandbox"
        self.base_dir = self.data_dir / "sub_agent_tasks"
        self.state_file = self.data_dir / STATE_FILENAME
        self._registry = get_registry(self.data_dir)
        self.tasks = self._registry.tasks
        self._sub_agent_instances = self._registry.instances
        self._running_tasks = self._registry.running
        self.multi_agent_states = self._registry.states
        self._state_lock = self._registry.lock
        self._event_loop = None
        self._loop_lock = threading.Lock()
        self._activation_lock = threading.Lock()
        self._activation_restored = False
        self._sub_agent_todos = {}

    def _load_state(self):
        # All managers of this workspace share the same live records.
        # A query must never replace records still held by running children.
        return self.tasks

    def _save_state(self):
        save_registry(self.data_dir, self._registry)

    def set_terminal(self, terminal):
        self.terminal = terminal

    def set_container_session(self, session):
        self.container_session = session

    def set_host_execution_mode(self, mode):
        self.host_execution_mode = "direct" if mode == "direct" else "sandbox"

    def _ensure_event_loop(self):
        with self._loop_lock:
            if self._event_loop is not None and not self._event_loop.is_closed():
                return self._event_loop
            loop = asyncio.new_event_loop()
            self._event_loop = loop
            self._loop_thread = threading.Thread(target=loop.run_forever,
                                                 name="sub-agent-loop", daemon=True)
            self._loop_thread.start()
            return loop

    def get_multi_agent_state(self, conversation_id):
        return self.multi_agent_states.get(conversation_id)

    def get_or_create_multi_agent_state(self, conversation_id):
        from modules.multi_agent.state import MultiAgentState
        with self._state_lock:
            if conversation_id not in self.multi_agent_states:
                self.multi_agent_states[conversation_id] = MultiAgentState(conversation_id)
            return self.multi_agent_states[conversation_id]

    def next_free_agent_id(self, conversation_id=None):
        used = {task["agent_id"] for task in self.tasks.values()}
        return max(used, default=0) + 1

    def create_sub_agent(self, *, agent_id, summary, task, conversation_id, access_level,
                         model_key, intelligence, reasoning_effort, thinking_mode,
                         display_name, working_dir, system_prompt, task_message,
                         timeout_seconds=None, compress_threshold_tokens=250000):
        from modules.aux_model_resolver import resolve_locked_model_profile
        from modules.multi_agent.state import AgentInstance
        from modules.personalization_manager import load_personalization_config
        resolve_locked_model_profile(model_key)
        prefs = load_personalization_config(self.data_dir) or {}
        max_instances = resolve_max_instances(prefs.get("sub_agent_max_instances"))
        with self._state_lock:
            retained = sum(item.get("status") != "terminated" and item.get("conversation_id") == conversation_id
                           for item in self.tasks.values())
            if retained >= max_instances:
                return {"success": False, "error": (
                    f"当前对话未终结的子智能体已达上限（{max_instances} 个）；"
                    "请继续使用已有实例，或终结不再需要的实例后再创建。"
                )}
            directory = self._resolve_working_dir(working_dir, agent_id=agent_id)
            task_id = "sub_" + uuid.uuid4().hex
            task_root = self.base_dir / task_id
            task_root.mkdir(parents=True)
            system_prompt += child_access_prompt(access_level)
            (task_root / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
            (task_root / "task.txt").write_text(task_message, encoding="utf-8")
            record = dict(task_id=task_id, agent_id=agent_id, name=display_name,
                          display_name=display_name, summary=summary, task=task,
                          conversation_id=conversation_id, access_level=access_level,
                          workspace_root=str(self.project_path), working_dir=str(directory),
                          task_root=str(task_root),
                          timeout_seconds=timeout_seconds, model_key=model_key,
                          intelligence=intelligence, reasoning_effort=reasoning_effort,
                          thinking_mode=thinking_mode, status="running", round_id=1,
                          compress_threshold_tokens=compress_threshold_tokens,
                          created_at=time.time(), updated_at=time.time())
            record.update({key: str(task_root / filename) for key, filename in (
                ("output_file", "output.json"), ("stats_file", "stats.json"),
                ("progress_file", "progress.jsonl"), ("conversation_file", "conversation.json"))})
            self.tasks[task_id] = record
            state = self.get_or_create_multi_agent_state(conversation_id)
            state.register_instance(AgentInstance(agent_id=agent_id, display_name=display_name, task_id=task_id, status="running", summary=summary))
            self._save_state()
        try:
            instance = SubAgentTask(self, record, task_message, system_prompt, model_key,
                thinking_mode, multi_agent_state=state, display_name=display_name)
            self._launch(instance)
        except Exception as exc:
            self._mark_task_done(task_id, False, str(exc), 0)
            return {"success": False, "error": str(exc)}
        return {"success": True, "task_id": task_id}

    def _launch(self, instance):
        task_id, agent_id = instance.task_id, instance.agent_id
        loop = self._ensure_event_loop()

        def start():
            handle = loop.create_task(instance.run())
            instance._task = handle
            with self._state_lock:
                self._running_tasks[task_id] = handle
                self._sub_agent_instances[agent_id] = instance

            def finished(_future):
                with self._state_lock:
                    if self._running_tasks.get(task_id) is handle:
                        self._running_tasks.pop(task_id, None)
                    if self._sub_agent_instances.get(agent_id) is instance:
                        self._sub_agent_instances.pop(agent_id, None)
                self._save_state()
            handle.add_done_callback(finished)
            return handle

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            return start()

        async def schedule():
            return start()
        return asyncio.run_coroutine_threadsafe(schedule(), loop).result(timeout=60)

    @staticmethod
    def _has_fixed_access(record):
        try:
            from modules.execution_scope import ACCESS_LEVELS
            if record.get("access_level") not in ACCESS_LEVELS or not record.get("working_dir"):
                return False
            scope = ExecutionScope(executor_kind="sub_agent", actor_id=record["task_id"],
                workspace_root=Path(record["workspace_root"]), access_level=record["access_level"],
                working_dir=Path(record["working_dir"]))
            fixed_workspace_root(scope)
            fixed_working_dir(scope)
            return True
        except (KeyError, ValueError, OSError, RuntimeError):
            return False

    def _latest_task_for_agent(self, agent_id):
        return next((record for record in self.tasks.values() if record.get("agent_id") == agent_id), None)

    def _find_or_revive_sub_agent_task(self, agent_id):
        with self._registry.revive_lock:
            return self._find_or_revive_locked(agent_id)

    def _find_or_revive_locked(self, agent_id):
        record = self._latest_task_for_agent(agent_id)
        if not record or record.get("status") == "terminated":
            return None
        if self.owner_conversation_id and record["conversation_id"] != self.owner_conversation_id:
            return None
        if Path(record["workspace_root"]) != self.project_path or not self._has_fixed_access(record):
            return None
        instance = self._sub_agent_instances.get(agent_id)
        if instance is not None and instance._task is not None and not instance._task.done():
            return instance
        return self._revive_sub_agent(agent_id)

    def _revive_sub_agent(self, agent_id):
        record = self._latest_task_for_agent(agent_id)
        if not record or record.get("status") == "terminated" or not self._has_fixed_access(record):
            return None
        try:
            from modules.sub_agent.recovery import restore_child_context
            root = Path(record["task_root"])
            messages = restore_child_context(Path(record["conversation_file"]))
            state = self.get_or_create_multi_agent_state(record["conversation_id"])
            instance = SubAgentTask(self, record, (root / "task.txt").read_text(encoding="utf-8"),
                (root / "system_prompt.txt").read_text(encoding="utf-8"), record["model_key"],
                record["thinking_mode"], multi_agent_state=state,
                display_name=record["name"])
            instance.messages = messages
            from modules.sub_agent.usage import restore_statistics
            restore_statistics(instance)
            instance._idle = True
            record.update(status="idle", updated_at=time.time())
            state.mark_status(agent_id, "idle")
            self._launch(instance)
            self._save_state()
            return instance
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("Cannot restore child %s: %s", record.get("name"), exc)
            return None

    def restore_for_activation(self):
        if not self.owner_conversation_id:
            return 0
        with self._activation_lock:
            if self._activation_restored:
                return 0
            restored = self.restore_running_tasks()
            self._activation_restored = True
            return restored

    def restore_running_tasks(self):
        restored = 0
        for record in list(self.tasks.values()):
            if record["conversation_id"] != self.owner_conversation_id or record["status"] == "terminated":
                continue
            if record["task_id"] in self._running_tasks:
                continue
            if self._find_or_revive_sub_agent_task(record["agent_id"]):
                restored += 1
        return restored

    def inject_message_to_sub_agent(self, agent_id, message_text):
        instance = self._find_or_revive_sub_agent_task(agent_id)
        if instance is None:
            return False
        instance.enqueue_work(message_text)
        return True

    def stop_sub_agent(self, *, task_id=None, agent_id=None, stopped_by="user"):
        if stopped_by not in {"user", "master"}:
            raise ValueError("无效的停止来源。")
        record = self.tasks.get(task_id) if task_id else self._latest_task_for_agent(agent_id)
        if not record or record["status"] == "terminated":
            return {"success": False, "error": "子智能体不存在或已终结。"}
        instance = self._sub_agent_instances.get(record["agent_id"])
        if instance is not None:
            requested = instance.request_stop(stopped_by=stopped_by)
        else:
            requested = record.get("status") == "running"
            record.update(status="idle", work_paused=True, updated_at=time.time())
            if requested:
                record.update(end_reason="stopped", final_result=(
                    "被主智能体取消" if stopped_by == "master" else "被用户取消"))
            state = self.get_multi_agent_state(record["conversation_id"])
            if state:
                state.mark_status(record["agent_id"], "idle")
            self._save_state()
        message = "已请求立即取消当前工作；停止后保留上下文，可发送消息继续。" if requested else "子智能体当前已空闲，可发送消息继续。"
        return {"success": True, "name": record["name"], "message": message}

    def soft_stop_all_agents(self, conversation_id):
        records = [record for record in self.tasks.values() if record["conversation_id"] == conversation_id
                   and record["status"] == "running"]
        for record in records:
            self.stop_sub_agent(task_id=record["task_id"])
        return len(records)

    def terminate_sub_agent(self, *, task_id=None, agent_id=None):
        record = self.tasks.get(task_id) if task_id else self._latest_task_for_agent(agent_id)
        if not record:
            return {"success": False, "error": "子智能体不存在。"}
        record.update(status="terminated", updated_at=time.time())
        state = self.get_multi_agent_state(record["conversation_id"])
        if state:
            state.mark_status(record["agent_id"], "terminated")
        instance = self._sub_agent_instances.get(record["agent_id"])
        if instance is not None:
            instance._cancelled = True
            handle = instance._task
            if handle is not None and not handle.done():
                handle.get_loop().call_soon_threadsafe(handle.cancel)
        self._save_state()
        return {"success": True, "name": record["name"], "message": "子智能体已终结。"}

    def _mark_task_done(self, task_id, success, summary, runtime_seconds):
        record = self.tasks.get(task_id)
        if not record or record.get("status") == "terminated":
            return
        record.update(status="idle" if success else "failed", final_result=summary,
                      updated_at=time.time(), end_reason="completed" if success else "error")
        state = self.get_multi_agent_state(record["conversation_id"])
        if state:
            state.mark_status(record["agent_id"], record["status"])
        self._save_state()

    def reconcile_task_states(self, conversation_id=None):
        # Running records are updated by their owning loop, not inferred from files.
        return 0

    def get_overview(self, conversation_id=None):
        from modules.sub_agent.named import runtime_agent_view
        rows = []
        for record in list(self.tasks.values()):
            if record.get("conversation_id") != conversation_id:
                continue
            row = runtime_agent_view(record)
            row["display_name"] = row["name"]
            row["last_tool"] = record.get("last_tool")
            try:
                stats = json.loads(Path(record["stats_file"]).read_text(encoding="utf-8"))
                row.update(current_context_tokens=stats.get("current_context_tokens", 0),
                           stats_summary=self._build_stats_summary(stats))
            except (OSError, ValueError):
                row["current_context_tokens"] = 0
            rows.append(row)
        return sorted(rows, key=lambda row: row.get("created_at") or 0, reverse=True)

    def lookup_task(self, *, task_id=None, agent_id=None):
        return self.tasks.get(task_id) if task_id else self._latest_task_for_agent(agent_id)

    async def execute_tool_for_sub_agent(self, tool_name, arguments, agent_id=None, executor=None):
        if executor is None or executor.manager is not self or executor.agent_id != agent_id:
            return {"success": False, "error": "子工具调用缺少可信执行实例。"}
        if tool_name in {"todo_create", "todo_update_task", "todo_get"}:
            from modules.sub_agent.todos import execute_todo
            return execute_todo(self._sub_agent_todos.setdefault(agent_id, {}), tool_name, arguments)
        try:
            from modules.sub_agent.execution import execute_child_tool
            result = await execute_child_tool(self, tool_name, arguments, executor)
            return json.loads(result) if isinstance(result, str) else result
        except Exception as exc:
            logger.exception("Child tool failed: %s", tool_name)
            return {"success": False, "error": str(exc)}
