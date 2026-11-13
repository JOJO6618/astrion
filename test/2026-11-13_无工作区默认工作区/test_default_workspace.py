"""零工作区修复回归：自动创建默认工作区、导航容错、无模型不报错。

运行：python3 -B test/2026-11-13_无工作区默认工作区/test_default_workspace.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import host_workspace_manager as hwm  # noqa: E402
from modules.desktop_navigation.service import DesktopNavigation  # noqa: E402
from server.context.new_conversation import _default_model_key  # noqa: E402


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class EnsureDefaultWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "host_workspaces.json"
        self.desktop = self.root / "Desktop"
        self.desktop.mkdir()
        patcher = mock.patch.object(hwm, "default_workspace_path",
                                    lambda: self.desktop / "Astrion" / "Default")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_default_workspace_once(self):
        created = hwm.ensure_default_host_workspace(self.config_path)
        self.assertIsNotNone(created)
        payload = _read(self.config_path)
        self.assertTrue(payload.get(hwm.AUTO_DEFAULT_WORKSPACE_MARKER) is True)
        self.assertEqual(len(payload["workspaces"]), 1)
        entry = payload["workspaces"][0]
        self.assertEqual(entry["label"], "默认工作区")
        self.assertEqual(entry["path"], str((self.desktop / "Astrion" / "Default").resolve()))
        self.assertTrue((self.desktop / "Astrion" / "Default").is_dir())
        self.assertEqual(payload["default_workspace_id"], entry["workspace_id"])
        # 幂等：已有工作区时不再动作
        self.assertIsNone(hwm.ensure_default_host_workspace(self.config_path))

    def test_does_not_rebuild_after_user_removes_all(self):
        hwm.ensure_default_host_workspace(self.config_path)
        payload = _read(self.config_path)
        payload["workspaces"] = []
        payload["default_workspace_id"] = ""
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(hwm.ensure_default_host_workspace(self.config_path))
        self.assertEqual(_read(self.config_path)["workspaces"], [])

    def test_skips_when_catalog_has_workspace(self):
        self.config_path.write_text(json.dumps({
            "default_workspace_id": "mine",
            "workspaces": [{"workspace_id": "mine", "label": "mine", "path": str(self.root)}],
        }, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(hwm.ensure_default_host_workspace(self.config_path))
        self.assertNotIn(hwm.AUTO_DEFAULT_WORKSPACE_MARKER, _read(self.config_path))

    def test_never_raises_on_broken_config(self):
        self.config_path.write_text("not json", encoding="utf-8")
        # 非 strict 读取会回退内存默认值，仍可尝试创建；关键是不抛异常
        hwm.ensure_default_host_workspace(self.config_path)


class ReconcileNoWorkspaceTest(unittest.TestCase):
    def test_keeps_tab_when_workspace_missing(self):
        state = {"tabs": [{
            "key": "k1", "kind": "conv", "conversation_kind": "agent",
            "conversation_id": "conv_1", "workspace_id": "agents",
            "workspace_label": "agents", "title": "t", "error": "",
            "running": False, "awaiting_human_approval": False,
        }]}
        DesktopNavigation._reconcile(
            state, lambda _ws, _conv: {"workspace_id": "", "exists": True, "no_workspace": True})
        self.assertEqual(len(state["tabs"]), 1)
        self.assertEqual(state["tabs"][0]["error"], "workspace_unavailable")


class DefaultModelKeyTest(unittest.TestCase):
    def test_empty_registry_returns_empty_string(self):
        with mock.patch("server.context.new_conversation.get_default_model_key",
                        side_effect=ValueError("尚未配置任何模型")):
            self.assertEqual(_default_model_key({}), "")

    def test_configured_default_wins(self):
        with mock.patch("server.context.new_conversation.get_default_model_key",
                        side_effect=AssertionError("should not be called")):
            self.assertEqual(_default_model_key({"default_model": "openai/gpt-5"}), "openai/gpt-5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
