"""执行环境显示 + 深树路由回归（2026-11-07）。

覆盖：
1. 深树（maxlevel=5）经执行后端 ``list_files``，内存 backend 在本机不存在项目目录时仍输出；
2. 环境说明以 ``get_environment`` 为唯一事实源：Windows/Linux 目标按后端上报显示；
3. 元数据缺失（空 dict）时明确“未知”，不拿控制端 OS/路径兜底；
4. docker 目标 ``container.os=unknown`` 明确未知；
5. 本地 backend 的宿主机文本继续复用 ``_host_runtime_cache``；
6. ``_build_workspace_system_message`` 的 runtime_environment / resource_limit /
   container_path 走同一份后端事实，不用控制端挂载路径。

只使用内存替身与临时运行目录，不读真实 .env/settings.json，不启动服务。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 必须在导入 config / modules 之前隔离运行态。
from read_routing_env import prepare_isolated_runtime  # noqa: E402

_TEST_HOME = prepare_isolated_runtime("astrion_env_routing_")

from read_routing_fakes import (  # noqa: E402
    CONTROLLER_PATH,
    EnvironmentDisplayStub,
    FakeFiles,
    ProjectStub,
    WorkspaceMessageStub,
)


class DeepTreeRoutingTest(unittest.TestCase):
    """内存 backend + 本机不存在项目目录：深树仍来自执行后端。"""

    def _nested_files(self):
        return FakeFiles(
            listings={
                "": {
                    "folders": [{"name": "src", "path": "src"}],
                    "files": [{"name": "README.md", "path": "README.md",
                               "size": 100, "modified": "2026-11-07T03:00:00"}],
                },
                "src": {
                    "folders": [{"name": "pkg", "path": "src/pkg"}],
                    "files": [{"name": "main.py", "path": "src/main.py",
                               "size": 10, "modified": "2026-11-07T03:01:00"}],
                },
                "src/pkg": {
                    "folders": [],
                    "files": [{"name": "deep.py", "path": "src/pkg/deep.py",
                               "size": 5, "modified": "2026-11-07T03:02:00"}],
                },
            },
            metas={},
        )

    def test_deep_tree_from_memory_backend_without_local_dir(self):
        self.assertFalse(Path(CONTROLLER_PATH).exists())
        stub = ProjectStub(self._nested_files(), project_path=CONTROLLER_PATH)

        structure = stub.get_project_structure()

        self.assertEqual(structure["total_files"], 3)
        self.assertEqual(structure["total_size"], 115)
        self.assertEqual([item["path"] for item in structure["folders"]],
                         ["src", "src/pkg"])
        self.assertEqual(
            sorted(item["path"] for item in structure["files"]),
            ["README.md", "src/main.py", "src/pkg/deep.py"],
        )
        readme = [item for item in structure["files"] if item["path"] == "README.md"][0]
        self.assertEqual(readme["modified"], "2026-11-07T03:00:00")
        deep = structure["tree"]["src"]["children"]["pkg"]["children"]["deep.py"]
        self.assertEqual(deep["type"], "file")
        self.assertEqual(deep["size"], 5)
        self.assertEqual(deep["path"], "src/pkg/deep.py")

    def test_deep_tree_respects_max_depth_five(self):
        listings = {}
        parts = []
        for index in range(7):
            name = f"d{index}"
            listings["/".join(parts)] = {
                "folders": [{"name": name, "path": "/".join(parts + [name])}],
                "files": [],
            }
            parts.append(name)
        listings["/".join(parts)] = {
            "folders": [],
            "files": [{"name": "leaf.txt", "path": "/".join(parts + ["leaf.txt"]),
                       "size": 1}],
        }
        stub = ProjectStub(FakeFiles(listings=listings), project_path=CONTROLLER_PATH)

        structure = stub.get_project_structure()

        folder_paths = [item["path"] for item in structure["folders"]]
        self.assertIn("d0/d1/d2/d3/d4/d5", folder_paths)
        self.assertNotIn("d0/d1/d2/d3/d4/d5/d6", folder_paths)
        self.assertEqual(structure["total_files"], 0)


class ExecutorEnvironmentDisplayTest(unittest.TestCase):
    """环境说明只信 ``get_environment``，控制端 OS/路径不得冒充执行目标。"""

    WINDOWS_ENV = {
        "backend": "remote-windows",
        "is_local": False,
        "platform": "win32",
        "execution_target": "host",
        "execution_mode": "sandbox",
        "network_permission": "restricted",
        "host": {"platform": "win32", "system": "Windows", "release": "10",
                 "machine": "AMD64", "python": "3.11.9"},
        "workspace_root": "C:\\executor\\ws",
        "cwd": "C:\\executor\\ws",
        "container": None,
    }

    LINUX_ENV = {
        "backend": "remote-linux",
        "is_local": False,
        "platform": "linux",
        "execution_target": "host",
        "execution_mode": "sandbox",
        "network_permission": "restricted",
        "host": {"platform": "linux", "system": "Linux",
                 "release": "6.8.0-45-generic", "machine": "x86_64",
                 "python": "3.11.11"},
        "workspace_root": "/srv/executor/ws",
        "cwd": "/srv/executor/ws",
        "container": None,
    }

    DOCKER_ENV = {
        "backend": "remote-docker",
        "is_local": False,
        "platform": "linux",
        "execution_target": "docker",
        "execution_mode": "sandbox",
        "network_permission": "restricted",
        "workspace_root": "/srv/executor/ws",
        "cwd": "/workspace",
        "container": {"mount_path": "/workspace", "os": "unknown"},
    }

    def _display(self, environment, **kwargs):
        return EnvironmentDisplayStub(environment, **kwargs)._resolve_runtime_display()

    # -- 后端事实 ------------------------------------------------------
    def test_windows_target_display_uses_backend_facts(self):
        display = self._display(self.WINDOWS_ENV)

        self.assertEqual(
            display["runtime_environment"],
            "远端执行后端（remote-windows）\n"
            "  OS: Windows 10 | Arch: AMD64 | Python: 3.11.9\n"
            "  执行模式: sandbox | 网络: restricted\n"
            "  工作区: C:\\executor\\ws",
        )
        self.assertEqual(display["resource_limit"], "由执行后端决定（未上报资源配额）")
        self.assertEqual(display["container_path"], "C:\\executor\\ws")

    def test_linux_target_display_uses_backend_facts(self):
        display = self._display(self.LINUX_ENV)

        self.assertEqual(
            display["runtime_environment"],
            "远端执行后端（remote-linux）\n"
            "  OS: Linux 6.8.0-45-generic | Arch: x86_64 | Python: 3.11.11\n"
            "  执行模式: sandbox | 网络: restricted\n"
            "  工作区: /srv/executor/ws",
        )
        self.assertEqual(display["container_path"], "/srv/executor/ws")

    def test_local_backend_reuses_host_cache(self):
        env = {
            "backend": "local",
            "is_local": True,
            "execution_target": "host",
            "platform": "darwin",
            "execution_mode": "host",
            "network_permission": "full",
            "workspace_root": "/ws",
            "cwd": "/ws",
            "container": None,
        }
        stub = EnvironmentDisplayStub(env, host_cache={
            "os": "CACHED-OS", "arch": "CACHED-ARCH", "model": "CACHED-MODEL",
            "python": "CACHED-PYTHON", "node": "CACHED-NODE", "git": "CACHED-GIT",
        })

        display = stub._resolve_runtime_display()

        # 本地 backend 的宿主机文本继续走 cache（不重新探测）
        self.assertTrue(display["runtime_environment"].startswith("宿主机模式"))
        self.assertIn("CACHED-OS", display["runtime_environment"])
        self.assertIn("CACHED-NODE", display["runtime_environment"])
        self.assertEqual(display["resource_limit"], "宿主机模式无限制")
        self.assertEqual(display["container_path"], "/ws")

    # -- 缺失即未知 ----------------------------------------------------
    def test_missing_metadata_does_not_fabricate_platform(self):
        display = self._display({})

        self.assertEqual(
            display["runtime_environment"],
            "远端执行后端（remote）\n"
            "  OS: 未知（执行后端未上报平台） | Arch: 未知 | Python: 未知\n"
            "  执行模式: 未知 | 网络: 未知",
        )
        self.assertEqual(display["resource_limit"], "由执行后端决定（未上报资源配额）")

    def test_missing_workspace_path_does_not_use_controller_path(self):
        stub = ProjectStub(
            FakeFiles(listings={"": {"folders": [{"name": "src", "path": "src"}],
                                    "files": []}}),
            environment={},
            project_path=CONTROLLER_PATH,
        )

        tree = stub._build_file_tree(stub._get_project_structure_for_prompt())

        self.assertIn("（执行后端未上报工作区路径） (项目根)", tree)
        self.assertNotIn(CONTROLLER_PATH, tree)

    def test_empty_project_root_path_does_not_use_controller_path(self):
        stub = ProjectStub(
            FakeFiles(listings={"": {"folders": [], "files": []}}),
            environment={},
            project_path=CONTROLLER_PATH,
        )

        tree = stub._build_file_tree(stub._get_project_structure_for_prompt())

        self.assertEqual(tree, "📁 （执行后端未上报工作区路径）/\n(空项目)")

    def test_docker_unknown_os_is_explicit(self):
        display = self._display(self.DOCKER_ENV)

        self.assertEqual(
            display["runtime_environment"],
            "隔离容器中（挂载目录 /workspace，后端 remote-docker）\n"
            "  OS: 未知（容器内系统未探测）",
        )
        self.assertEqual(display["resource_limit"], "由执行后端决定（未上报资源配额）")
        self.assertEqual(display["container_path"], "/workspace")

    # -- 工作区系统消息接线 -------------------------------------------
    def test_workspace_message_uses_backend_environment(self):
        stub = WorkspaceMessageStub(self.WINDOWS_ENV)

        content = stub._build_workspace_system_message(
            {"project_info": {"file_tree": "src/"}}
        )

        self.assertIn("name=project", content)
        self.assertIn("Windows 10", content)
        self.assertIn("由执行后端决定（未上报资源配额）", content)
        self.assertNotIn(CONTROLLER_PATH, content)
        self.assertIn("src/", content)

    def test_workspace_message_docker_ignores_controller_mount(self):
        stub = WorkspaceMessageStub(self.DOCKER_ENV, container_mount_path="/controller-mount")

        content = stub._build_workspace_system_message(
            {"project_info": {"file_tree": ""}}
        )

        self.assertIn("container_path=/workspace", content)
        self.assertNotIn("/controller-mount", content)
        self.assertIn("未知（容器内系统未探测）", content)
        self.assertNotIn("macOS", content)


if __name__ == "__main__":
    unittest.main()
