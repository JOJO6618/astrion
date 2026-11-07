"""环境快照不得拿宿主工作区元数据冒充容器。无需真实 Docker。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.execution_plane.local import LocalExecutionBackend


class ContainerEnvironmentTest(unittest.TestCase):
    def backend(self, mount_path):
        owner = SimpleNamespace(
            project_path="/controller/mirror",
            container_session=SimpleNamespace(
                mode="docker", mount_path=mount_path, container_name="test-container",
            ),
        )
        return LocalExecutionBackend(owner)

    def test_container_uses_its_mount_and_unknown_case_rules(self):
        with patch("modules.execution_plane.environment.detect_case_sensitivity",
                   side_effect=AssertionError("must not inspect controller mirror")):
            environment = self.backend("/workspace").get_environment()
        self.assertEqual(environment["workspace_root"], "/workspace")
        self.assertEqual(environment["cwd"], "/workspace")
        self.assertEqual(environment["execution_target"], "docker")
        self.assertEqual(environment["container"]["os"], "unknown")
        self.assertIsNone(environment["case_sensitive"])
        self.assertEqual(environment["case_sensitive_source"], "unknown")
        self.assertFalse(environment["supports_full_access"])
        self.assertFalse(environment["capabilities"]["full_access"])

    def test_missing_mount_does_not_fall_back_to_controller(self):
        environment = self.backend(None).get_environment()
        self.assertIsNone(environment["workspace_root"])
        self.assertIsNone(environment["cwd"])


if __name__ == "__main__":
    unittest.main()
