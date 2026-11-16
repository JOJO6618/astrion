"""modules/scheduled_tasks/tools.py 回归：身份来自终端、只读/计划模式闸门与权限边界。

设计：
- owner 使用带**真实 getter 方法**的终端替身（get_work_mode / get_permission_mode /
  get_execution_mode / get_network_permission / _is_host_mode 都是实际方法，不是临时
  lambda），从而走 tools.py 真实的权限读取路径；
- 执行端 kind 通过 owner.execution_backend 模拟：本地用 FakeExecutionBackend（无
  ``kind`` 属性 → tools 按 local 处理，文件读写走真实执行层契约）；远端用 Mock 注入
  ``kind='remote'``，验证拒绝路径；
- SchedulerService 使用 FakeAdapter，只验证工具自身的校验顺序，不启动真实 runtime；
- 计划全部落在 tempfile 临时 SQLite，不读取真实 .env / 用户数据 / 凭据，无 LLM 调用。
"""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import schedule_fixtures as fx
from modules.execution_plane.fake import FakeExecutionBackend
from modules.scheduled_tasks import tools
from modules.scheduled_tasks.schema import dump_definition
from modules.scheduled_tasks.service import SchedulerService
from modules.scheduled_tasks.store import ConflictError, ScheduleStore


class GuardAdapter:
    """AgentRuntimeAdapter 的替身：暴露工具 _guard 依赖的 validate/_scope/_settings。"""

    def __init__(self):
        self.calls = []
        self.settings = {
            "permission_mode": "auto_approval",
            "execution_mode": "sandbox",
            "network_permission": "restricted",
        }

    def validate(self, definition, principal):
        self.calls.append({"definition": definition, "principal": dict(principal)})
        return {"username": principal.get("username"), "role": principal.get("role") or "user"}

    def _scope(self, principal, workspace_id):
        return {"workspace_id": workspace_id, "identity": principal}, None

    def _settings(self, definition, validated, data):
        return dict(self.settings), None


class TerminalOwner:
    """带真实 getter 的终端替身；只有属性状态可变，方法本身固定。"""

    def __init__(self, username="alice", *, workspace_id="ws-1", role="user", host=False,
                 work_mode="execute", permission="auto_approval", execution="sandbox",
                 network="restricted", backend=None):
        self.username = username
        self.workspace_id = workspace_id
        self.user_role = role
        self._host = host
        self._work_mode = work_mode
        self._permission = permission
        self._execution = execution
        self._network = network
        self.context_manager = SimpleNamespace(current_conversation_id="conv-tools")
        self.execution_backend = backend if backend is not None else FakeExecutionBackend()

    # ---- 真实 getter（tools.py 直接调用这些方法） ----
    def _is_host_mode(self):
        return self._host

    def get_work_mode(self):
        return self._work_mode

    def get_permission_mode(self):
        return self._permission

    def get_execution_mode(self):
        return self._execution

    def get_network_permission(self):
        return self._network


class ScheduleToolsCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ScheduleStore(Path(self._tmp.name) / "tools.sqlite3")
        self.adapter = GuardAdapter()
        self.service = SchedulerService(self.store, self.adapter)
        patcher = mock.patch.object(tools, "get_service", return_value=self.service)
        self.get_service = patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = TerminalOwner()
        self.principal = tools._principal(self.owner)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def submit(self, definition=None, owner=None, *, filename="plan.yaml", overwrite=False):
        owner = owner or self.owner
        text = dump_definition(definition or fx.plan_definition())
        written = owner.execution_backend.files.write_file(filename, text)
        self.assertTrue(written["success"], written)
        return tools.execute(owner, "submit_schedule", {"file_path": filename, "intent": "提交计划", "overwrite": overwrite})

    def guard(self, definition=None, owner=None):
        owner = owner or self.owner
        return tools._guard(owner, self.service, definition or fx.plan_definition(), tools._principal(owner))

    # ------------------------------------------------------------------
    # Schema / 身份
    # ------------------------------------------------------------------
    def test_definitions_shape_and_no_identity_parameters(self):
        defs = {item["function"]["name"]: item["function"] for item in tools.definitions()}
        self.assertEqual(set(defs), {"submit_schedule", "query_schedules", "manage_schedule"})
        submit = defs["submit_schedule"]["parameters"]
        self.assertEqual(submit["required"], ["intent", "file_path"])
        self.assertEqual(submit["properties"]["overwrite"]["type"], "boolean")
        self.assertFalse(submit["additionalProperties"])
        manage = defs["manage_schedule"]["parameters"]
        self.assertEqual(manage["properties"]["action"]["enum"], ["enable", "pause", "delete", "run", "stop"])
        for item in defs.values():
            props = item["parameters"]["properties"]
            # 身份只能来自绑定终端，工具参数里不允许出现 username/owner 一类字段。
            self.assertNotIn("username", props)
            self.assertNotIn("owner", props)

    def test_principal_comes_from_terminal_identity(self):
        host = TerminalOwner("host", host=True, role="admin", workspace_id="host-ws")
        self.assertEqual(
            tools._principal(host),
            {"username": "host", "host_mode": True, "host_workspace_id": "host-ws", "role": "admin", "is_api_user": False},
        )
        api = TerminalOwner("apiuser", role="api", workspace_id="api-ws")
        self.assertEqual(tools._principal(api)["is_api_user"], True)
        self.assertFalse(tools._principal(api)["host_mode"])
        # 普通用户即使 workspace_id 叫 host-ws 也不会被当成 host。
        self.assertFalse(tools._principal(self.owner)["host_mode"])

    def test_query_is_scoped_to_terminal_user(self):
        created = self.submit()
        schedule_id = created["plan"]["definition"]["id"]
        self.assertEqual(self.store.get("alice", schedule_id)["username"], "alice")

        own = tools.execute(self.owner, "query_schedules", {"intent": "查询"})
        self.assertEqual(len(own["plans"]), 1)

        intruder = TerminalOwner("bob")
        self.assertEqual(tools.execute(intruder, "query_schedules", {"intent": "查询"})["plans"], [])
        with self.assertRaises(KeyError):
            tools.execute(intruder, "query_schedules", {"intent": "查询", "schedule_id": schedule_id})
        with self.assertRaises(KeyError):
            tools.execute(intruder, "manage_schedule", {"intent": "暂停", "schedule_id": schedule_id, "action": "pause"})

        # 模型参数里的 username 不会被采信。
        spoofed = tools.execute(self.owner, "query_schedules", {"intent": "查询", "username": "bob"})
        self.assertEqual(len(spoofed["plans"]), 1)

    def test_query_projection_and_runs(self):
        created = self.submit()
        schedule_id = created["plan"]["definition"]["id"]
        tools.execute(self.owner, "manage_schedule", {"intent": "立即运行", "schedule_id": schedule_id, "action": "run", "request_id": "r1"})

        projected = tools.execute(
            self.owner,
            "query_schedules",
            {"intent": "投影", "schedule_id": schedule_id, "fields": ["definition", "next_at"]},
        )["plans"][0]
        self.assertEqual(set(projected), {"definition", "next_at"})

        with_runs = tools.execute(
            self.owner,
            "query_schedules",
            {"intent": "含运行", "schedule_id": schedule_id, "include_runs": True},
        )["plans"][0]
        self.assertEqual(len(with_runs["runs"]), 1)
        self.assertIn("definition", with_runs["runs"][0])
        self.assertNotIn("notification_token", with_runs["runs"][0])

        with self.assertRaises(ValueError):
            tools.execute(self.owner, "query_schedules", {"intent": "坏投影", "fields": ["definition", "secret"]})

        filtered = tools.execute(self.owner, "query_schedules", {"intent": "工作区筛选", "workspace_id": "other"})
        self.assertEqual(filtered["plans"], [])

    # ------------------------------------------------------------------
    # submit_schedule
    # ------------------------------------------------------------------
    def test_submit_reads_file_through_execution_backend(self):
        result = self.submit(fx.plan_definition(name="工具计划"))
        self.assertTrue(result["success"])
        self.assertEqual(result["plan"]["definition"]["name"], "工具计划")
        stored = self.store.get("alice", result["plan"]["definition"]["id"])
        self.assertEqual(stored["source"]["kind"], "agent")
        self.assertEqual(stored["source"]["conversation_id"], "conv-tools")
        self.assertEqual(stored["source"]["file_path"], "plan.yaml")
        self.assertEqual(stored["principal"]["username"], "alice")

    def test_submit_missing_or_invalid_file_does_not_persist(self):
        with self.assertRaises(ValueError):
            tools.execute(self.owner, "submit_schedule", {"intent": "缺失", "file_path": "missing.yaml"})
        self.owner.execution_backend.files.write_file("broken.yaml", "name: [")
        with self.assertRaises(ValueError):
            tools.execute(self.owner, "submit_schedule", {"intent": "坏 YAML", "file_path": "broken.yaml"})
        self.assertEqual(self.store.list("alice"), [])

    def test_submit_default_workspace_and_overwrite_semantics(self):
        # 目标工作区省略时，工具用 owner.workspace_id 作为默认值。
        definition = fx.plan_definition()
        definition["target"].pop("workspace_id")
        created = self.submit(definition)
        self.assertEqual(created["plan"]["definition"]["target"]["workspace_id"], "ws-1")

        first = self.submit(fx.plan_definition(name="v1"), filename="v1.yaml")
        plan = first["plan"]["definition"]
        updated = self.submit({**plan, "name": "v2"}, filename="v2.yaml", overwrite=True)
        self.assertEqual(updated["plan"]["definition"]["revision"], 2)

        with self.assertRaises(KeyError):
            # 覆盖不存在的 ID 在 store 查询阶段就抛 KeyError（“计划不存在”），不是静默新建。
            self.submit({**plan, "id": "schedule_absent", "revision": 1}, filename="absent.yaml", overwrite=True)

    def test_plan_and_readonly_cannot_submit_or_run(self):
        for owner in (TerminalOwner(work_mode="plan"), TerminalOwner(permission="readonly")):
            with self.assertRaises(PermissionError):
                self.submit(owner=owner)
            self.assertEqual(self.adapter.calls, [])
            self.assertEqual(self.store.list(owner.username), [])

        created = self.submit()
        schedule_id = created["plan"]["definition"]["id"]
        for owner in (TerminalOwner(work_mode="plan"), TerminalOwner(permission="readonly")):
            for action in ("run", "enable"):
                with self.assertRaises(PermissionError):
                    tools.execute(owner, "manage_schedule", {"intent": "运行", "schedule_id": schedule_id, "action": action})
        # readonly / plan 仍可只读查询。
        self.assertEqual(len(tools.execute(TerminalOwner(permission="readonly"), "query_schedules", {"intent": "查询"})["plans"]), 1)

    def test_readonly_blocks_mutating_lifecycle_actions(self):
        created = self.submit()
        schedule_id = created["plan"]["definition"]["id"]
        run = tools.execute(self.owner, "manage_schedule", {"intent": "运行", "schedule_id": schedule_id, "action": "run"})
        run_id = run["result"]["id"]

        readonly = TerminalOwner(permission="readonly")
        for args in (
            {"action": "pause"},
            {"action": "delete"},
            {"action": "stop", "run_id": run_id},
        ):
            with self.assertRaises(PermissionError):
                tools.execute(readonly, "manage_schedule", {"intent": "变更", "schedule_id": schedule_id, **args})

        paused = tools.execute(self.owner, "manage_schedule", {"intent": "暂停", "schedule_id": schedule_id, "action": "pause"})
        self.assertFalse(paused["result"]["definition"]["enabled"])
        enabled = tools.execute(self.owner, "manage_schedule", {"intent": "启用", "schedule_id": schedule_id, "action": "enable"})
        self.assertTrue(enabled["result"]["definition"]["enabled"])

    def test_manage_validation_and_run_control(self):
        created = self.submit()
        schedule_id = created["plan"]["definition"]["id"]

        with self.assertRaises(ValueError):
            tools.execute(self.owner, "manage_schedule", {"intent": "坏动作", "schedule_id": schedule_id, "action": "explode"})
        with self.assertRaises(KeyError):
            tools.execute(self.owner, "manage_schedule", {"intent": "不存在", "schedule_id": "schedule_missing", "action": "pause"})

        queued = tools.execute(
            self.owner,
            "manage_schedule",
            {"intent": "运行", "schedule_id": schedule_id, "action": "run", "request_id": "req-tools"},
        )["result"]
        stopped = tools.execute(
            self.owner,
            "manage_schedule",
            {"intent": "停止", "schedule_id": schedule_id, "action": "stop", "run_id": queued["id"]},
        )["result"]
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(stopped["reason"], "user_stopped")

        with self.assertRaises(ValueError):
            tools.execute(self.owner, "manage_schedule", {"intent": "缺少 run_id", "schedule_id": schedule_id, "action": "stop"})

        with self.assertRaises(ConflictError):
            tools.execute(
                self.owner,
                "manage_schedule",
                {"intent": "旧版本", "schedule_id": schedule_id, "action": "pause", "revision": 99},
            )

    # ------------------------------------------------------------------
    # _guard 权限边界（真实 getter + 模拟执行端）
    # ------------------------------------------------------------------
    def test_remote_execution_backend_is_rejected_before_validation(self):
        remote = mock.Mock(kind="remote")
        owner = TerminalOwner(backend=remote)
        with self.assertRaises(ValueError):
            self.guard(owner=owner)
        self.assertEqual(self.adapter.calls, [])
        # 远后端在 plan 模式下也先按远后端报错（校验顺序：后端 → 模式）。
        with self.assertRaises(ValueError):
            self.guard(owner=TerminalOwner(backend=mock.Mock(kind="remote"), work_mode="plan"))

    def test_plan_settings_cannot_exceed_current_terminal(self):
        cases = [
            ({"permission_mode": "unrestricted"}, TerminalOwner(permission="auto_approval"), PermissionError),
            ({"permission_mode": "approval"}, TerminalOwner(permission="readonly"), PermissionError),
            ({"execution_mode": "direct"}, TerminalOwner(execution="sandbox"), PermissionError),
            ({"network_permission": "full"}, TerminalOwner(network="restricted"), PermissionError),
            ({"network_permission": "restricted"}, TerminalOwner(network="none"), PermissionError),
        ]
        for settings, owner, error in cases:
            self.adapter.settings = {
                "permission_mode": "auto_approval",
                "execution_mode": "sandbox",
                "network_permission": "restricted",
                **settings,
            }
            with self.assertRaises(error, msg=str(settings)):
                self.guard(owner=owner)

    def test_plan_settings_equal_or_lower_are_allowed(self):
        cases = [
            ({"permission_mode": "approval"}, TerminalOwner(permission="approval")),
            ({"permission_mode": "auto_approval"}, TerminalOwner(permission="unrestricted")),
            ({"execution_mode": "direct"}, TerminalOwner(execution="direct")),
            ({"execution_mode": "sandbox"}, TerminalOwner(execution="direct")),
            ({"network_permission": "none"}, TerminalOwner(network="restricted")),
            ({"network_permission": "full"}, TerminalOwner(network="full")),
            # 终端未上报网络权限时按 restricted 处理：restricted 计划允许，full 拒绝。
            ({"network_permission": "restricted"}, TerminalOwner(network=None)),
        ]
        for settings, owner in cases:
            self.adapter.settings = {
                "permission_mode": "auto_approval",
                "execution_mode": "sandbox",
                "network_permission": "restricted",
                **settings,
            }
            validated = self.guard(owner=owner)
            # _guard 返回 adapter.validate 的结果（已校验的 principal），不是 definition。
            self.assertEqual(validated["username"], owner.username, msg=str(settings))
            self.assertEqual(self.adapter.calls[-1]["definition"]["name"], "巡检")

    def test_network_none_terminal_rejects_full_plan(self):
        self.adapter.settings = {"permission_mode": "auto_approval", "execution_mode": "sandbox", "network_permission": "full"}
        with self.assertRaises(PermissionError):
            self.guard(owner=TerminalOwner(network=None))

    def test_plan_mode_blocks_guard_before_adapter_validation(self):
        with self.assertRaises(PermissionError):
            self.guard(owner=TerminalOwner(work_mode="plan"))
        self.assertEqual(self.adapter.calls, [])

    def test_guard_rejects_direct_plan_for_sandbox_terminal_end_to_end(self):
        """submit_schedule 在 direct 计划 + sandbox 终端时必须拒绝且不落库（防止静默降级）。"""
        definition = fx.plan_definition()
        definition["configuration"] = {
            "mode": "custom",
            "work_mode": "execute",
            "execution_mode": "direct",
        }
        self.adapter.settings = {"permission_mode": "auto_approval", "execution_mode": "direct", "network_permission": "restricted"}
        text = dump_definition(definition)
        self.owner.execution_backend.files.write_file("direct.yaml", text)
        with self.assertRaises(PermissionError):
            tools.execute(self.owner, "submit_schedule", {"intent": "直接执行", "file_path": "direct.yaml"})
        self.assertEqual(self.store.list("alice"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
