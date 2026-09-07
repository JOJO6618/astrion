"""FakeExecutionBackend：内存替身执行器（Execution Plane 的首个非真实实现）。

用途：Runtime + 替身执行器的独立测试（docs/execution_contract.md §6 验收）。
不产生任何真实副作用：不起子进程、不写磁盘、不开终端。
文件写落内存文件系统；命令执行只记录调用并返回固定结构。

非目标：不模拟命令真实输出语义、不模拟审批/权限裁决（那是 Runtime 编排层职责）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional


class FakeExecutionBackend:
    """内存替身执行器。calls 记录全部调用（供测试断言）。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.files: Dict[str, str] = {}  # path -> content（内存文件系统）
        self.background_commands: Dict[str, Dict[str, Any]] = {}

    async def run_command(
        self,
        command: str,
        *,
        timeout: float,
        sandbox_write_access: bool,
        network_permission: str,
    ) -> Dict[str, Any]:
        self.calls.append({
            "op": "run_command",
            "command": command,
            "timeout": timeout,
            "sandbox_write_access": sandbox_write_access,
            "network_permission": network_permission,
        })
        return {
            "success": True,
            "status": "completed",
            "output": f"[fake-exec] {command}",
            "return_code": 0,
            "truncated": False,
            "elapsed_ms": 0,
        }

    def run_command_background(
        self,
        command: str,
        *,
        timeout: float,
        conversation_id: Optional[str],
        wait_seconds: float,
        network_permission: str,
        sandbox_write_access: bool,
    ) -> Dict[str, Any]:
        command_id = f"fake_bg_{uuid.uuid4().hex[:8]}"
        self.calls.append({
            "op": "run_command_background",
            "command": command,
            "timeout": timeout,
            "conversation_id": conversation_id,
            "sandbox_write_access": sandbox_write_access,
            "network_permission": network_permission,
        })
        self.background_commands[command_id] = {
            "command": command,
            "status": "completed",
            "output": f"[fake-exec-bg] {command}",
            "return_code": 0,
            "created_at": time.time(),
        }
        return {
            "success": True,
            "command_id": command_id,
            "status": "running_background",
        }

    def write_file(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]:
        original = self.files.get(path)
        if mode == "a" and original is not None:
            new_content = original + content
        else:
            new_content = content
        self.files[path] = new_content
        self.calls.append({"op": "write_file", "path": path, "mode": mode, "bytes": len(content)})
        return {
            "success": True,
            "path": path,
            "original_file": original,
            "new_file": new_content,
        }

    def edit_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict[str, Any]:
        original = self.files.get(path)
        if original is None:
            self.calls.append({"op": "edit_file", "path": path, "error": "not_found"})
            return {"success": False, "error": f"文件不存在: {path}"}
        new_content = original
        applied = 0
        for rep in replacements:
            old = rep.get("old_string", "")
            new = rep.get("new_string", "")
            if not old or old not in new_content:
                continue
            if rep.get("replace_all"):
                applied += new_content.count(old)
                new_content = new_content.replace(old, new)
            else:
                new_content = new_content.replace(old, new, 1)
                applied += 1
        if applied == 0:
            return {"success": False, "error": "未找到任何匹配内容"}
        self.files[path] = new_content
        self.calls.append({"op": "edit_file", "path": path, "replacements_applied": applied})
        return {
            "success": True,
            "path": path,
            "original_file": original,
            "new_file": new_content,
            "replacements_applied": applied,
        }
