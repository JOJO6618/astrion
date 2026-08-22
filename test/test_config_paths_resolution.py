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
        self.repo_root = Path(__file__).resolve().parents[1]

    def _load_paths(self, *, cwd: Path, extra_env: dict | None = None) -> dict:
        code = """
import json
import config
print(json.dumps({
  "RUNTIME_ROOT": config.RUNTIME_ROOT,
  "DEFAULT_PROJECT_PATH": config.DEFAULT_PROJECT_PATH,
  "HOST_WORKSPACES_FILE": config.HOST_WORKSPACES_FILE,
  "PROMPTS_DIR": config.PROMPTS_DIR,
  "DATA_DIR": config.DATA_DIR,
  "LOGS_DIR": config.LOGS_DIR,
  "USER_SPACE_DIR": config.USER_SPACE_DIR,
  "API_USER_SPACE_DIR": config.API_USER_SPACE_DIR,
  "DEPLOY_CONFIG_DIR": config.DEPLOY_CONFIG_DIR,
  "SUB_AGENT_STATE_FILE": config.SUB_AGENT_STATE_FILE,
  "SUB_AGENT_TASKS_BASE_DIR": config.SUB_AGENT_TASKS_BASE_DIR,
}, ensure_ascii=False))
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo_root)
        # 清掉可能从外部 .env / 环境继承的覆盖项，保证用例可控。
        for key in (
            "TERMINAL_SANDBOX_MODE", "ASTRION_DATA_ROOT",
            "DATA_DIR", "LOGS_DIR", "USER_SPACE_DIR", "API_USER_SPACE_DIR",
            "DEFAULT_PROJECT_PATH", "HOST_WORKSPACES_FILE", "PROMPTS_DIR",
            "DEPLOY_CONFIG_DIR",
            "SUB_AGENT_STATE_FILE", "SUB_AGENT_TASKS_BASE_DIR",
        ):
            env.pop(key, None)
        if extra_env:
            env.update(extra_env)
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        self.assertTrue(lines, f"stdout is empty, stderr={completed.stderr}")
        return json.loads(lines[-1])

    def test_runtime_dirs_default_to_web_home(self):
        """默认（非 host）模式下，运行态根为 ~/.astrion/astrion，模式子目录为 web。"""
        with tempfile.TemporaryDirectory() as td:
            data = self._load_paths(cwd=Path(td), extra_env={"TERMINAL_SANDBOX_MODE": "docker"})

        runtime_root = Path("~/.astrion/astrion").expanduser().resolve()
        web_home = runtime_root / "web"
        self.assertEqual(data["RUNTIME_ROOT"], str(runtime_root))
        self.assertEqual(data["DATA_DIR"], str(web_home / "data"))
        self.assertEqual(data["LOGS_DIR"], str(web_home / "logs"))
        self.assertEqual(data["USER_SPACE_DIR"], str(web_home / "users"))
        self.assertEqual(data["API_USER_SPACE_DIR"], str(web_home / "api" / "users"))
        # 数据文件跟随 DATA_DIR
        self.assertEqual(data["SUB_AGENT_STATE_FILE"], str(web_home / "data" / "sub_agents.json"))
        self.assertEqual(data["SUB_AGENT_TASKS_BASE_DIR"], str(web_home / "data" / "sub_agent_tasks"))

    def test_runtime_dirs_switch_to_host_home(self):
        """host 模式下，运行态根为 ~/.astrion/astrion，模式子目录为 host。"""
        with tempfile.TemporaryDirectory() as td:
            data = self._load_paths(cwd=Path(td), extra_env={"TERMINAL_SANDBOX_MODE": "host"})

        runtime_root = Path("~/.astrion/astrion").expanduser().resolve()
        host_home = runtime_root / "host"
        self.assertEqual(data["RUNTIME_ROOT"], str(runtime_root))
        self.assertEqual(data["DATA_DIR"], str(host_home / "data"))
        self.assertEqual(data["LOGS_DIR"], str(host_home / "logs"))

    def test_source_tree_paths_stay_anchored_to_repo_root(self):
        """配置类路径（提示词、默认工作区）仍锚定源码树。"""
        with tempfile.TemporaryDirectory() as td:
            data = self._load_paths(cwd=Path(td), extra_env={"TERMINAL_SANDBOX_MODE": "docker"})

        for key in ("DEFAULT_PROJECT_PATH", "PROMPTS_DIR"):
            value = Path(data[key])
            self.assertTrue(value.is_absolute(), f"{key} not absolute: {data[key]}")
            self.assertTrue(
                str(value).startswith(str(self.repo_root)),
                f"{key} should anchor to repo root: {data[key]}",
            )

    def test_deploy_config_anchors_to_runtime_root(self):
        """部署级配置目录与 host_workspaces.json 锚定到运行态根 ~/.astrion/astrion/config（host/web 共享）。"""
        with tempfile.TemporaryDirectory() as td:
            data = self._load_paths(cwd=Path(td), extra_env={"TERMINAL_SANDBOX_MODE": "web"})

        runtime_config = Path("~/.astrion/astrion/config").expanduser().resolve()
        self.assertEqual(data["DEPLOY_CONFIG_DIR"], str(runtime_config))
        self.assertEqual(
            data["HOST_WORKSPACES_FILE"], str(runtime_config / "host_workspaces.json")
        )

    def test_deploy_config_follows_data_root(self):
        """数据根变量覆盖时，部署配置目录跟随该根目录。"""
        with tempfile.TemporaryDirectory() as td:
            custom = Path(td) / "web_root"
            data = self._load_paths(
                cwd=Path(td),
                extra_env={
                    "TERMINAL_SANDBOX_MODE": "web",
                    "ASTRION_DATA_ROOT": str(custom),
                },
            )
        self.assertEqual(data["DEPLOY_CONFIG_DIR"], str((custom / "config").resolve()))

    def test_data_root_env_overrides_root(self):
        """数据根变量（ASTRION_DATA_ROOT）整体搬迁运行态目录。"""
        with tempfile.TemporaryDirectory() as td:
            custom = Path(td) / "host_root"
            data = self._load_paths(
                cwd=Path(td),
                extra_env={
                    "TERMINAL_SANDBOX_MODE": "host",
                    "ASTRION_DATA_ROOT": str(custom),
                },
            )

        self.assertEqual(data["RUNTIME_ROOT"], str(custom.resolve()))
        self.assertEqual(data["DATA_DIR"], str((custom / "host" / "data").resolve()))
        self.assertEqual(data["LOGS_DIR"], str((custom / "host" / "logs").resolve()))

    def test_specific_dir_env_has_highest_priority(self):
        """具体目录变量优先级高于数据根变量。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            logs = Path(td) / "separate_logs"
            data = self._load_paths(
                cwd=Path(td),
                extra_env={
                    "TERMINAL_SANDBOX_MODE": "web",
                    "ASTRION_DATA_ROOT": str(root),
                    "LOGS_DIR": str(logs),
                },
            )

        # DATA 跟随根变量下的模式子目录，LOGS 被单独覆盖
        self.assertEqual(data["DATA_DIR"], str((root / "web" / "data").resolve()))
        self.assertEqual(data["LOGS_DIR"], str(logs.resolve()))

    def test_relative_specific_dir_env_anchors_to_repo_root(self):
        """具体目录变量给相对路径时，仍锚定仓库根目录。"""
        with tempfile.TemporaryDirectory() as td:
            data = self._load_paths(
                cwd=Path(td),
                extra_env={
                    "TERMINAL_SANDBOX_MODE": "docker",
                    "DATA_DIR": "./data_rel",
                    "LOGS_DIR": "./logs_rel",
                    "USER_SPACE_DIR": "./users_rel",
                    "DEFAULT_PROJECT_PATH": "./workspace_rel",
                    "HOST_WORKSPACES_FILE": "./config/host_workspaces_rel.json",
                    "SUB_AGENT_STATE_FILE": "./data/sub_agents_rel.json",
                },
            )

        self.assertEqual(data["DATA_DIR"], str((self.repo_root / "data_rel").resolve()))
        self.assertEqual(data["LOGS_DIR"], str((self.repo_root / "logs_rel").resolve()))
        self.assertEqual(data["USER_SPACE_DIR"], str((self.repo_root / "users_rel").resolve()))
        self.assertEqual(data["DEFAULT_PROJECT_PATH"], str((self.repo_root / "workspace_rel").resolve()))
        self.assertEqual(
            data["HOST_WORKSPACES_FILE"],
            str((self.repo_root / "config" / "host_workspaces_rel.json").resolve()),
        )
        self.assertEqual(
            data["SUB_AGENT_STATE_FILE"],
            str((self.repo_root / "data" / "sub_agents_rel.json").resolve()),
        )


if __name__ == "__main__":
    unittest.main()
