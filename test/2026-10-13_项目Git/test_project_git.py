"""Offline checks with temporary repositories; never reads real credentials/data."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.project_git.discovery import discover, resolve_repository
from modules.project_git.history import branches, changed_files, comparison, file_diff, history
from modules.project_git.reviews import read_reviews, record_review, review_prompt
from modules.project_git.runner import GitQueryError, commit_sha


def command(repo, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env, stderr=subprocess.DEVNULL).decode().strip()


class GitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "project"
        self.repo.mkdir()
        command(self.repo, "init", "-b", "main")
        (self.repo / "中文 文件.txt").write_text("one\ntwo\nthree\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "初始提交")
        self.initial = command(self.repo, "rev-parse", "HEAD")
        command(self.repo, "mv", "中文 文件.txt", "重命名 文件.txt")
        (self.repo / "重命名 文件.txt").write_text("one\nchanged\nthree\n")
        (self.repo / "image.bin").write_bytes(b"\x00\x01\x02")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "rename and binary")
        self.head = command(self.repo, "rev-parse", "HEAD")
        self.workspaces = [{"workspace_id": "fixture", "label": "Fixture", "path": str(self.repo)}]

    def tearDown(self):
        self.temp.cleanup()

    def test_history_pagination_search_and_utf8(self):
        rows = history(self.repo, limit=1)
        self.assertTrue(rows["has_more"])
        self.assertEqual(rows["commits"][0]["sha"], self.head)
        self.assertEqual(history(self.repo, offset=1)["commits"][0]["subject"], "初始提交")
        self.assertEqual(len(history(self.repo, search="rename")["commits"]), 1)

    def test_root_commit_binary_rename_and_historical_contents(self):
        initial = comparison(self.repo, sha=self.initial)
        self.assertEqual(changed_files(self.repo, initial)[0]["status"], "A")
        compare = comparison(self.repo, sha=self.head)
        files = changed_files(self.repo, compare)
        self.assertTrue(next(f for f in files if f["path"] == "image.bin")["binary"])
        renamed = next(f for f in files if f["path"] == "重命名 文件.txt")
        self.assertEqual(renamed["old_path"], "中文 文件.txt")
        (self.repo / "重命名 文件.txt").write_text("uncommitted noise")
        patch_data = file_diff(self.repo, compare, renamed["path"], limit=2)
        self.assertTrue(patch_data["has_more"])
        full = file_diff(self.repo, compare, renamed["path"])
        texts = [line["text"] for line in full["lines"]]
        self.assertIn("changed", texts)
        self.assertNotIn("uncommitted noise", texts)

    def test_invalid_parent_and_file_are_rejected(self):
        with self.assertRaises(GitQueryError): comparison(self.repo, sha=self.initial, parent=self.head)
        with self.assertRaises(GitQueryError): file_diff(self.repo, comparison(self.repo, sha=self.head), "../outside")
        with self.assertRaises(GitQueryError): commit_sha(self.repo, "--help")

    def test_branch_comparison_and_frozen_versions(self):
        command(self.repo, "branch", "feature")
        command(self.repo, "reset", "--hard", self.initial)
        compare = comparison(self.repo, base="main", head="feature")
        self.assertEqual(compare["base_sha"], self.initial)
        self.assertEqual(compare["head_sha"], self.head)
        self.assertGreater(len(changed_files(self.repo, compare)), 0)
        self.assertIn("refs/heads/feature", [b["ref"] for b in branches(self.repo)["branches"]])

    def test_merge_parent_selection(self):
        command(self.repo, "checkout", "-b", "side", self.initial)
        (self.repo / "side.txt").write_text("side\n")
        command(self.repo, "add", ".")
        command(self.repo, "commit", "-m", "side")
        second = command(self.repo, "rev-parse", "HEAD")
        command(self.repo, "checkout", "main")
        command(self.repo, "merge", "--no-ff", "side", "-m", "merge")
        merged = comparison(self.repo, sha="HEAD", parent=second)
        self.assertEqual(merged["base_sha"], second)
        self.assertEqual(len(merged["commit"]["parents"]), 2)

    def test_discovery_deduplicates_and_revalidates_scope(self):
        nested = self.repo / "nested"
        nested.mkdir()
        command(nested, "init", "-b", "main")
        (self.repo / "linked").symlink_to(self.root, target_is_directory=True)
        result = discover(self.workspaces + [{"workspace_id": "nested", "label": "Nested", "path": str(nested)}], refresh=True)
        self.assertEqual(len(result["repositories"]), 2)
        repo_id = next(r["id"] for r in result["repositories"] if r["path"] == str(self.repo))
        with self.assertRaises(GitQueryError): resolve_repository(self.workspaces, repo_id, "unknown")
        self.assertEqual(resolve_repository(self.workspaces, repo_id, "fixture")[0], self.repo)

    def test_empty_repository_and_review_index(self):
        blank = self.root / "blank"
        blank.mkdir()
        command(blank, "init", "-b", "main")
        self.assertEqual(history(blank)["commits"], [])
        self.assertEqual(read_reviews(self.root / "data", "fixture", "repo"), [])
        record_review(self.root / "data", "fixture", "repo", {"conversation_id": "one"})
        self.assertEqual(read_reviews(self.root / "data", "fixture", "repo")[0]["conversation_id"], "one")
        self.assertEqual(read_reviews(self.root / "data", "other", "repo"), [])
        prompt = review_prompt(self.repo, comparison(self.repo, sha=self.head))
        self.assertIn(self.head, prompt)
        self.assertIn(self.initial, prompt)
        self.assertIn("git show SHA:路径", prompt)


def fake_module(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


class ReviewLaunchTests(unittest.TestCase):
    def setUp(self):
        from dataclasses import dataclass
        from flask import Flask
        self.app = Flask(__name__)
        self.app.secret_key = "synthetic-fixture-only"
        self.created = []
        self.accepted = []
        self.busy = False
        self.fail = False
        test = self
        @dataclass(frozen=True)
        class Principal:
            host_workspace_id: str = "source"
        class Manager:
            def __init__(self, **kwargs): pass
            def create_conversation(self, **kwargs): test.created.append(kwargs); return "conv_fixture"
            def update_conversation_title(self, *args): return True
            def update_conversation_metadata(self, *args): return True
        def create_task(ctx):
            test.accepted.append(ctx)
            if test.fail: raise RuntimeError("busy")
            return types.SimpleNamespace(task_id="task_fixture")
        runtime = types.SimpleNamespace(list_runs=lambda *a, **k: [1] if self.busy else [], create_task=create_task)
        workspace = types.SimpleNamespace(data_dir=Path("synthetic-data"), project_path=Path("synthetic-project"))
        modules = {
            "core.tool_loading": fake_module("core.tool_loading", snapshot_overrides_from_prefs=lambda p: {}),
            "modules.aux_model_resolver": fake_module("modules.aux_model_resolver", resolve_aux_model_profile=lambda k: {"api_protocol": "chat"}),
            "modules.personalization_manager": fake_module("modules.personalization_manager", load_personalization_config=lambda p: {"default_model": "fixture"}),
            "server.context.data": fake_module("server.context.data", get_workspace_data=lambda *a, **k: types.SimpleNamespace(workspace=workspace)),
            "server.runtime": fake_module("server.runtime", RuntimeContext=lambda **k: types.SimpleNamespace(**k), TaskParams=lambda **k: types.SimpleNamespace(**k), principal_from_session_snapshot=lambda *a, **k: Principal(), runtime_service=runtime),
            "utils.conversation_manager": fake_module("utils.conversation_manager", ConversationManager=Manager),
            "server.utils_common": fake_module("server.utils_common", debug_log=lambda *a: None),
        }
        spec = importlib.util.spec_from_file_location("git_review_launch_fixture", ROOT / "server/project_git_review.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules): spec.loader.exec_module(self.module)
        self.module.read_reviews = lambda *a: []
        self.module.record_review = lambda *a: None
        self.compare = {"kind": "commit", "head_sha": "a" * 40, "base_sha": "b" * 40, "commit": {"subject": "fixture"}}
        self.identity = types.SimpleNamespace(host_mode=True)

    def launch(self):
        return self.module.launch_review("host", "target", "repo", Path("project"), self.compare, self.identity)

    def test_new_conversation_is_readonly_execute_and_explicit_workspace(self):
        with self.app.test_request_context(): result = self.launch()
        metadata = self.created[0]["metadata_overrides"]
        self.assertEqual(metadata["permission_mode"], "readonly")
        self.assertEqual(metadata["work_mode"], "execute")
        self.assertEqual(metadata["execution_mode"], "sandbox")
        self.assertEqual(self.accepted[0].principal.host_workspace_id, "target")
        self.assertEqual(result["workspace_id"], "target")
        self.assertEqual(self.accepted[0].params.message_source, "git_review")

    def test_busy_workspace_does_not_create_or_queue(self):
        self.busy = True
        with self.app.test_request_context(), self.assertRaises(RuntimeError): self.launch()
        self.assertEqual(self.created, [])
        self.assertEqual(self.accepted, [])

    def test_task_failure_is_reported(self):
        self.fail = True
        with self.app.test_request_context(), self.assertRaises(RuntimeError): self.launch()


if __name__ == "__main__":
    unittest.main()
