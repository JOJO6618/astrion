import json
import tempfile
import unittest
from pathlib import Path

from modules.host_workspace_manager import (
    create_host_workspace,
    load_host_workspace_catalog,
    resolve_host_workspace,
)


class HostWorkspaceManagerTest(unittest.TestCase):
    def test_create_default_when_config_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "host_workspaces.json"
            self.assertFalse(cfg.exists())
            catalog = load_host_workspace_catalog(config_path=cfg)
            self.assertTrue(cfg.exists())
            self.assertTrue(catalog["workspaces"])
            self.assertEqual(catalog["default_workspace_id"], catalog["workspaces"][0]["workspace_id"])

    def test_resolve_selected_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_a = Path(tmpdir) / "a"
            ws_b = Path(tmpdir) / "b"
            payload = {
                "default_workspace_id": "a",
                "workspaces": [
                    {"workspace_id": "a", "label": "A", "path": str(ws_a)},
                    {"workspace_id": "b", "label": "B", "path": str(ws_b)},
                ],
            }
            cfg = Path(tmpdir) / "host_workspaces.json"
            cfg.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            _, current = resolve_host_workspace("b", config_path=cfg)
            self.assertEqual(current["workspace_id"], "b")

            _, current_fallback = resolve_host_workspace("not-exist", config_path=cfg)
            self.assertEqual(current_fallback["workspace_id"], "a")

    def test_create_workspace_persisted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "host_workspaces.json"
            cfg.write_text(
                json.dumps(
                    {
                        "default_workspace_id": "default",
                        "workspaces": [
                            {"workspace_id": "default", "label": "默认", "path": str(Path(tmpdir) / "project")}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = create_host_workspace(str(Path(tmpdir) / "repo-a"), label="Repo A", config_path=cfg)
            self.assertTrue(result["created"])
            self.assertEqual(result["workspace"]["label"], "Repo A")

            catalog = load_host_workspace_catalog(config_path=cfg)
            labels = [item["label"] for item in catalog["workspaces"]]
            self.assertIn("Repo A", labels)


if __name__ == "__main__":
    unittest.main()
