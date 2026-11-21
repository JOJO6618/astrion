from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ConfigPathsResolutionTest(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[2]
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

    def _load_paths(self, *, cwd: Path, extra_env: dict | None = None) -> dict:
        code = """
import json
import config
keys = (
    "RUNTIME_ROOT", "DEFAULT_PROJECT_PATH", "HOST_WORKSPACES_FILE", "PROMPTS_DIR",
    "DATA_DIR", "LOGS_DIR", "DEPLOY_CONFIG_DIR", "SUB_AGENT_STATE_FILE",
    "SUB_AGENT_TASKS_BASE_DIR",
)
print(json.dumps({key: getattr(config, key) for key in keys}))
"""
        env = dict(os.environ)
        for key in (
            "TERMINAL_SANDBOX_MODE", "LINUX_SAFETY", "ASTRION_DATA_ROOT",
            "DATA_DIR", "LOGS_DIR", "DEFAULT_PROJECT_PATH", "HOST_WORKSPACES_FILE",
            "PROMPTS_DIR", "DEPLOY_CONFIG_DIR", "SUB_AGENT_STATE_FILE",
            "SUB_AGENT_TASKS_BASE_DIR",
        ):
            env.pop(key, None)
        env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                   ASTRION_IGNORE_DOTENV="1", PYTHONPATH=str(self.repo_root))
        env.update(extra_env or {})
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=cwd, env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lines = completed.stdout.splitlines()
        self.assertTrue(lines, completed.stderr)
        return json.loads(lines[-1])

    def test_runtime_dirs_default_to_host_home(self):
        data = self._load_paths(cwd=self.home)
        runtime_root = self.home / ".astrion" / "astrion"
        host_data = runtime_root / "host" / "data"
        self.assertEqual(data["RUNTIME_ROOT"], str(runtime_root))
        self.assertEqual(data["DATA_DIR"], str(host_data))
        self.assertEqual(data["LOGS_DIR"], str(runtime_root / "host" / "logs"))
        self.assertEqual(data["SUB_AGENT_STATE_FILE"], str(host_data / "sub_agents.json"))
        self.assertEqual(data["SUB_AGENT_TASKS_BASE_DIR"], str(host_data / "sub_agent_tasks"))

    def test_source_tree_paths_stay_anchored_to_repo_root(self):
        data = self._load_paths(cwd=self.home)
        for key in ("DEFAULT_PROJECT_PATH", "PROMPTS_DIR"):
            self.assertTrue(Path(data[key]).is_relative_to(self.repo_root), data[key])

    def test_deploy_config_anchors_to_runtime_root(self):
        data = self._load_paths(cwd=self.home)
        runtime_config = self.home / ".astrion" / "astrion" / "config"
        self.assertEqual(data["DEPLOY_CONFIG_DIR"], str(runtime_config))
        self.assertEqual(data["HOST_WORKSPACES_FILE"], str(runtime_config / "host_workspaces.json"))

    def test_data_root_moves_host_directories_and_deploy_config(self):
        custom = self.home / "host-root"
        data = self._load_paths(cwd=self.home, extra_env={"ASTRION_DATA_ROOT": str(custom)})
        self.assertEqual(data["RUNTIME_ROOT"], str(custom))
        self.assertEqual(data["DATA_DIR"], str(custom / "host" / "data"))
        self.assertEqual(data["LOGS_DIR"], str(custom / "host" / "logs"))
        self.assertEqual(data["DEPLOY_CONFIG_DIR"], str(custom / "config"))

    def test_specific_dir_env_has_highest_priority(self):
        root = self.home / "root"
        logs = self.home / "separate_logs"
        data = self._load_paths(cwd=self.home, extra_env={
            "ASTRION_DATA_ROOT": str(root), "LOGS_DIR": str(logs),
        })
        self.assertEqual(data["DATA_DIR"], str(root / "host" / "data"))
        self.assertEqual(data["LOGS_DIR"], str(logs))

    def test_relative_specific_dir_env_anchors_to_repo_root(self):
        relative_paths = {
            "DATA_DIR": "./data_rel",
            "LOGS_DIR": "./logs_rel",
            "DEFAULT_PROJECT_PATH": "./workspace_rel",
            "HOST_WORKSPACES_FILE": "./config/host_workspaces_rel.json",
            "SUB_AGENT_STATE_FILE": "./data/sub_agents_rel.json",
        }
        data = self._load_paths(cwd=self.home, extra_env=relative_paths)
        for key, path in relative_paths.items():
            self.assertEqual(data[key], str((self.repo_root / path).resolve()))


if __name__ == "__main__":
    unittest.main()
