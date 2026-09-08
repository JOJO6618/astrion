"""get_user_resources 显式身份（RuntimeIdentity）分支路由回归测试。

契约 docs/runtime_contract.md §7 T10：host / web / API 三种身份 × 资源解析，
是阶段二拆桥后的最高风险点（is_api_user/host_mode 分支选错会静默串工作区）。

本测试用重 mock 阻断容器/终端/磁盘副作用，聚焦验证：
- identity 模式下全程无 Flask 请求上下文不崩溃（不读 session）
- host_mode=True → 宿主机工作区解析路径
- is_api_user=True → api_user_manager（而非 user_manager）
- web 身份 → user_manager 与正确的工作区 id
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.context import RuntimeIdentity, get_user_resources  # noqa: E402


def _base_patches():
    """公共 mock：容器、terminal 缓存、skills 同步、个性化应用、配额。"""
    return [
        patch("server.context.resources.state.container_manager"),
        patch("server.context.resources._ensure_workspace_skills_synced"),
        patch("server.context.resources._apply_workspace_personalization_preferences"),
        patch("server.context.resources.get_or_create_usage_tracker", return_value=None),
        patch("server.context.resources.WebTerminal"),
    ]


class GetUserResourcesIdentityTest(unittest.TestCase):
    def _run_with_mocks(self, identity, username="tester", workspace_id="default", extra_patches=None):
        patches = _base_patches() + (extra_patches or [])
        mocks = []
        for p in patches:
            mocks.append(p.start())
        try:
            # 缓存命中路径：预置一个对话级 terminal，避免 WebTerminal 构造细节
            import server.context.resources as resources
            fake_terminal = MagicMock()
            fake_terminal._reaper_closing = False
            term_key = f"host::{workspace_id}::conv_t10" if identity.host_mode else f"{username}::{workspace_id}::conv_t10"
            with patch.dict(resources.state.user_terminals, {term_key: fake_terminal}, clear=False):
                terminal, workspace = get_user_resources(
                    username,
                    workspace_id=workspace_id,
                    update_session=False,
                    conversation_id="conv_t10",
                    identity=identity,
                )
            return terminal, workspace, mocks
        finally:
            for p in patches:
                p.stop()

    def test_host_mode_routes_to_host_workspace(self):
        """host 身份 → resolve_host_workspace 路径，且不需要请求上下文。"""
        host_ws = {"workspace_id": "default", "path": "/tmp/host_ws_t10", "label": "默认"}
        with patch("server.context.resources.resolve_host_workspace", return_value=(None, host_ws)):
            terminal, workspace, _ = self._run_with_mocks(
                RuntimeIdentity(host_mode=True, host_workspace_id="default", role="admin")
            )
        self.assertIsNotNone(terminal)
        # host 工作区 username 固定为 "host"，路径来自 resolve_host_workspace
        # （生产代码对路径做 resolve()，macOS 上 /tmp 会解析为 /private/tmp）
        self.assertEqual(getattr(workspace, "username", None), "host")
        self.assertEqual(
            str(getattr(workspace, "project_path", "")),
            str(Path("/tmp/host_ws_t10").resolve()),
        )

    def test_api_user_routes_to_api_manager(self):
        """API 身份 → api_user_manager.ensure_workspace，绝不走 user_manager。"""
        import server.context.resources as resources
        api_ws = MagicMock()
        api_ws.workspace_id = "ws_api"
        api_ws.project_path = Path("/tmp/api_ws")
        api_ws.data_dir = Path("/tmp/api_data")
        with patch.object(resources.state, "api_user_manager") as api_mgr, \
             patch.object(resources.state, "user_manager") as user_mgr:
            api_mgr.ensure_workspace.return_value = api_ws
            terminal, workspace, _ = self._run_with_mocks(
                RuntimeIdentity(is_api_user=True, role="api"),
                username="api_tester",
                workspace_id="ws_api",
            )
            api_mgr.ensure_workspace.assert_called_once_with("api_tester", "ws_api")
            user_mgr.ensure_user_workspace.assert_not_called()
        self.assertIsNotNone(terminal)

    def test_web_user_routes_to_user_manager(self):
        """普通网页身份 → user_manager.ensure_user_workspace。"""
        import server.context.resources as resources
        web_ws = MagicMock()
        web_ws.workspace_id = "default"
        web_ws.project_path = Path("/tmp/web_ws")
        web_ws.data_dir = Path("/tmp/web_data")
        with patch.object(resources.state, "user_manager") as user_mgr, \
             patch.object(resources.state, "api_user_manager") as api_mgr:
            user_mgr.ensure_user_workspace.return_value = web_ws
            user_mgr.get_user.return_value = None
            user_mgr.list_user_workspaces.return_value = {}
            terminal, workspace, _ = self._run_with_mocks(
                RuntimeIdentity(is_api_user=False, role="user"),
                username="web_tester",
            )
            user_mgr.ensure_user_workspace.assert_called_once_with("web_tester", "default")
            api_mgr.ensure_workspace.assert_not_called()
        self.assertIsNotNone(terminal)

    def test_identity_mode_never_touches_flask_session(self):
        """identity 模式在无请求上下文下运行全程不抛（不读 session 的直接证明）。"""
        import server.context.resources as resources
        web_ws = MagicMock()
        web_ws.workspace_id = "default"
        web_ws.project_path = Path("/tmp/web_ws2")
        web_ws.data_dir = Path("/tmp/web_data2")
        with patch.object(resources.state, "user_manager") as user_mgr:
            user_mgr.ensure_user_workspace.return_value = web_ws
            user_mgr.get_user.return_value = None
            user_mgr.list_user_workspaces.return_value = {}
            # 无 flask app/request context：若有任何 session 读取会抛 RuntimeError
            terminal, _ws, _mocks = self._run_with_mocks(RuntimeIdentity(role="user"), username="clean_tester")
        self.assertIsNotNone(terminal)


class GetUserResourcesHostPolicyTest(unittest.TestCase):
    """回归：host 模式 + 非显式身份 + host 用户记录存在时，管理员策略必须应用。

    历史 bug（拆包遗漏导入）：resources.py 调用未导入的 get_current_user_role，
    record 存在时触发 NameError，被 except 吞掉后工具分类/禁用模型策略静默不应用。
    """

    def test_host_record_applies_admin_policy(self):
        import server.context.resources as resources

        host_ws = {"workspace_id": "default", "path": "/tmp/host_ws_policy", "label": "默认"}
        record = MagicMock()
        record.username = "host"
        record.invite_code = None
        policy = {
            "categories": {},
            "forced_category_states": {},
            "disabled_models": [],
            "ui_blocks": {},
            "updated_at": "v1",
        }
        base = _base_patches()
        for p in base:
            p.start()
        try:
            with patch("server.context.resources.TERMINAL_SANDBOX_MODE", "host"), \
                 patch("server.context.resources.resolve_host_workspace", return_value=(None, host_ws)), \
                 patch("server.context.resources._get_current_user_record", return_value=record), \
                 patch("server.context.resources._get_current_user_role", return_value="admin") as get_role, \
                 patch("modules.admin_policy_manager.get_effective_policy", return_value=policy) as get_policy:
                from flask import Flask
                app = Flask("host_policy_test")
                app.secret_key = "test"
                fake_terminal = MagicMock()
                fake_terminal._reaper_closing = False
                fake_terminal.model_key = "kimi-k2"
                # 缓存命中路径会校验 project_path 是否匹配目标工作区路径，
                # 不匹配会原地重建 terminal；设为 resolve 后的真实路径避免重建
                resolved_path = str(Path("/tmp/host_ws_policy").resolve())
                fake_terminal.project_path = resolved_path
                fake_terminal.context_manager.project_path = resolved_path
                ctx = app.test_request_context("/")
                ctx.push()
                try:
                    # host 分支入口要求 session["host_mode"] 为真（非显式路径）
                    from flask import session as flask_session
                    flask_session["host_mode"] = True
                    # 预置对话级 terminal 缓存命中，避免 WebTerminal 构造细节
                    with patch.dict(resources.state.user_terminals, {"host::default::conv_policy": fake_terminal}, clear=False):
                        terminal, _workspace = get_user_resources(
                            "host",
                            workspace_id="default",
                            update_session=False,
                            conversation_id="conv_policy",
                            identity=None,  # 非显式：HTTP 适配层路径（bug 触发路径）
                        )
                finally:
                    ctx.pop()
            # 修复后直接证据：role 解析与策略应用都被真正执行
            get_role.assert_called_once_with(record)
            get_policy.assert_called_once()
            fake_terminal.set_admin_policy.assert_called_once()
            self.assertIsNotNone(terminal)
        finally:
            for p in base:
                p.stop()


if __name__ == "__main__":
    unittest.main()
