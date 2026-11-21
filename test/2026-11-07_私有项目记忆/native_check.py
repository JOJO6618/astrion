"""Finite native Host checks. Uses only synthetic data.

Run macOS sandbox checks: python3 native_check.py
"""
from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess

import test_workspace_memory as fixtures
from modules.host_sandbox_runner import build_host_sandbox_plan, build_host_sandbox_readonly_plan
from modules.workspace_memory_context import main_prompt


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


if __name__ == "__main__":
    host_check()
