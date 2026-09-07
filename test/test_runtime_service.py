"""阶段二公共任务入口（RuntimeService）的显式上下文验收测试。

契约用例（docs/runtime_contract.md §7）：
- T01：显式上下文受理（无 HTTP 请求、无 test_request_context）
- T02：同对话并发 chat 互斥 / notice 豁免
- T04：取消（受理层语义；执行线程以 no-op 替身阻断，不触达模型调用）
- 快照完整性：to_session_data 携带资源装配所需的全部身份与偏好字段

本测试全程不创建 Flask 应用/请求上下文——这本身就是
「公共入口不依赖隐式 Web 环境」的直接证明。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.runtime import (  # noqa: E402
    InternalDirectives,
    RuntimeContext,
    TaskParams,
    TrustedPrincipal,
    principal_from_session_snapshot,
    runtime_service,
)
from server.tasks import task_manager  # noqa: E402


def _make_ctx(conversation_id="conv_test_rt", task_type="chat", username="tester"):
    return RuntimeContext(
        principal=TrustedPrincipal(
            username=username,
            workspace_id="default",
            role="user",
            is_api_user=False,
            host_mode=True,
            host_workspace_id="default",
            preferred_model_key="kimi-test",
            preferred_run_mode="fast",
            preferred_thinking_mode=False,
        ),
        params=TaskParams(
            message="hello",
            conversation_id=conversation_id,
            run_mode="fast",
            task_type=task_type,
        ),
        directives=InternalDirectives(main_task_gate_token="tok123"),
    )


class RuntimeContextModelTest(unittest.TestCase):
    def test_validate_rejects_empty_username(self):
        ctx = _make_ctx()
        object.__setattr__(ctx.principal, "username", "")  # frozen dataclass 测试绕过
        with self.assertRaises(ValueError):
            ctx.validate()

    def test_to_session_data_carries_identity_and_preferences(self):
        ctx = _make_ctx()
        snap = ctx.to_session_data()
        # 身份与资源范围
        self.assertEqual(snap["username"], "tester")
        self.assertEqual(snap["workspace_id"], "default")
        self.assertEqual(snap["role"], "user")
        self.assertFalse(snap["is_api_user"])
        self.assertTrue(snap["host_mode"])
        self.assertEqual(snap["host_workspace_id"], "default")
        # 偏好快照层（非本次覆盖）
        self.assertEqual(snap["model_key"], "kimi-test")
        self.assertEqual(snap["run_mode"], "fast")
        self.assertFalse(snap["thinking_mode"])
        # 内部指令
        self.assertEqual(snap["main_task_gate_token"], "tok123")
        # 默认不出现事件回放键
        self.assertNotIn("auto_user_message_event", snap)

    def test_to_session_data_directives(self):
        ctx = RuntimeContext(
            principal=_make_ctx().principal,
            params=TaskParams(message="m", conversation_id="c1", approval_timeout_seconds=120),
            directives=InternalDirectives(
                auto_user_message_event=True,
                auto_user_message_payload={"visibility": "chat"},
                preceding_user_notices=[{"message": "n1", "payload": {}}],
            ),
        )
        snap = ctx.to_session_data()
        self.assertTrue(snap["auto_user_message_event"])
        self.assertEqual(snap["auto_user_message_payload"], {"visibility": "chat"})
        self.assertEqual(len(snap["preceding_user_notices"]), 1)
        # 超时透传机制（默认 None 时不出现，语义不变）
        self.assertEqual(snap["approval_timeout_seconds"], 120)

    def test_principal_from_session_snapshot(self):
        snap = {
            "username": "u1",
            "role": "api",
            "is_api_user": True,
            "host_mode": False,
            "workspace_id": "ws1",
            "model_key": "m1",
        }
        p = principal_from_session_snapshot(snap, "ws1")
        self.assertEqual(p.username, "u1")
        self.assertEqual(p.role, "api")
        self.assertTrue(p.is_api_user)
        self.assertFalse(p.host_mode)
        self.assertIsNone(p.host_workspace_id)
        self.assertEqual(p.preferred_model_key, "m1")


class RuntimeServiceAdmissionTest(unittest.TestCase):
    """受理层行为：用 no-op 替身阻断执行线程，不触达模型与工作区装配。"""

    def setUp(self):
        self._orig_run = task_manager._run_chat_task
        task_manager._run_chat_task = lambda *a, **k: None  # 线程即刻结束
        self._created = []

    def tearDown(self):
        task_manager._run_chat_task = self._orig_run
        for task_id in self._created:
            with task_manager._lock:
                task_manager._tasks.pop(task_id, None)

    def _create(self, **kw):
        rec = runtime_service.create_task(_make_ctx(**kw))
        self._created.append(rec.task_id)
        return rec

    def test_t01_create_task_without_http_context(self):
        rec = self._create()
        self.assertTrue(rec.task_id)
        self.assertEqual(rec.username, "tester")
        self.assertEqual(rec.conversation_id, "conv_test_rt")
        # 快照经显式上下文进入，未触碰 Flask session
        self.assertEqual(rec.session_data["username"], "tester")
        self.assertEqual(rec.session_data["main_task_gate_token"], "tok123")

    def test_t02_same_conversation_chat_mutex_and_notice_exempt(self):
        # 第一个任务保持 running（线程 no-op 但 status 已被置 running）
        rec1 = self._create()
        self.assertEqual(rec1.status, "running")
        # 同对话第二个 chat 被拒
        with self.assertRaises(RuntimeError):
            self._create()
        # notice 类型豁免互斥
        rec2 = self._create(task_type="notice")
        self.assertEqual(rec2.task_type, "notice")
        # 不同对话不受影响
        rec3 = self._create(conversation_id="conv_other")
        self.assertTrue(rec3.task_id)

    def test_create_task_validates_context(self):
        bad = _make_ctx()
        object.__setattr__(bad.principal, "workspace_id", "")
        with self.assertRaises(ValueError):
            runtime_service.create_task(bad)

    def test_create_chat_task_requires_explicit_session_data(self):
        # 直调底层入口且不带快照 → 明确拒绝（不再静默读 Flask session）
        with self.assertRaises(ValueError):
            task_manager.create_chat_task(
                "tester", "default", "msg", [], "conv_x",
            )

    def test_t04_cancel_task(self):
        rec = self._create()
        ok = runtime_service.cancel_task("tester", rec.task_id)
        self.assertTrue(ok)
        # 他人不可取消
        rec2 = self._create(conversation_id="conv_cancel2")
        self.assertFalse(runtime_service.cancel_task("someone_else", rec2.task_id))

    def test_get_task_events_offset_protocol(self):
        rec = self._create()
        task_manager._append_event(rec, "system_message", {"text": "a"})
        task_manager._append_event(rec, "text_chunk", {"text": "b"})
        events, next_offset, err = runtime_service.get_task_events("tester", rec.task_id, 0)
        self.assertIsNone(err)
        self.assertEqual([e["idx"] for e in events], [0, 1])
        self.assertEqual(next_offset, 2)
        # offset 续读
        events2, next2, _ = runtime_service.get_task_events("tester", rec.task_id, 1)
        self.assertEqual([e["idx"] for e in events2], [1])
        self.assertEqual(next2, 2)
        # 无权/不存在
        events3, _, err3 = runtime_service.get_task_events("someone_else", rec.task_id, 0)
        self.assertIsNone(events3)
        self.assertTrue(err3)


if __name__ == "__main__":
    unittest.main()
