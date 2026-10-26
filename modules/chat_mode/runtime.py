"""Lightweight adapter for the existing shared task, stream and tool loops."""
from __future__ import annotations

from pathlib import Path

from config.model_profiles import get_default_model_key, get_model_profile
from utils.api_client import APIClient
from modules.chat_mode.context import ChatContext
from modules.chat_mode.definitions import NAMES, definitions
from modules.chat_mode.memory import ChatMemory
from modules.chat_mode.prompts import build_system
from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tools import ChatTools


class ChatRuntime:
    conversation_kind = "chat"

    def __init__(self, storage: ChatStorage, conversation_id: str, turn_id: str,
                 *, preferences_dir: Path, username: str, role="user", api_client=None):
        self.storage = storage
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.preferences_dir = Path(preferences_dir)
        self.data_dir = storage.root
        self.username = username
        self.user_role = role
        self.context_manager = ChatContext(storage, conversation_id, turn_id)
        self.context_manager.main_terminal = self
        self.memory_manager = ChatMemory(storage.root)
        metadata = self.context_manager.conversation_metadata
        self.thinking_mode = bool(metadata.get("thinking_mode", True))
        self.run_mode = "thinking" if self.thinking_mode else "fast"
        self.reasoning_effort = metadata.get("reasoning_effort")
        self.api_client = api_client or APIClient(thinking_mode=self.thinking_mode, web_mode=True)
        from modules.external_session import resolve_context_headers
        self.api_client.extra_headers_resolver = lambda base_url: resolve_context_headers(
            base_url, self.context_manager, base_dir=self.preferences_dir,
        )
        self.model_key = metadata.get("model_key") or get_default_model_key()
        self.model_profile = get_model_profile(self.model_key) if self.model_key else None
        if self.model_profile:
            self.apply_model_profile(self.model_profile)
        self.tools = ChatTools(self)
        self._tool_loop_active = False
        self.usage_tracker = None

    def record_model_call(self, is_thinking):
        if self.usage_tracker is None:
            return True, {}
        return self.usage_tracker.check_and_increment("thinking" if is_thinking else "fast")

    def record_search_call(self):
        if self.usage_tracker is None:
            return True, {}
        return self.usage_tracker.check_and_increment("search")

    def apply_model_profile(self, profile):
        self.model_profile = profile
        self.api_client.model_key = self.model_key
        self.api_client.apply_profile(profile)
        self.api_client.thinking_mode = self.thinking_mode
        self.api_client.reasoning_effort = self.reasoning_effort

    def set_model(self, model_key):
        profile = get_model_profile(model_key)
        if not profile:
            raise ValueError("Unknown Chat model")
        previous = self.model_profile or {}
        if previous and (previous.get("api_protocol") == "responses") != (profile.get("api_protocol") == "responses"):
            raise ValueError("Cannot switch Chat API protocol within a conversation")
        self.model_key = model_key
        self.apply_model_profile(profile)

    def set_run_mode(self, run_mode):
        if run_mode not in {"fast", "thinking"}:
            raise ValueError("Invalid Chat thinking mode")
        self.run_mode = run_mode
        self.thinking_mode = run_mode == "thinking"
        self.api_client.thinking_mode = self.thinking_mode

    def define_tools(self):
        return definitions()

    def evaluate_tool_permission(self, name, arguments):
        allowed = name in NAMES
        if name in {"web_search", "extract_webpage"}:
            allowed = allowed and self.tools.network_enabled()
        return {"allowed": allowed, "mode": "chat", "code": "chat_tool_unavailable",
                "message": "该工具在当前 Chat 设置下不可用" if not allowed else ""}

    async def handle_tool_call(self, name, arguments):
        return await self.tools.execute(name, arguments)

    def build_context(self):
        # This is a model context, never a scan of a project or execution host.
        return {}

    def build_messages(self, context=None, message=None):
        from modules.chat_mode.compression import api_history
        self.context_manager.refresh()
        return [{"role": "system", "content": build_system(self)},
                *api_history(self.context_manager)]

    async def prepare_model_messages(self, messages, *, sender):
        from modules.chat_mode.compression import maybe_compress
        self.context_manager.consume_network_notices()
        await maybe_compress(self, sender)
        return self.build_messages()
