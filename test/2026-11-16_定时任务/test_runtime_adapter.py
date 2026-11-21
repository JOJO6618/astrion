"""AgentRuntimeAdapter 隔离集成测试：真实配置/工作区/对话存储/门闸，mock 任务受理与观察依赖。

环境：
- 在导入任何 config/server 模块之前把 ASTRION_IGNORE_DOTENV=1，并把
  DATA_DIR / LOGS_DIR / USER_SPACE_DIR / API_USER_SPACE_DIR / DEPLOY_CONFIG_DIR
  指向本文件创建的临时目录；不会读取真实 .env、用户数据或凭据。
- 注册一个 apikey 为字面量的 test-model，仅用于通过模型能力校验，不会发起网络请求。
- 任务受理（runtime_service.create_task/observe 依赖）与 terminal 执行端用替身；
  配置解析、身份校验、工作区解析、对话文件读写、主任务门闸使用真实实现。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import schedule_fixtures as fx

TMP_ROOT = Path(tempfile.mkdtemp(prefix="astrion-scheduled-runtime-"))
for _sub in ("data", "logs", "users", "api_users", "config"):
    (TMP_ROOT / _sub).mkdir(parents=True, exist_ok=True)
_BOOTSTRAP_PROJECT = TMP_ROOT / "project"
_BOOTSTRAP_PROJECT.mkdir()

os.environ.update(
    {
        "ASTRION_IGNORE_DOTENV": "1",
        "ASTRION_DATA_ROOT": str(TMP_ROOT),
        "DATA_DIR": str(TMP_ROOT / "data"),
        "LOGS_DIR": str(TMP_ROOT / "logs"),
        "USER_SPACE_DIR": str(TMP_ROOT / "users"),
        "API_USER_SPACE_DIR": str(TMP_ROOT / "api_users"),
        "DEPLOY_CONFIG_DIR": str(TMP_ROOT / "config"),
        "TERMINAL_SANDBOX_MODE": "host",
    }
)
os.environ.pop("HOST_WORKSPACES_FILE", None)

(TMP_ROOT / "config" / "custom_models.json").write_text(
    json.dumps(
        {
            "models": [
                {
                    "model_name": "test-model",
                    "url": "http://127.0.0.1:9/v1",
                    "apikey": "test-key",
                    "model_id": "test-model",
                    "reasoning_capability": "fast,thinking",
                }
            ]
        }
    ),
    encoding="utf-8",
)

_DATA_DIR = Path(os.environ["DATA_DIR"])


def use_workspaces(entries: dict[str, Path], default: str | None = None) -> None:
    payload = {
        "version": 1,
        "default_workspace_id": default or next(iter(entries)),
        "workspaces": [
            {"workspace_id": key, "path": str(path), "label": key} for key, path in entries.items()
        ],
    }
    (TMP_ROOT / "config" / "host_workspaces.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


use_workspaces({"ws-bootstrap": _BOOTSTRAP_PROJECT})

# 环境断言：确保 config 使用临时目录而不是真实数据根。
# macOS 上 tempfile 给出 /var/folders/...，而 config 可能规范化为 /private/var/folders/...
# （/var 是符号链接），必须按 resolve() 后的真实路径比较，不能直接比字符串。
import config as _config  # noqa: E402

assert Path(_config.DATA_DIR).resolve() == _DATA_DIR.resolve(), (
    _config.DATA_DIR,
    _DATA_DIR,
)
assert Path(_config.DEPLOY_CONFIG_DIR).resolve().is_relative_to(TMP_ROOT.resolve()), (
    _config.DEPLOY_CONFIG_DIR,
    TMP_ROOT,
)

from modules.scheduled_tasks import schema  # noqa: E402
from modules.scheduled_tasks.runtime import AgentRuntimeAdapter  # noqa: E402
from modules.scheduled_tasks.service import SchedulerService  # noqa: E402
from modules.scheduled_tasks.store import ScheduleStore  # noqa: E402
from utils.conversation_manager import ConversationManager  # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP_ROOT, ignore_errors=True)


DEFAULT_PREFS = {
    "default_model": "test-model",
    "default_run_mode": "thinking",
    "default_work_mode": "plan",
    "default_permission_mode": "approval",
    "default_reasoning_effort": None,
}

REALISTIC_CONVERSATION_METADATA = {
    "conversation_kind": "agent",
    "work_mode": "execute",
    "permission_mode": "approval",
    "execution_mode": "sandbox",
    "network_permission": "restricted",
    "model_key": "test-model",
    "run_mode": "fast",
    "thinking_mode": False,
    "reasoning_effort": None,
}


class FakeContextManager:
    def __init__(self, manager: ConversationManager):
        self.manager = manager
        self.conversation_metadata: dict = {}

    def _get_conversation_manager_for_id(self, conversation_id: str) -> ConversationManager:
        return self.manager


class FakeTerminal:
    """按 WebTerminal 的真实签名记录设置调用；不启动执行端。"""

    def __init__(self, manager: ConversationManager):
        self.calls: dict = {}
        self.context_manager = FakeContextManager(manager)
        self.pending_permission_mode = "approval"
        self.pending_execution_mode = "sandbox"
        self.pending_network_permission = "restricted"

    def set_model(self, model_key: str) -> str:
        self.calls["model_key"] = model_key
        return model_key

    def set_run_mode(self, mode: str) -> str:
        self.calls["run_mode"] = mode
        return mode

    def set_reasoning_effort(self, effort):
        self.calls["reasoning_effort"] = effort
        return effort

    def switch_work_mode(self, mode: str, *, persist=True, conversation_id=None):
        self.calls["work_mode"] = mode
        return {"success": True}

    def set_permission_mode(self, mode: str, *, persist=True, conversation_id=None):
        self.calls["permission_mode"] = mode
        return mode

    def set_execution_mode(self, mode: str):
        self.calls["execution_mode"] = mode
        return {"success": True}

    def set_network_permission(self, mode: str):
        self.calls["network_permission"] = mode
        return mode


class FakeGoalState:
    def __init__(self, state: dict):
        self.state = state


def fake_goal_class(state: dict):
    return lambda *args, **kwargs: FakeGoalState(state)


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.case_root = Path(tempfile.mkdtemp(dir=TMP_ROOT, prefix="case-"))
        self.addCleanup(shutil.rmtree, self.case_root, True)
        self.project = self.case_root / "workspace"
        self.project.mkdir()
        self.workspace_id = "ws_" + uuid.uuid4().hex[:8]
        use_workspaces({self.workspace_id: self.project})
        self.set_prefs()
        self.adapter = AgentRuntimeAdapter()
        self._stdout = io.StringIO()
        redirect = contextlib.redirect_stdout(self._stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def set_prefs(self, **overrides):
        payload = {**DEFAULT_PREFS, **overrides}
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        (_DATA_DIR / "personalization.json").write_text(json.dumps(payload), encoding="utf-8")

    def host_principal(self, workspace_id: str | None = None) -> dict:
        return {
            "username": "host",
            "host_mode": True,
            "host_workspace_id": workspace_id or self.workspace_id,
        }

    def definition(
        self,
        *,
        mode: str = "new",
        conversation_id: str | None = None,
        configuration: dict | None = None,
        goal: dict | None = None,
        workspace_id: str | None = None,
        name: str = "计划标题",
        message: str = "执行任务",
    ) -> dict:
        conversation = {"mode": mode}
        if conversation_id:
            conversation["id"] = conversation_id
        raw = {
            "schema_version": 1,
            "name": name,
            "message": message,
            "enabled": False,
            "schedule": {
                "type": "interval",
                "timezone": "UTC",
                "every": 60,
                "unit": "minutes",
                "anchor": "2026-01-01T00:00:00Z",
            },
            "target": {"workspace_id": workspace_id or self.workspace_id, "conversation": conversation},
            "configuration": configuration or {"mode": "inherit"},
            "goal": goal or {"mode": "off"},
        }
        return schema.normalize_definition(raw, None)

    def create_conversation(self, *, project: Path | None = None, metadata: dict | None = None):
        manager = ConversationManager(base_dir=str(_DATA_DIR), project_path=str(project or self.project))
        conversation_id = manager.create_conversation(
            str(project or self.project),
            model_key="test-model",
            run_mode="fast",
            thinking_mode=False,
            initial_messages=[{"role": "assistant", "content": "最终摘要"}],
            metadata_overrides={**REALISTIC_CONVERSATION_METADATA, **(metadata or {})},
        )
        return conversation_id, manager

    def manager(self, project: Path | None = None) -> ConversationManager:
        return ConversationManager(base_dir=str(_DATA_DIR), project_path=str(project or self.project))


class SnapshotTest(RuntimeCase):
    def test_new_conversation_snapshot_freezes_defaults(self):
        out = self.adapter.validate(self.definition(), self.host_principal())
        snapshot = out.get("configuration_snapshot")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["model_key"], "test-model")
        self.assertEqual(snapshot["run_mode"], "thinking")
        self.assertIs(snapshot["thinking_mode"], True)
        self.assertEqual(snapshot["work_mode"], "plan")
        self.assertEqual(snapshot["permission_mode"], "readonly")
        self.assertEqual(snapshot["execution_mode"], "sandbox")
        self.assertEqual(snapshot["network_permission"], "restricted")
        self.assertIs(snapshot["chatgpt_fast_mode"], False)
        self.assertEqual(out["username"], "host")
        self.assertEqual(out["role"], "admin")

    def test_custom_configuration_snapshot_reflects_overrides(self):
        definition = self.definition(
            configuration={
                "mode": "custom",
                "model_key": "test-model",
                "thinking": False,
                "work_mode": "execute",
                "permission_mode": "auto_approval",
                "execution_mode": "sandbox",
                "network_enabled": True,
            }
        )
        snapshot = self.adapter.validate(definition, self.host_principal())["configuration_snapshot"]
        self.assertEqual(snapshot["work_mode"], "execute")
        self.assertEqual(snapshot["permission_mode"], "auto_approval")
        self.assertEqual(snapshot["execution_mode"], "sandbox")
        self.assertEqual(snapshot["network_permission"], "full")
        self.assertEqual(snapshot["run_mode"], "fast")
        self.assertFalse(snapshot["thinking_mode"])

    def test_goal_start_forces_execute_and_auto_approval(self):
        definition = self.definition(goal={"mode": "start", "max_turns": 2})
        snapshot = self.adapter.validate(definition, self.host_principal())["configuration_snapshot"]
        self.assertEqual(snapshot["work_mode"], "execute")
        self.assertEqual(snapshot["permission_mode"], "auto_approval")
        self.assertEqual(snapshot["execution_mode"], "sandbox")

    def test_existing_conversation_does_not_freeze_snapshot(self):
        conversation_id, _ = self.create_conversation()
        out = self.adapter.validate(
            self.definition(mode="existing", conversation_id=conversation_id), self.host_principal()
        )
        self.assertNotIn("configuration_snapshot", out)

    def test_service_submit_persists_and_reuses_snapshot(self):
        store = ScheduleStore(self.case_root / "schedules.sqlite3")
        service = SchedulerService(store, self.adapter)
        value = self.definition()
        plan = service.submit("host", value, self.host_principal(), None)
        stored = plan["definition"]
        stored_plan = store.get("host", stored["id"])
        self.assertEqual(stored_plan["principal"]["configuration_snapshot"]["work_mode"], "plan")
        self.addCleanup(self.set_prefs)

        self.set_prefs(default_work_mode="execute", default_permission_mode="auto_approval", default_run_mode="fast")
        updated = service.submit(
            "host",
            {**stored, "name": "改名"},
            self.host_principal(),
            None,
            overwrite=True,
        )
        reused = store.get("host", updated["definition"]["id"])["principal"]["configuration_snapshot"]
        self.assertEqual(reused["work_mode"], "plan")
        self.assertEqual(reused["permission_mode"], "readonly")
        self.assertEqual(reused["run_mode"], "thinking")

        custom = service.submit(
            "host",
            {
                **updated["definition"],
                "configuration": {
                    "mode": "custom",
                    "work_mode": "execute",
                    "permission_mode": "auto_approval",
                    "execution_mode": "sandbox",
                    "model_key": "test-model",
                },
            },
            self.host_principal(),
            None,
            overwrite=True,
        )
        snapshot = store.get("host", custom["definition"]["id"])["principal"]["configuration_snapshot"]
        self.assertEqual(snapshot["work_mode"], "execute")
        self.assertEqual(snapshot["permission_mode"], "auto_approval")

    def test_service_submit_existing_conversation_has_no_snapshot(self):
        conversation_id, _ = self.create_conversation()
        store = ScheduleStore(self.case_root / "schedules.sqlite3")
        service = SchedulerService(store, self.adapter)
        value = self.definition(mode="existing", conversation_id=conversation_id)
        plan = service.submit("host", value, self.host_principal(), None)
        stored_plan = store.get("host", plan["definition"]["id"])
        self.assertNotIn("configuration_snapshot", stored_plan["principal"])


class IdentityTest(RuntimeCase):
    def test_host_identity_is_admin_and_workspace_scoped(self):
        out = self.adapter.validate(self.definition(), self.host_principal())
        self.assertEqual(out["username"], "host")
        self.assertEqual(out["role"], "admin")
        self.assertIs(out["host_mode"], True)
        self.assertEqual(out["host_workspace_id"], self.workspace_id)
        self.assertIs(out["is_api_user"], False)

    def test_host_principal_requires_host_mode(self):
        with mock.patch("config.IS_HOST_MODE", False):
            with self.assertRaises(PermissionError):
                self.adapter.validate(self.definition(), self.host_principal())

    def test_legacy_regular_principal_is_rejected(self):
        principal = {"username": "ghost", "host_mode": False, "host_workspace_id": self.workspace_id}
        with self.assertRaises(PermissionError):
            self.adapter.validate(self.definition(), principal)

    def test_legacy_api_principal_is_rejected(self):
        principal = {"username": "api_user", "host_mode": False, "is_api_user": True}
        with self.assertRaises(PermissionError):
            self.adapter.validate(self.definition(), principal)

    def test_non_host_username_with_host_flag_is_rejected(self):
        principal = {"username": "alice", "host_mode": True, "host_workspace_id": self.workspace_id}
        with self.assertRaises(PermissionError):
            self.adapter.validate(self.definition(), principal)

    def test_unknown_workspace_is_rejected(self):
        principal = self.host_principal()
        definition = self.definition(workspace_id="ws_missing")
        with self.assertRaises(ValueError) as ctx:
            self.adapter.validate(definition, principal)
        self.assertIn("工作区不存在", str(ctx.exception))

    def test_host_admin_explicit_direct_with_readonly_permission_is_not_silently_downgraded(self):
        """显式 direct 与继承的 readonly 冲突时应报错，而不是悄悄改成 sandbox。"""
        definition = self.definition(
            configuration={"mode": "custom", "work_mode": "execute", "execution_mode": "direct"}
        )
        with self.assertRaises((PermissionError, ValueError)):
            self.adapter.validate(definition, self.host_principal())

    def test_existing_conversation_from_other_workspace_is_rejected(self):
        other_project = self.case_root / "other-workspace"
        other_project.mkdir()
        manager = self.manager()
        conversation_id = manager.create_conversation(
            str(other_project),
            model_key="test-model",
            run_mode="fast",
            thinking_mode=False,
            metadata_overrides=dict(REALISTIC_CONVERSATION_METADATA),
        )
        with self.assertRaises(ValueError) as ctx:
            self.adapter.validate(
                self.definition(mode="existing", conversation_id=conversation_id), self.host_principal()
            )
        self.assertIn("不属于所选工作区", str(ctx.exception))

    def test_chat_conversation_is_rejected(self):
        conversation_id, _ = self.create_conversation(metadata={"conversation_kind": "chat"})
        with self.assertRaises(ValueError) as ctx:
            self.adapter.validate(
                self.definition(mode="existing", conversation_id=conversation_id), self.host_principal()
            )
        self.assertIn("Agent", str(ctx.exception))

    def test_missing_existing_conversation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.validate(
                self.definition(mode="existing", conversation_id="conv_missing"), self.host_principal()
            )


class StartHandoffTest(RuntimeCase):
    def start(self, definition: dict, terminal: FakeTerminal, create_task=None):
        principal = self.adapter.validate(definition, self.host_principal())
        run = {
            "id": "run_" + uuid.uuid4().hex,
            "schedule_id": "plan_" + uuid.uuid4().hex,
            "username": "host",
            "definition": definition,
            "principal": principal,
        }
        fake_workspace = SimpleNamespace(workspace_id=self.workspace_id, username="host")
        captured = {}

        def default_create(ctx):
            captured["ctx"] = ctx
            return SimpleNamespace(task_id="task-1", conversation_id=ctx.params.conversation_id)

        with ExitStack() as stack:
            stack.enter_context(
                mock.patch("server.context.get_user_resources", return_value=(terminal, fake_workspace))
            )
            stack.enter_context(mock.patch("server.context.ensure_conversation_loaded", return_value=None))
            stack.enter_context(
                mock.patch(
                    "server.runtime.runtime_service.create_task",
                    side_effect=create_task or default_create,
                )
            )
            if definition["target"]["conversation"]["mode"] == "existing":
                stack.enter_context(mock.patch("server.tasks.task_manager.list_tasks", return_value=[]))
                stack.enter_context(
                    mock.patch(
                        "server.context.background.background_status",
                        return_value={
                            "has_running_sub_agents": False,
                            "has_running_background_commands": False,
                            "has_running_multi_agent": False,
                        },
                    )
                )
                stack.enter_context(
                    mock.patch("modules.goal_state_manager.GoalStateManager", fake_goal_class({"status": "stopped"}))
                )
            result = self.adapter.start(run)
        return result, captured, run

    def test_new_conversation_creates_file_metadata_and_hands_off_gate(self):
        manager = self.manager()
        terminal = FakeTerminal(manager)
        definition = self.definition(
            configuration={
                "mode": "custom",
                "model_key": "test-model",
                "thinking": True,
                "work_mode": "execute",
                "permission_mode": "auto_approval",
                "execution_mode": "sandbox",
                "network_enabled": True,
            },
            name="每日巡检",
            message="检查项目",
        )
        result, captured, run = self.start(definition, terminal)
        ctx = captured["ctx"]
        self.assertEqual(result["conversation_id"], ctx.params.conversation_id)
        self.assertEqual(result["task_id"], "task-1")
        self.assertEqual(ctx.params.message, "检查项目")
        self.assertEqual(ctx.params.model_key, "test-model")
        self.assertEqual(ctx.params.run_mode, "thinking")
        self.assertEqual(ctx.params.message_source, "schedule")
        self.assertIsNone(ctx.params.goal_action)
        token = ctx.directives.main_task_gate_token
        self.assertTrue(token)
        self.assertEqual(getattr(terminal, "_main_task_gate_token"), token)
        self.assertEqual(ctx.principal.username, "host")
        self.assertEqual(ctx.principal.workspace_id, self.workspace_id)
        self.assertEqual(ctx.principal.role, "admin")
        self.assertIs(ctx.principal.host_mode, True)
        self.assertEqual(terminal.calls["work_mode"], "execute")
        self.assertEqual(terminal.calls["permission_mode"], "auto_approval")
        self.assertEqual(terminal.calls["network_permission"], "full")
        doc = manager.read_conversation(result["conversation_id"])
        self.assertEqual(doc["title"], "每日巡检")
        self.assertEqual(doc["metadata"]["schedule_run_id"], run["id"])
        self.assertEqual(doc["metadata"]["schedule_id"], run["schedule_id"])
        self.assertIs(doc["metadata"]["skip_auto_title_generation"], True)
        self.assertEqual(doc["metadata"]["work_mode"], "execute")
        self.assertEqual(doc["metadata"]["model_key"], "test-model")

    def test_existing_conversation_reuses_id_and_applies_custom_settings(self):
        conversation_id, manager = self.create_conversation()
        terminal = FakeTerminal(manager)
        definition = self.definition(
            mode="existing",
            conversation_id=conversation_id,
            configuration={
                "mode": "custom",
                "model_key": "test-model",
                "thinking": False,
                "work_mode": "execute",
                "permission_mode": "auto_approval",
                "execution_mode": "sandbox",
            },
        )
        result, captured, _ = self.start(definition, terminal)
        self.assertEqual(result["conversation_id"], conversation_id)
        token = captured["ctx"].directives.main_task_gate_token
        self.assertTrue(token)
        doc = manager.read_conversation(conversation_id)
        self.assertEqual(doc["metadata"]["work_mode"], "execute")
        self.assertEqual(doc["metadata"]["permission_mode"], "auto_approval")
        self.assertIsNone(doc["metadata"]["pending_permission_mode"])
        self.assertIsNone(doc["metadata"]["pending_execution_mode"])
        self.assertIsNone(doc["metadata"]["pending_network_permission"])
        self.assertEqual(getattr(terminal, "_main_task_gate_token"), token)

    def test_goal_start_passes_goal_params(self):
        terminal = FakeTerminal(self.manager())
        definition = self.definition(goal={"mode": "start", "max_turns": 3, "max_tokens": 9000})
        _, captured, _ = self.start(definition, terminal)
        ctx = captured["ctx"]
        self.assertEqual(ctx.params.goal_action, "start")
        self.assertEqual(ctx.params.goal_options, {"max_turns": 3, "max_tokens": 9000})
        self.assertEqual(ctx.params.message_source, "schedule")
        self.assertEqual(ctx.principal.preferred_model_key, "test-model")
        self.assertEqual(terminal.calls["work_mode"], "execute")

    def custom(self, **overrides):
        return {
            "mode": "custom",
            "model_key": "test-model",
            "work_mode": "execute",
            "permission_mode": "auto_approval",
            "execution_mode": "sandbox",
            **overrides,
        }

    def test_ask_user_notice_defaults_on_for_custom_execute_and_plan(self):
        from modules.scheduled_tasks.runtime import ASK_USER_NOTICE
        for configuration in (
            self.custom(),
            self.custom(work_mode="plan", permission_mode="readonly"),
        ):
            _, captured, _ = self.start(self.definition(configuration=configuration), FakeTerminal(self.manager()))
            self.assertEqual(captured["ctx"].directives.hidden_user_notice, ASK_USER_NOTICE)

    def test_ask_user_notice_skipped_when_allowed_inherited_ask_mode_or_goal(self):
        cases = (
            self.definition(configuration=self.custom(ask_user=True)),
            self.definition(configuration=self.custom(work_mode="ask", permission_mode="approval")),
            self.definition(configuration={"mode": "inherit"}),
            self.definition(configuration=self.custom(), goal={"mode": "start"}),
        )
        for definition in cases:
            _, captured, _ = self.start(definition, FakeTerminal(self.manager()))
            self.assertIsNone(captured["ctx"].directives.hidden_user_notice)

    def test_gate_busy_blocks_before_admission(self):
        terminal = FakeTerminal(self.manager())
        setattr(terminal, "_main_task_gate_token", "foreign-token")
        called = []
        definition = self.definition()
        from modules.scheduled_tasks.service import BusyError

        with self.assertRaises(BusyError):
            self.start(definition, terminal, create_task=lambda context: called.append(context))
        self.assertEqual(called, [])
        self.assertEqual(getattr(terminal, "_main_task_gate_token"), "foreign-token")

    def test_admission_failure_releases_gate(self):
        terminal = FakeTerminal(self.manager())
        definition = self.definition()

        def boom(context):
            raise RuntimeError("admission-boom")

        with self.assertRaises(RuntimeError):
            self.start(definition, terminal, create_task=boom)
        self.assertIsNone(getattr(terminal, "_main_task_gate_token", None))


class ObserveTest(RuntimeCase):
    def observe(self, *, goal: dict | None = None, conversation_id: str, record, pending=None, tasks=None, background=None, stop=False, definition=None):
        definition = definition or self.definition(goal=goal)
        run = {
            "id": "run_observe",
            "schedule_id": "plan_observe",
            "username": "host",
            "definition": definition,
            "principal": self.adapter.validate(definition, self.host_principal()),
            "conversation_id": conversation_id,
            "task_id": "task-1",
            "stop_requested": stop,
        }
        background = background or {
            "has_running_sub_agents": False,
            "has_running_background_commands": False,
            "has_running_multi_agent": False,
        }
        with ExitStack() as stack:
            stack.enter_context(mock.patch("server.runtime.runtime_service.get_task", return_value=record))
            stack.enter_context(
                mock.patch(
                    "server.runtime.runtime_service.list_pending_approvals",
                    return_value=pending or {},
                )
            )
            stack.enter_context(
                mock.patch("server.tasks.task_manager.list_tasks", return_value=tasks or [])
            )
            stack.enter_context(
                mock.patch("server.context.background.background_status", return_value=background)
            )
            if goal and goal.get("mode", "off") != "off":
                state = getattr(self, "goal_state", {"status": "stopped"})
                stack.enter_context(
                    mock.patch("modules.goal_state_manager.GoalStateManager", fake_goal_class(state))
                )
            return self.adapter.observe(run)

    def record(self, status="running", error=None, created_at=1.0, task_id="task-1"):
        return SimpleNamespace(
            task_id=task_id, status=status, error=error, created_at=created_at, task_type="chat"
        )

    def test_missing_task_is_interrupted(self):
        conversation_id, _ = self.create_conversation()
        self.assertEqual(
            self.observe(conversation_id=conversation_id, record=None),
            {"state": "interrupted", "reason": "task_unavailable"},
        )

    def test_goal_active_keeps_run_alive(self):
        conversation_id, _ = self.create_conversation()
        self.goal_state = {"status": "running", "task_id": "task-goal"}
        result = self.observe(
            conversation_id=conversation_id,
            goal={"mode": "start"},
            record=self.record("succeeded"),
        )
        self.assertEqual(result, {"state": "running", "task_id": "task-goal"})

    def test_goal_paused_after_restart_fails(self):
        conversation_id, _ = self.create_conversation()
        self.goal_state = {"status": "paused", "last_message": "应用已重启，点击继续以恢复目标。"}
        result = self.observe(
            conversation_id=conversation_id,
            goal={"mode": "start"},
            record=self.record("succeeded"),
        )
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["reason"], "应用已重启，点击继续以恢复目标。")

    def test_pending_approval_reports_waiting(self):
        conversation_id, _ = self.create_conversation()
        result = self.observe(
            conversation_id=conversation_id,
            record=self.record("running"),
            pending={"tool_approval": [{"id": "a1"}]},
        )
        self.assertEqual(result, {"state": "waiting"})

    def test_pending_approval_after_finish_is_not_waiting(self):
        conversation_id, _ = self.create_conversation()
        result = self.observe(
            conversation_id=conversation_id,
            record=self.record("succeeded"),
            pending={"tool_approval": [{"id": "a1"}]},
        )
        self.assertEqual(result["state"], "success")

    def test_background_work_keeps_run_alive(self):
        conversation_id, _ = self.create_conversation()
        result = self.observe(
            conversation_id=conversation_id,
            record=self.record("succeeded"),
            background={"has_running_sub_agents": True},
        )
        self.assertEqual(result, {"state": "running"})

    def test_following_notice_task_keeps_run_alive_with_latest_task(self):
        conversation_id, _ = self.create_conversation()
        notices = [
            SimpleNamespace(
                conversation_id=conversation_id,
                created_at=2.0,
                task_type="notice",
                status="running",
                task_id="notice-old",
            ),
            SimpleNamespace(
                conversation_id=conversation_id,
                created_at=3.0,
                task_type="notice",
                status="pending",
                task_id="notice-new",
            ),
        ]
        result = self.observe(
            conversation_id=conversation_id, record=self.record("succeeded", created_at=1.0), tasks=notices
        )
        self.assertEqual(result, {"state": "running", "task_id": "notice-new"})

    def test_success_uses_latest_assistant_text_as_summary(self):
        conversation_id, manager = self.create_conversation()
        manager.save_conversation(
            conversation_id,
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [{"type": "text", "text": "第一段"}, {"type": "tool_use", "id": "t"}]},
            ],
        )
        result = self.observe(
            conversation_id=conversation_id,
            record=self.record("succeeded"),
        )
        self.assertEqual(result["state"], "success")
        self.assertEqual(result["summary"], "第一段")
        self.assertIsNone(result["reason"])

    def test_failed_and_stopped_statuses_map(self):
        conversation_id, _ = self.create_conversation()
        failed = self.observe(
            conversation_id=conversation_id, record=self.record("failed", error="boom")
        )
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["reason"], "boom")
        stopped = self.observe(
            conversation_id=conversation_id, record=self.record("stopped")
        )
        self.assertEqual(stopped["state"], "stopped")


class CancelTest(unittest.TestCase):
    def setUp(self):
        self.adapter = AgentRuntimeAdapter()

    def test_cancel_uses_owner_and_task_id(self):
        with mock.patch("server.runtime.runtime_service.cancel_task", return_value=True) as cancel:
            self.adapter.cancel({"username": "host", "task_id": "task-9"})
        cancel.assert_called_once_with("host", "task-9")

    def test_cancel_without_task_is_noop(self):
        with mock.patch("server.runtime.runtime_service.cancel_task") as cancel:
            self.adapter.cancel({"username": "host"})
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
