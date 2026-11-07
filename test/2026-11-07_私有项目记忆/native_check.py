"""Finite native checks. Uses only synthetic data and uniquely named containers.

Run macOS sandbox checks: python3 native_check.py host
Run Docker checks: python3 native_check.py docker
Never constructs a production container manager (its startup cleanup is avoided).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import uuid

import test_workspace_memory as fixtures
from modules.file_manager import FileManager
from modules.execution_plane.local import LocalExecutionBackend
from modules.host_sandbox_runner import build_host_sandbox_plan, build_host_sandbox_readonly_plan
from modules.workspace_memory import location, prepare_directory
from modules.workspace_memory_context import main_prompt
from modules.workspace_memory_tools import write_injected_memory


def host_check():
    if platform.system() != "Darwin":
        raise RuntimeError("This native profile check requires macOS")
    case = fixtures.MemoryTest()
    case.setUp()
    try:
        case.seed("native-v1")
        main_prompt(case.owner)
        path = shlex.quote(case.target.tool_path)
        command = f"printf native-v2 > {path}"
        writable = build_host_sandbox_plan(command, case.project, dict(os.environ),
                                           network_permission="none", workspace_root=case.project)
        result = subprocess.run(writable.command, env=writable.env, cwd=writable.cwd,
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert case.target.file.read_text() == "native-v2"
        readonly = build_host_sandbox_readonly_plan(f"printf forbidden > {path}", case.project,
                     dict(os.environ), network_permission="none", workspace_root=case.project)
        result = subprocess.run(readonly.command, env=readonly.env, cwd=readonly.cwd,
                                capture_output=True, text=True, timeout=20)
        assert result.returncode != 0, "Readonly profile unexpectedly wrote memory"
        assert case.target.file.read_text() == "native-v2"
        print(json.dumps({"host": "passed", "write": True, "readonly_denied": True}))
    finally:
        case.tearDown()


def docker_check():
    from test_integration import ContainerTest
    docker = "/usr/local/bin/docker"
    if not Path(docker).exists():
        import shutil
        docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI missing")
    subprocess.run([docker, "image", "inspect", "python:3.11-slim"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15)
    with tempfile.TemporaryDirectory(prefix="astrion-memory-native-") as temporary:
        root = Path(temporary).resolve()
        project, data = root / "project", root / "data"
        project.mkdir()
        target = location(str(project), str(data), docker=True)
        prepare_directory(target)
        name = "astrion-memory-check-" + uuid.uuid4().hex[:16]
        manager = ContainerTest().manager()
        manager.sandbox_bin = docker
        manager.mount_path = "/workspace"
        manager.network = "none"
        manager.image = "python:3.11-slim"
        manager._build_container_name = lambda key: name
        created = False
        try:
            handle = manager._create_handle("synthetic", str(project), "docker", memory_directory=str(target.directory))
            created = True
            owner = SimpleNamespace(project_path=str(project), data_dir=str(data),
                                    container_session=handle, context_manager=fixtures.Context())
            owner.file_manager = FileManager(str(project), container_session=handle, data_dir=str(data))
            owner.execution_backend = LocalExecutionBackend(owner)
            owner.load_prompt = lambda key: (fixtures.REPO / "prompts" / f"{key}.txt").read_text()
            frozen = main_prompt(owner)
            result = write_injected_memory(owner, "write_file", {
                "file_path": target.tool_path, "content": "docker-persisted\n"})
            assert result.get("success"), result
            assert target.file.read_text() == "docker-persisted\n"
            assert not (project / ".astrion/astrion-memory/MEMORY.md").exists()
            assert main_prompt(owner) == frozen
            owner.context_manager = fixtures.Context()
            assert "docker-persisted" in main_prompt(owner)
            subprocess.run([docker, "rm", "-f", name], check=True, capture_output=True, timeout=20)
            created = False
            handle = manager._create_handle("synthetic", str(project), "docker", memory_directory=str(target.directory))
            created = True
            owner.container_session = handle
            owner.file_manager.set_container_session(handle)
            owner.context_manager = fixtures.Context()
            assert "docker-persisted" in main_prompt(owner)
            # Check Git's ignore behavior inside the *real mounted directory*.
            git = "/usr/bin/git"
            subprocess.run([git, "init", "-q", str(project)], check=True, capture_output=True,
                           env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
            probe = subprocess.run([docker, "exec", name, "python3", "-c",
                "from pathlib import Path; assert Path('/workspace/.astrion/astrion-memory/.gitignore').read_text() == '*\\n'"],
                capture_output=True, text=True, timeout=20)
            assert probe.returncode == 0, probe.stderr
            print(json.dumps({"docker": "passed", "external_persistence": True,
                              "container_recreation": True, "mounted_path": target.tool_path,
                              "ignore_file_present": True}))
        finally:
            if created:
                subprocess.run([docker, "rm", "-f", name], check=True, capture_output=True, timeout=20)


if __name__ == "__main__":
    {"host": host_check, "docker": docker_check}[sys.argv[1]]()
