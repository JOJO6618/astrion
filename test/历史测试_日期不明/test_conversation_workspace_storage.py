from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.conversation_manager import ConversationManager


def _write_conversation(path: Path, conv_id: str, project_path: str) -> None:
    payload = {
        "id": conv_id,
        "title": f"title {conv_id}",
        "created_at": "2026-05-26T10:00:00",
        "updated_at": "2026-05-26T10:00:00",
        "messages": [{"role": "user", "content": "hello"}],
        "todo_list": None,
        "metadata": {
            "project_path": project_path,
            "project_relative_path": None,
            "thinking_mode": False,
            "run_mode": "fast",
            "model_key": None,
            "has_images": False,
            "has_videos": False,
            "total_messages": 1,
            "total_tools": 0,
            "status": "active",
        },
        "token_statistics": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "current_context_tokens": 0,
            "updated_at": "2026-05-26T10:00:00",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class ConversationWorkspaceStorageTest(unittest.TestCase):
    def test_legacy_conversations_are_migrated_and_scoped_by_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            conversations_dir = data_dir / "conversations"
            conversations_dir.mkdir(parents=True)
            ws1_path = root / "workspace1"
            ws1_child = ws1_path / "child"
            ws2_path = root / "workspace2"
            other_path = root / "outside"
            ws1_child.mkdir(parents=True)
            ws2_path.mkdir(parents=True)
            other_path.mkdir(parents=True)

            host_workspaces = root / "host_workspaces.json"
            host_workspaces.write_text(
                json.dumps(
                    {
                        "workspaces": [
                            {"workspace_id": "workspace1", "label": "W1", "path": str(ws1_path)},
                            {"workspace_id": "workspace2", "label": "W2", "path": str(ws2_path)},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            _write_conversation(conversations_dir / "conv_ws1.json", "conv_ws1", str(ws1_child))
            _write_conversation(conversations_dir / "conv_ws2.json", "conv_ws2", str(ws2_path))
            _write_conversation(conversations_dir / "conv_unmatched.json", "conv_unmatched", str(other_path))

            with patch("utils.conversation_manager.HOST_WORKSPACES_FILE", str(host_workspaces)):
                manager_ws1 = ConversationManager(base_dir=str(data_dir), project_path=str(ws1_path))

                self.assertTrue((conversations_dir / "workspace1" / "conv_ws1.json").exists())
                self.assertTrue((conversations_dir / "workspace2" / "conv_ws2.json").exists())
                self.assertTrue((conversations_dir / "conv_unmatched.json").exists())

                listed_ids = {item["id"] for item in manager_ws1.get_conversation_list(limit=20)["conversations"]}
                self.assertEqual(listed_ids, {"conv_ws1"})
                self.assertIsNotNone(manager_ws1.load_conversation("conv_ws1"))
                self.assertIsNone(manager_ws1.load_conversation("conv_ws2"))
                self.assertIsNone(manager_ws1.load_conversation("conv_unmatched"))

                created_id = manager_ws1.create_conversation(project_path=str(ws1_path), initial_messages=[])
                self.assertTrue((conversations_dir / "workspace1" / f"{created_id}.json").exists())
                self.assertFalse((conversations_dir / f"{created_id}.json").exists())

    def test_search_accepts_three_keywords_and_excludes_current_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            manager = ConversationManager(base_dir=str(data_dir), project_path=str(workspace))

            empty_id = manager.create_conversation(project_path=str(workspace), initial_messages=[])
            time.sleep(0.002)
            current_id = manager.create_conversation(
                project_path=str(workspace),
                initial_messages=[{"role": "user", "content": "当前对话包含 alpha"}],
            )
            time.sleep(0.002)
            beta_id = manager.create_conversation(
                project_path=str(workspace),
                initial_messages=[{"role": "user", "content": "旧对话包含 beta"}],
            )
            time.sleep(0.002)
            gamma_id = manager.create_conversation(
                project_path=str(workspace),
                initial_messages=[{"role": "user", "content": "旧对话包含 gamma"}],
            )
            time.sleep(0.002)
            delta_id = manager.create_conversation(
                project_path=str(workspace),
                initial_messages=[{"role": "user", "content": "旧对话包含 delta"}],
            )

            results = manager.search_conversation_summaries(
                keywords=["alpha", "beta", "gamma", "delta"],
                exclude_conversation_id=current_id,
                limit=10,
            )
            result_ids = {item["id"] for item in results}

            self.assertNotIn(current_id, result_ids)
            self.assertIn(beta_id, result_ids)
            self.assertIn(gamma_id, result_ids)
            self.assertNotIn(delta_id, result_ids)
            self.assertNotIn(empty_id, result_ids)
            beta_result = next(item for item in results if item["id"] == beta_id)
            self.assertEqual(beta_result.get("total_messages"), 1)
            self.assertEqual(beta_result.get("total_tools"), 0)

            listed = manager.search_conversation_summaries(limit=10)
            listed_ids = {item["id"] for item in listed}
            self.assertNotIn(empty_id, listed_ids)
            self.assertIn(beta_id, listed_ids)

            recent = manager.get_recent_conversation_summaries(limit=10)
            recent_ids = {item["id"] for item in recent}
            self.assertNotIn(empty_id, recent_ids)


if __name__ == "__main__":
    unittest.main()
