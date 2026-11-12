"""Isolated main Agent fixtures; never read real user settings or credentials."""
from __future__ import annotations

import atexit
from copy import deepcopy
from datetime import datetime
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
_RUNTIME = Path(tempfile.mkdtemp(prefix="astrion_system_prompt_"))
atexit.register(lambda: shutil.rmtree(_RUNTIME, ignore_errors=True))
os.environ.update(
    ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(_RUNTIME),
    DATA_DIR=str(_RUNTIME / "data"), LOGS_DIR=str(_RUNTIME / "logs"),
    USER_SPACE_DIR=str(_RUNTIME / "users"), API_USER_SPACE_DIR=str(_RUNTIME / "api_users"),
    TERMINAL_SANDBOX_MODE="host", WEB_SECRET_KEY="isolated-test-only",
)
os.environ.pop("ASTRION_TRUSTED_PROXIES", None)

from core.main_terminal_parts.context.messages import MessagesMixin  # noqa: E402
from core.main_terminal_parts.context import system_prompt as composer  # noqa: E402
from utils.conversation_manager import ConversationManager  # noqa: E402


class Owner(MessagesMixin):
    def __init__(self, root: Path, *, metadata: dict | None = None):
        self.project_path = root / "project"
        self.project_path.mkdir(parents=True, exist_ok=True)
        self.data_dir = root / "data"
        self.manager = ConversationManager(base_dir=str(self.data_dir), project_path=str(self.project_path))
        conversation_id = self.manager.create_conversation(
            project_path=str(self.project_path), initial_messages=[], model_key="test-model",
            metadata_overrides={"workspace_memory_enabled": False, **(metadata or {})},
        )
        self.context_manager = SimpleNamespace(
            current_conversation_id=conversation_id,
            conversation_metadata=deepcopy(self.manager.read_conversation(conversation_id)["metadata"]),
            custom_personalization_config={"agents_md_auto_inject": True, "agents_skills_scan_enabled": False},
            custom_system_prompt="custom instruction",
            _get_conversation_manager_for_id=lambda _id: self.manager,
            _build_workspace_system_message=lambda _context: "workspace start: 2026/11/12 20:50:27 UTC+08:00",
        )
        self.model_key = "test-model"
        self.disabled_tools = set()
        self.api_client = SimpleNamespace(
            api_protocol="responses",
            get_current_thinking_mode=lambda: True,
            _select_api_config=lambda _: {"model_id": "test-model"},
            _build_responses_instructions=lambda _: "provider base\n\nbridge",
        )
        self.permission = "permission initial"

    def load_prompt(self, name):
        return (ROOT / "prompts" / f"{name}.txt").read_text()

    def _load_root_md_content(self, _filename):
        return "project rules"

    def _load_root_md_updated_at(self, _filename):
        return "（已保存时间）"

    def _load_sub_md_paths(self, _filename):
        return [], 0

    def _build_memory_system_content(self, *, template_name):
        return self.load_prompt(template_name).replace("{global_memory}", "user memory").replace("{project_memory_list}", "project index")

    def _build_permission_mode_message(self):
        return self.permission

    def _build_execution_mode_message(self):
        return "execution initial"

    def _build_work_mode_message(self):
        return "work mode initial"

    def _format_disabled_tool_notice(self):
        return ""

    def _tool_calls_followed_by_tools(self, *_args):
        return True

    def context(self):
        return {"project_info": {"path": str(self.project_path)}, "conversation": [
            {"role": "user", "content": "question", "metadata": {}}
        ]}

    def saved(self):
        return self.manager.read_conversation(self.context_manager.current_conversation_id)


def isolated_builders():
    """Only external catalogs/capabilities are fake; persistence and templates are real."""
    from contextlib import ExitStack
    stack = ExitStack()
    patches = {
        "get_skills_catalog": {}, "merge_enabled_skills": [], "build_skills_list": "",
        "model_supports_image": True, "model_supports_video": False,
        "get_model_prompt_replacements": {"model_description": "model description"},
    }
    for name, result in patches.items():
        stack.enter_context(patch.object(composer, name, return_value=result))
    stack.enter_context(patch.object(composer, "_features", return_value="feature states"))
    stack.enter_context(patch.object(composer, "_modify_history", return_value="conversation-specific directory"))
    return stack
