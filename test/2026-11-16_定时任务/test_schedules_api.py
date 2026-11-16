"""server/schedules.py Flask test_client API 回归。

环境：
- 在导入 config/server 前把 ASTRION_IGNORE_DOTENV=1，并把数据目录指向临时目录
  （若其它测试模块已先导入 config，则沿用其已指向的临时目录，不覆盖）。
- Flask app 使用本文件自建的最小 app（注册真实 schedules_bp + 真实
  attach_security_hooks），因此认证、CSRF、JSON 序列化与路由参数都走真实中间件；
  只有 SchedulerService 的 adapter 与 get_service 单例注入为替身。
- 没有真实 LLM 调用；计划用 SQLite 全部落在 tempfile 临时目录。

已知边界（本文件按实际中间件行为锁定，而不是按理想顺序）：
- CSRF 检查在 before_request 中先于视图认证执行：未登录的 POST /api/schedules
  若没有有效 CSRF 头会先得到 403（CSRF validation failed），而不是 401；
  只有携带有效 CSRF（会话内 token）但未登录时才到 401。
- host Bearer 通道是唯一免 CSRF 的 API 认证方式（host 模式 + 回环地址 + token）。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import schedule_fixtures as fx

_HERE = Path(__file__).resolve().parent
for _entry in (str(_HERE.parents[1]), str(_HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

# 独立运行时（未先被其它模块导入 config）也要隔离；discovery 顺序下沿用已设置的临时根。
if not os.environ.get("DATA_DIR"):
    _TMP = Path(tempfile.mkdtemp(prefix="astrion-schedule-api-"))
    for _sub in ("data", "logs", "users", "api_users", "config", "project"):
        (_TMP / _sub).mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "ASTRION_IGNORE_DOTENV": "1",
            "ASTRION_DATA_ROOT": str(_TMP),
            "DATA_DIR": str(_TMP / "data"),
            "LOGS_DIR": str(_TMP / "logs"),
            "USER_SPACE_DIR": str(_TMP / "users"),
            "API_USER_SPACE_DIR": str(_TMP / "api_users"),
            "DEPLOY_CONFIG_DIR": str(_TMP / "config"),
        }
    )

from flask import Flask  # noqa: E402

import server.schedules as schedules_api  # noqa: E402
from modules.scheduled_tasks.schema import dump_definition, parse_definition  # noqa: E402
from modules.scheduled_tasks.service import SchedulerService  # noqa: E402
from modules.scheduled_tasks.store import ConflictError, ScheduleStore  # noqa: E402
from server import state  # noqa: E402
from server.security import attach_security_hooks  # noqa: E402

CSRF = "csrf-token-for-tests"


def build_app() -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="schedule-api-tests")
    app.register_blueprint(schedules_api.schedules_bp)
    attach_security_hooks(app)
    return app


APP = build_app()


class ApiAdapter:
    """SchedulerService 的执行端替身：只记录调用，不触碰真实 runtime/terminal。"""

    def __init__(self):
        self.validated = []
        self.started = []
        self.observed = {}
        self.notified = []
        self.notify_status = "available"

    def validate(self, definition, principal):
        username = principal.get("username") or principal.get("user")
        if not username:
            raise PermissionError("缺乏身份")
        self.validated.append({"definition": definition, "principal": dict(principal)})
        return {
            "username": username,
            "role": principal.get("role") or "user",
            "configuration_snapshot": dict(definition.get("configuration") or {}),
        }

    def start(self, run):
        self.started.append(run["id"])
        return {"task_id": f"task-{run['id']}", "conversation_id": "conv-api"}

    def observe(self, run):
        # 契约：runtime adapter 返回的终态字面量是 "success"（runtime.py 把 run 记录的
        # succeeded 映射为 success），不是 "succeeded"。
        return self.observed.get(run["id"], {"state": "success", "summary": "ok"})

    def cancel(self, run):
        return None

    def notify(self, run):
        self.notified.append(run["id"])
        return self.notify_status


class ScheduleApiCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = ScheduleStore(Path(self._tmp.name) / "schedules.sqlite3")
        self.adapter = ApiAdapter()
        self.service = SchedulerService(self.store, self.adapter)
        patcher = mock.patch.object(schedules_api, "get_service", return_value=self.service)
        self.get_service = patcher.start()
        self.addCleanup(patcher.stop)
        browser = mock.patch("server.host_password.browser_session_valid", return_value=True)
        browser.start()
        self.addCleanup(browser.stop)
        self.client = APP.test_client()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def login(self, username="alice", role="user", *, host=False, host_workspace="default"):
        nonce = uuid.uuid4().hex
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["username"] = username
            sess["role"] = role
            sess["login_nonce"] = nonce
            sess["_csrf_token"] = CSRF
            sess["host_mode"] = bool(host)
            if host:
                sess["host_workspace_id"] = host_workspace
                sess["workspace_id"] = host_workspace
        state.active_login_nonces.setdefault(username, set()).add(nonce)
        return nonce

    def post(self, url, body=None, *, csrf=True, headers=None):
        merged = {"Content-Type": "application/json"}
        if csrf:
            merged["X-CSRF-Token"] = CSRF
        merged.update(headers or {})
        data = None if body is None else json.dumps(body)
        return self.client.post(url, data=data, headers=merged)

    def create_plan(self, definition=None, username="alice", *, csrf=True):
        self.login(username)
        response = self.post(
            "/api/schedules",
            {"definition": definition or fx.plan_definition(), "workspace_id": "ws-1"},
            csrf=csrf,
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]

    def data(self, response):
        payload = response.get_json()
        self.assertIsInstance(payload, dict)
        return payload

    # ------------------------------------------------------------------
    # 认证 / CSRF / host Bearer
    # ------------------------------------------------------------------
    def test_unauthenticated_requests_are_rejected(self):
        response = self.client.get("/api/schedules")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.data(response)["success"])

        detail = self.client.get("/api/schedules/schedule_x")
        self.assertEqual(detail.status_code, 401)

        page = self.client.get("/schedules")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/login", page.headers["Location"])

    def test_csrf_runs_before_session_auth(self):
        # 无会话、无 CSRF：中间件先拒绝，不会泄露“是否已登录”的差异。
        response = self.post("/api/schedules", {"definition": fx.plan_definition()}, csrf=False)
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", self.data(response)["error"])

        # 有会话 CSRF 但未登录：才轮到认证返回 401。
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        response = self.post("/api/schedules", {"definition": fx.plan_definition()})
        self.assertEqual(response.status_code, 401)

        # 登录后缺少 CSRF 仍被拒；带上正确 token 才通过。
        self.login()
        self.assertEqual(self.post("/api/schedules", {"definition": fx.plan_definition()}, csrf=False).status_code, 403)
        self.assertEqual(
            self.post("/api/schedules", {"definition": fx.plan_definition()}, headers={"X-CSRF-Token": "wrong"}).status_code,
            403,
        )
        self.assertEqual(self.post("/api/schedules/validate", {"definition": fx.plan_definition()}).status_code, 200)

    def test_host_bearer_channel_bypasses_csrf_and_does_not_leak_session(self):
        # 先验证“已登录会话 + 无效 Bearer”不会被回退成 Cookie 身份。
        session_client = APP.test_client()
        self.client = session_client
        self.login("alice")
        with mock.patch.multiple(
            "server.gateway_auth",
            TERMINAL_SANDBOX_MODE="host",
            LINUX_SAFETY=False,
            get_or_create_host_api_token=lambda: "host-secret",
        ):
            bad = session_client.get("/api/schedules", headers={"Authorization": "Bearer wrong"})
            self.assertEqual(bad.status_code, 401)
            self.assertFalse(self.data(bad)["success"])

        # 全新的 client 走 Bearer 通道：免 CSRF、不落 Cookie、身份为 host。
        bearer_client = APP.test_client()
        with mock.patch.multiple(
            "server.gateway_auth",
            TERMINAL_SANDBOX_MODE="host",
            LINUX_SAFETY=False,
            get_or_create_host_api_token=lambda: "host-secret",
        ):
            ok = bearer_client.get("/api/schedules", headers={"Authorization": "Bearer host-secret"})
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(self.data(ok)["success"])
            self.assertIsNone(ok.headers.get("Set-Cookie"))

            submitted = bearer_client.post(
                "/api/schedules",
                data=json.dumps({"definition": fx.plan_definition(), "workspace_id": "ws-1"}),
                headers={"Authorization": "Bearer host-secret", "Content-Type": "application/json"},
            )
            self.assertEqual(submitted.status_code, 200, submitted.get_data(as_text=True))
            self.assertEqual(self.store.get("host", self.data(submitted)["data"]["definition"]["id"])["username"], "host")

        # Bearer 身份不落 Cookie：同一 client 撤掉 Authorization 后仍是未登录。
        anonymous = bearer_client.get("/api/schedules")
        self.assertEqual(anonymous.status_code, 401)

    def test_host_bearer_disabled_outside_host_mode(self):
        client = APP.test_client()
        with mock.patch.multiple(
            "server.gateway_auth",
            TERMINAL_SANDBOX_MODE="sandbox",
            LINUX_SAFETY=False,
            get_or_create_host_api_token=lambda: "host-secret",
        ):
            response = client.get("/api/schedules", headers={"Authorization": "Bearer host-secret"})
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # options 只读，不创建终端 / 不调用 service
    # ------------------------------------------------------------------
    def test_options_is_read_only_and_never_touches_service_or_terminal(self):
        self.login("alice")
        with mock.patch("modules.execution_plane.get_execution_backend") as backend:
            response = self.client.get("/api/schedules/options")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = self.data(response)["data"]
        self.assertEqual(set(data), {"workspaces", "models", "timezones", "host_admin"})
        self.assertFalse(data["host_admin"])
        self.assertTrue(any(item["id"] == "default" for item in data["workspaces"]))
        self.assertIsInstance(data["models"], list)
        self.assertIn("UTC", data["timezones"])
        backend.assert_not_called()
        self.get_service.assert_not_called()
        self.assertEqual(self.store.list("alice"), [])

    def test_options_host_admin_reports_catalog_workspaces(self):
        self.login("host", "admin", host=True)
        catalog = {"workspaces": [{"workspace_id": "adopted", "path": self._tmp.name, "label": "Adopted"}]}
        with mock.patch("modules.host_workspace_manager.load_host_workspace_catalog", return_value=catalog):
            response = self.client.get("/api/schedules/options")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = self.data(response)["data"]
        self.assertTrue(data["host_admin"])
        self.assertEqual([item["id"] for item in data["workspaces"]], ["adopted"])
        self.assertEqual(data["workspaces"][0]["label"], "Adopted")

    # ------------------------------------------------------------------
    # submit / validate
    # ------------------------------------------------------------------
    def test_submit_definition_returns_serialized_plan_and_persists_owner(self):
        self.login("alice")
        response = self.post(
            "/api/schedules",
            {"definition": fx.plan_definition(name="库存巡检"), "workspace_id": "ws-1"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = self.data(response)["data"]
        self.assertEqual(data["definition"]["name"], "库存巡检")
        self.assertEqual(data["definition"]["revision"], 1)
        self.assertIsNotNone(data["next_at"])
        self.assertIsInstance(data["next_dates"], list)
        stored = self.store.get("alice", data["definition"]["id"])
        self.assertEqual(stored["username"], "alice")
        self.assertEqual(stored["source"], {"kind": "page"})
        self.assertEqual(stored["principal"]["username"], "alice")

    def test_submit_yaml_and_validate_do_not_persist(self):
        self.login("alice")
        yaml_text = dump_definition(fx.plan_definition(name="YAML 计划"))
        validated = self.post(
            "/api/schedules/validate",
            {"yaml": yaml_text, "workspace_id": "ws-1"},
        )
        self.assertEqual(validated.status_code, 200, validated.get_data(as_text=True))
        body = self.data(validated)["data"]
        self.assertEqual(body["definition"]["name"], "YAML 计划")
        self.assertTrue(body["next_dates"])
        self.assertEqual(self.store.list("alice"), [])

        submitted = self.post("/api/schedules", {"yaml": yaml_text, "workspace_id": "ws-1"})
        self.assertEqual(submitted.status_code, 200, submitted.get_data(as_text=True))
        self.assertEqual(self.data(submitted)["data"]["definition"]["name"], "YAML 计划")

    def test_submit_validation_errors(self):
        self.login("alice")
        cases = [
            ({"definition": fx.plan_definition(), "username": "bob"}, "未知请求字段"),
            ({"definition": fx.plan_definition(), "overwrite": "yes"}, "overwrite 必须是布尔值"),
            ({"yaml": "not: [valid", "workspace_id": "ws-1"}, None),
            ({}, None),
        ]
        for body, message in cases:
            response = self.post("/api/schedules", body)
            self.assertEqual(response.status_code, 400, (body, response.get_data(as_text=True)))
            if message:
                self.assertIn(message, self.data(response)["error"])

        not_object = self.client.post(
            "/api/schedules", data="[]", headers={"Content-Type": "application/json", "X-CSRF-Token": CSRF}
        )
        self.assertEqual(not_object.status_code, 400)
        self.assertIn("JSON 对象", self.data(not_object)["error"])

        oversized = self.client.post(
            "/api/schedules",
            data="x" * 300001,
            headers={"Content-Type": "application/json", "X-CSRF-Token": CSRF},
        )
        self.assertEqual(oversized.status_code, 400)
        self.assertIn("过大", self.data(oversized)["error"])

    def test_submit_overwrite_requires_matching_revision(self):
        self.login("alice")
        first = fx.plan_definition(name="v1")
        created = self.post("/api/schedules", {"definition": first, "workspace_id": "ws-1"})
        self.assertEqual(created.status_code, 200)
        plan = self.data(created)["data"]["definition"]

        current = {**plan, "name": "v2"}
        updated = self.post("/api/schedules", {"definition": current, "workspace_id": "ws-1", "overwrite": True})
        self.assertEqual(updated.status_code, 200, updated.get_data(as_text=True))
        self.assertEqual(self.data(updated)["data"]["definition"]["revision"], 2)

        stale = self.post("/api/schedules", {"definition": plan, "workspace_id": "ws-1", "overwrite": True})
        self.assertEqual(stale.status_code, 409)
        self.assertIn("版本", self.data(stale)["error"])

        duplicate = self.post("/api/schedules", {"definition": plan, "workspace_id": "ws-1"})
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("overwrite", self.data(duplicate)["error"])

    def test_validate_reports_invalid_definition(self):
        self.login("alice")
        response = self.post(
            "/api/schedules/validate",
            {"definition": {**fx.plan_definition(), "enabled": "yes"}, "workspace_id": "ws-1"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.data(response)["success"])

    # ------------------------------------------------------------------
    # list / detail / yaml / runs / actions
    # ------------------------------------------------------------------
    def test_list_filters_by_workspace_and_enabled(self):
        self.login("alice")
        first = self.post("/api/schedules", {"definition": fx.plan_definition(name="A"), "workspace_id": "ws-1"})
        self.post("/api/schedules", {"definition": fx.plan_definition(name="B", workspace="ws-2"), "workspace_id": "ws-2"})
        self.assertEqual(first.status_code, 200)
        all_plans = self.data(self.client.get("/api/schedules"))["data"]["plans"]
        self.assertEqual({plan["definition"]["name"] for plan in all_plans}, {"A", "B"})

        scoped = self.data(self.client.get("/api/schedules?workspace_id=ws-2"))["data"]["plans"]
        self.assertEqual([plan["definition"]["name"] for plan in scoped], ["B"])

        paused = fx.plan_definition(name="C", enabled=False)
        self.post("/api/schedules", {"definition": paused, "workspace_id": "ws-1"})
        disabled = self.data(self.client.get("/api/schedules?enabled=false"))["data"]["plans"]
        self.assertEqual([plan["definition"]["name"] for plan in disabled], ["C"])
        enabled = self.data(self.client.get("/api/schedules?enabled=true"))["data"]["plans"]
        self.assertEqual({plan["definition"]["name"] for plan in enabled}, {"A", "B"})

    def test_detail_yaml_and_runs(self):
        plan = self.create_plan(fx.plan_definition(name="详情"))
        schedule_id = plan["definition"]["id"]

        detail = self.client.get(f"/api/schedules/{schedule_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(self.data(detail)["data"]["definition"]["name"], "详情")

        exported = self.client.get(f"/api/schedules/{schedule_id}/yaml")
        self.assertEqual(exported.status_code, 200)
        parsed = parse_definition(self.data(exported)["data"]["yaml"])
        self.assertEqual(parsed["name"], "详情")

        run = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "run", "request_id": "req-1"})
        self.assertEqual(run.status_code, 200, run.get_data(as_text=True))
        run_id = self.data(run)["data"]["id"]

        runs = self.client.get(f"/api/schedules/{schedule_id}/runs")
        self.assertEqual(runs.status_code, 200)
        items = self.data(runs)["data"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], run_id)
        self.assertIn("definition", items[0])
        self.assertNotIn("notification_token", items[0])

        missing = self.client.get("/api/schedules/schedule_missing")
        self.assertEqual(missing.status_code, 404)

    def test_actions_enable_pause_delete_run_stop(self):
        plan = self.create_plan()
        schedule_id = plan["definition"]["id"]
        revision = plan["definition"]["revision"]

        paused = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "pause", "revision": revision})
        self.assertEqual(paused.status_code, 200, paused.get_data(as_text=True))
        self.assertFalse(self.data(paused)["data"]["definition"]["enabled"])

        enabled = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "enable"})
        self.assertEqual(enabled.status_code, 200)
        self.assertTrue(self.data(enabled)["data"]["definition"]["enabled"])

        stale = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "pause", "revision": revision})
        self.assertEqual(stale.status_code, 409)

        queued = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "run", "request_id": "stop-me"})
        run_id = self.data(queued)["data"]["id"]
        stopped = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "stop", "run_id": run_id})
        self.assertEqual(stopped.status_code, 200, stopped.get_data(as_text=True))
        self.assertEqual(self.data(stopped)["data"]["state"], "stopped")
        self.assertEqual(self.data(stopped)["data"]["reason"], "user_stopped")

        # 已停止的运行不能再次停止，也不会误伤其它计划。
        again = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "stop", "run_id": run_id})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.data(again)["data"]["state"], "stopped")

        no_run = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "stop"})
        self.assertEqual(no_run.status_code, 400)
        self.assertIn("run_id", self.data(no_run)["error"])

        unknown = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "explode"})
        self.assertEqual(unknown.status_code, 400)

        extra = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "pause", "who": "bob"})
        self.assertEqual(extra.status_code, 400)
        self.assertIn("未知操作字段", self.data(extra)["error"])

        deleted = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "delete"})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(self.data(deleted)["data"]["deleted"])
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}").status_code, 404)
        # 软删除后运行历史仍可查询（store.get deleted=True）。
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}/runs").status_code, 200)

    def test_actions_on_missing_plan_return_404(self):
        self.login("alice")
        for action in ("enable", "pause", "delete", "run"):
            response = self.post("/api/schedules/schedule_missing/actions", {"action": action})
            self.assertEqual(response.status_code, 404, (action, response.get_data(as_text=True)))

    # ------------------------------------------------------------------
    # 用户隔离
    # ------------------------------------------------------------------
    def test_users_cannot_see_or_touch_each_others_plans(self):
        plan = self.create_plan(fx.plan_definition(name="Alice 私有"), username="alice")
        schedule_id = plan["definition"]["id"]
        run = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "run", "request_id": "alice-run"})
        self.assertEqual(run.status_code, 200)
        run_id = self.data(run)["data"]["id"]

        self.login("bob")
        self.assertEqual(self.data(self.client.get("/api/schedules"))["data"]["plans"], [])
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}/yaml").status_code, 404)
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}/runs").status_code, 404)
        self.assertEqual(self.post(f"/api/schedules/{schedule_id}/actions", {"action": "pause"}).status_code, 404)
        self.assertEqual(self.data(self.post("/api/schedules/notifications/claim", {}))["data"], [])
        self.assertFalse(
            self.data(self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": "whatever"}))["data"]["acknowledged"]
        )

        # Bob 自己创建同名 ID 也不会覆盖 Alice 的计划。
        stolen = fx.plan_definition(name="Bob 副本")
        stolen["id"] = schedule_id
        stolen["revision"] = 1
        self.login("bob")
        bob_created = self.post("/api/schedules", {"definition": stolen, "workspace_id": "ws-1"})
        self.assertEqual(bob_created.status_code, 200)
        self.login("alice")
        self.assertEqual(self.client.get(f"/api/schedules/{schedule_id}").status_code, 200)
        self.assertEqual(self.data(self.client.get(f"/api/schedules/{schedule_id}"))["data"]["definition"]["name"], "Alice 私有")

    # ------------------------------------------------------------------
    # 通知 claim / ack
    # ------------------------------------------------------------------
    def _make_completed_notification_run(self, username="alice"):
        definition = fx.plan_definition(name="通知计划")
        definition["notifications"] = {"enabled": True}
        self.login(username)
        created = self.post("/api/schedules", {"definition": definition, "workspace_id": "ws-1"})
        self.assertEqual(created.status_code, 200)
        schedule_id = self.data(created)["data"]["definition"]["id"]
        queued = self.post(f"/api/schedules/{schedule_id}/actions", {"action": "run", "request_id": "notify-1"})
        self.assertEqual(queued.status_code, 200)
        run_id = self.data(queued)["data"]["id"]
        self.service.tick()
        self.assertEqual(self.adapter.started, [run_id])
        self.service.tick()
        return schedule_id, run_id

    def test_notifications_claim_and_ack_token_scope(self):
        _, run_id = self._make_completed_notification_run()

        claim = self.post("/api/schedules/notifications/claim", {})
        self.assertEqual(claim.status_code, 200, claim.get_data(as_text=True))
        items = self.data(claim)["data"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], run_id)
        self.assertEqual(item["name"], "通知计划")
        self.assertEqual(item["workspace_id"], "ws-1")
        token = item["token"]
        self.assertTrue(token)

        # 同一 run 已被认领并进入 delivering，重复 claim 不能重复发放 token。
        self.assertEqual(self.data(self.post("/api/schedules/notifications/claim", {}))["data"], [])

        self.assertEqual(
            self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": "wrong"}).status_code, 200
        )
        self.assertFalse(
            self.data(self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": "wrong"}))["data"]["acknowledged"]
        )
        ack = self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": token})
        self.assertEqual(ack.status_code, 200, ack.get_data(as_text=True))
        self.assertTrue(self.data(ack)["data"]["acknowledged"])
        # token 已消费。
        self.assertFalse(self.data(self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": token}))["data"]["acknowledged"])

        self.login("bob")
        self.assertFalse(self.data(self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": token}))["data"]["acknowledged"])

    def test_notifications_ack_validation_and_error_recording(self):
        _, run_id = self._make_completed_notification_run()
        token = self.data(self.post("/api/schedules/notifications/claim", {}))["data"][0]["token"]

        missing = self.post(f"/api/schedules/notifications/{run_id}/ack", {})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("通知凭据", self.data(missing)["error"])
        not_text = self.post(f"/api/schedules/notifications/{run_id}/ack", {"token": token, "error": 42})
        self.assertEqual(not_text.status_code, 400)
        self.assertIn("文字", self.data(not_text)["error"])

        failed = self.post(
            f"/api/schedules/notifications/{run_id}/ack",
            {"token": token, "error": "x" * 1200},
        )
        self.assertEqual(failed.status_code, 200)
        self.assertTrue(self.data(failed)["data"]["acknowledged"])
        self.assertTrue(self.store.run(run_id, "alice")["notification_error"].startswith("xxx"))

    def test_notification_routes_require_post(self):
        self.login("alice")
        self.assertEqual(self.client.get("/api/schedules/notifications/claim").status_code, 405)
        self.assertEqual(self.client.get("/api/schedules/notifications/run_x/ack").status_code, 405)


if __name__ == "__main__":
    unittest.main(verbosity=2)
