"""Round boundaries, safe message delivery and per-agent external MCP clients."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path


class SubAgentRuntimeMixin:
    def _init_round_runtime(self) -> None:
        self._round_started = time.monotonic()
        self._round_id = int(self.task_record.get("round_id") or 1)
        import threading
        self._work_lock = threading.RLock()
        self._work_paused = bool(self.task_record.get("work_paused", False))
        self._pending_work = [(item["message"], item.get("timeout"))
                              for item in self.task_record.get("pending_work", [])]
        self._mcp_manager = None
        self._mcp_aliases = {}

    def _round_expired(self) -> bool:
        return self.timeout_seconds is not None and time.monotonic() - self._round_started >= self.timeout_seconds

    def enqueue_work(self, message: str, *, timeout=None) -> None:
        """Only the child's event loop may update its context or work queue."""
        with self._work_lock:
            if self.task_record.get("status") == "terminated" or getattr(self, "_cancelled", False):
                raise RuntimeError("子智能体已终结。")
            self._pending_work.append((message, timeout))
            self._work_paused = False
            self.task_record["work_paused"] = False
            self.task_record["pending_work"] = [{"message": text, "timeout": budget}
                                                 for text, budget in self._pending_work]
            self.task_record.update(status="running", updated_at=time.time())
            if self.multi_agent_state:
                self.multi_agent_state.mark_status(self.agent_id, "running")
            self.manager._save_state()
        def receive():
            if self._continue_event is not None:
                self._continue_event.set()
        loop = self._task.get_loop() if self._task is not None else self.manager._ensure_event_loop()
        loop.call_soon_threadsafe(receive)

    def _accept_work(self, *, new_round: bool) -> bool:
        with self._work_lock:
            if self._continue_event is not None:
                self._continue_event.clear()
            if self._work_paused or not self._pending_work:
                return False
            from modules.git_worktree.children import sync_before_work
            if not sync_before_work(self):
                return False
            pending, self._pending_work = self._pending_work, []
            self.task_record["pending_work"] = []
        if new_round:
            self._round_id += 1
            self._round_started = time.monotonic()
            self.timeout_seconds = pending[-1][1]
            self.task_record.update(round_id=self._round_id, end_reason=None,
                                    timeout_seconds=self.timeout_seconds, status="running")
        for message, _timeout in pending:
            self.messages.append({"role": "user", "content": message})
        self._persist_conversation()
        self.manager._save_state()
        return True

    def _finish_round(self, text: str, *, reason: str = "completed") -> None:
        self.task_record.update(status="idle", end_reason=reason, final_result=text,
                                round_id=self._round_id, updated_at=time.time())
        self._idle = True
        self._mark_idle()
        self._persist_conversation(partial_summary=text[:200])
        self.manager._save_state()
        if self.multi_agent_state:
            inst = self.multi_agent_state.get_instance(self.agent_id)
            if inst:
                inst.last_output = text
        # Publishing happens after the idle state and history have been committed.
        if reason != "completed":
            label = {"stopped": "已停止"}.get(reason, reason)
            text = f"[子智能体 {self.display_name} 本轮{label}]\n{text}"
        self._forward_output_to_master(text, is_final=True)
        self.manager._save_state()

    async def _build_runtime_tools(self) -> list:
        from modules.sub_agent.toolkit import SUB_AGENT_TOOLS
        from modules.sub_agent.definitions import build_sub_tools
        from modules.mcp_access import available_mcp_servers
        from modules.mcp_client_manager import MCPClientManager
        from modules.mcp_client_manager.runtime_scope import MCPRuntimeScope

        tools = list(SUB_AGENT_TOOLS) + build_sub_tools()
        parent = self.manager.terminal
        if not getattr(parent, "mcp_tools_enabled", False):
            return tools
        if self._mcp_manager is not None:
            await asyncio.to_thread(self._mcp_manager.close_all_clients)
        self._mcp_manager = MCPClientManager(
            parent.mcp_server_registry, container_session=self.manager.container_session,
            available_servers_resolver=lambda: available_mcp_servers(parent, "sub"),
            runtime_scope_resolver=lambda: MCPRuntimeScope(
                workspace_path=str(self.manager.project_path), data_dir=str(self.manager.data_dir),
                conversation_id=self.task_record["conversation_id"], actor_id=self.task_id,
                working_dir=str(self.task_record.get("working_dir") or self.manager.project_path),
            ),
        )
        return tools + await self._build_sub_mcp_tools()

    async def _build_sub_mcp_tools(self) -> list:
        from modules.mcp_access import list_mcp_tool

        self._mcp_aliases = {}
        manager = self._mcp_manager
        if manager is None or not manager.available_servers():
            return []
        tools, self._mcp_aliases = await asyncio.to_thread(manager.build_llm_tools)
        if manager.available_servers():
            tools.append(list_mcp_tool())
        return tools

    async def _execute_mcp_tool(self, name: str, arguments: dict):
        if name != "list_mcp_servers" and not name.startswith("mcp__"):
            return None
        if self._mcp_manager is None:
            return {"success": False, "error": "本子智能体没有启用MCP。"}
        if name == "list_mcp_servers":
            from modules.execution_plane.integrations import describe_mcp_servers
            return await asyncio.to_thread(describe_mcp_servers, self._mcp_manager, arguments)
        # External plugins deliberately do not inherit the native tool execution scope.
        clean = {key: value for key, value in arguments.items() if key != "intent"}
        return await asyncio.to_thread(self._mcp_manager.call_tool_by_alias, name, clean,
                                       alias_map=self._mcp_aliases)

    def _mcp_content(self, result: dict):
        """Keep structured results and image attachments in child context."""
        blocks = [{"type": "text", "text": result.get("message") or result.get("error") or ""}]
        structured = result.get("structured_content")
        if structured is not None:
            blocks.append({"type": "text", "text": json.dumps(structured, ensure_ascii=False)})
        from config.model_profiles import model_supports_image
        for item in result.get("content") or []:
            if item.get("type") == "image" and item.get("data") and model_supports_image(self.model_key):
                blocks.append({"type": "image_url", "image_url": {
                    "url": f"data:{item.get('mimeType') or 'image/png'};base64,{item['data']}"}})
        return blocks if len(blocks) > 1 else blocks[0]["text"]
