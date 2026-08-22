"""子智能体 Python 重写回归测试（mock 模型）。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

import sys
# 测试脚本在 test/ 目录下，需要把项目根目录加入路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 把子智能体运行态数据重定向到临时目录，避免写入受保护的 ~/.agents 路径
_test_tmp = Path(tempfile.mkdtemp(prefix="sub_agent_cfg_"))
os.environ["SUB_AGENT_TASKS_BASE_DIR"] = str(_test_tmp / "tasks")
os.environ["SUB_AGENT_STATE_FILE"] = str(_test_tmp / "state.json")
os.environ["SUB_AGENT_PROJECT_RESULTS_DIR"] = str(_test_tmp / "results")

from modules.sub_agent import SubAgentManager
from utils.api_client import APIClient


class FakeTerminal:
    """模拟 WebTerminal，只实现 handle_tool_call。"""

    def __init__(self, project_path: Path):
        self.project_path = project_path

    async def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> str:
        if name == "write_file":
            path = self.project_path / arguments["file_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if arguments.get("append") else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(arguments["content"])
            return json.dumps({"success": True, "message": "写入成功", "path": str(path)})

        if name == "edit_file":
            path = self.project_path / arguments["file_path"]
            content = path.read_text(encoding="utf-8")
            count = 0
            for repl in arguments.get("replacements", []):
                old = repl["old_string"]
                new = repl["new_string"]
                if repl.get("replace_all"):
                    n = content.count(old)
                    content = content.replace(old, new)
                    count += n
                else:
                    content = content.replace(old, new, 1)
                    count += 1
            path.write_text(content, encoding="utf-8")
            return json.dumps({"success": True, "path": str(path), "replacements": count})

        if name == "read_file":
            path = self.project_path / arguments["path"]
            if not path.exists():
                return json.dumps({"success": False, "error": f"文件不存在: {path}"})
            text = path.read_text(encoding="utf-8")
            return json.dumps({"success": True, "type": "read", "content": text})

        if name == "run_command":
            import subprocess
            cmd = arguments["command"]
            timeout = arguments.get("timeout", 30)
            cwd = arguments.get("working_dir")
            if cwd:
                cwd = self.project_path / cwd
            # mock 中不真正执行长时间 sleep，避免阻塞事件循环
            if cmd.strip().startswith("sleep "):
                return json.dumps({"success": True, "output": f"mock: {cmd}"})
            try:
                proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
                output = (proc.stdout or "") + (proc.stderr or "")
                return json.dumps({"success": proc.returncode == 0, "output": output})
            except Exception as exc:
                return json.dumps({"success": False, "output": str(exc)})

        return json.dumps({"success": False, "error": f"未实现的工具: {name}"})


class FakeModel:
    """按脚本驱动 APIClient.chat 的返回。"""

    def __init__(self, responses: List[List[Dict[str, Any]]]):
        self.responses = responses
        self.call_index = 0

    async def chat(self, messages, *, tools=None, stream=True, **kwargs):
        if self.call_index >= len(self.responses):
            chunks = [{"choices": [{"delta": {"content": ""}}]}]
        else:
            chunks = self.responses[self.call_index]
        self.call_index += 1
        for chunk in chunks:
            yield chunk


def make_tool_chunk(tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "choices": [{
            "delta": {
                "tool_calls": [
                    {"index": i, "id": f"call_{i}", "function": {"name": tc["name"], "arguments": tc["args"]}}
                    for i, tc in enumerate(tool_calls)
                ]
            }
        }]
    }


def make_finish_chunk() -> Dict[str, Any]:
    return {
        "choices": [{"delta": {"content": ""}}]
    }


def install_fake_model(responses: List[List[Dict[str, Any]]]):
    fake = FakeModel(responses)
    original = APIClient.chat
    APIClient.chat = fake.chat
    return fake, original


def restore_model(original):
    APIClient.chat = original


def test_write_file_and_edit():
    """测试 write_file 与 edit_file（多处替换 + replace_all）。"""
    tmp = Path(tempfile.mkdtemp(prefix="sub_agent_test_"))
    try:
        manager = SubAgentManager(str(tmp), str(tmp / "data"))
        terminal = FakeTerminal(tmp)
        manager.set_terminal(terminal)

        # 模型调用序列：先 write_file，再 edit_file 多处替换，最后 finish
        responses = [
            [make_tool_chunk([{
                "name": "write_file",
                "args": json.dumps({"file_path": "demo.txt", "content": "aaa bbb aaa ccc\n"})
            }])],
            [make_tool_chunk([{
                "name": "edit_file",
                "args": json.dumps({
                    "file_path": "demo.txt",
                    "replacements": [
                        {"old_string": "bbb", "new_string": "BBB"},
                        {"old_string": "aaa", "new_string": "AAA", "replace_all": True},
                    ]
                })
            }])],
            [make_tool_chunk([{
                "name": "finish_task",
                "args": json.dumps({"success": True, "summary": "已完成写入与编辑测试"})
            }])],
        ]
        _, original = install_fake_model(responses)
        try:
            result = manager.create_sub_agent(
                agent_id=1,
                summary="写入编辑测试",
                task="测试 write_file 与 edit_file",
                deliverables_dir="deliverables",
                timeout_seconds=30,
                conversation_id="conv-test-1",
                thinking_mode="fast",
            )
            assert result["success"], result
            task_id = result["task_id"]
            final = manager.wait_for_completion(task_id=task_id, timeout_seconds=10)
            assert final["status"] == "completed", final

            content = (tmp / "demo.txt").read_text(encoding="utf-8")
            assert content == "AAA BBB AAA ccc\n", f"内容不符: {content!r}"
            assert (tmp / "deliverables").exists()
        finally:
            restore_model(original)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_read_mediafile():
    """测试 read_mediafile 返回 base64。"""
    tmp = Path(tempfile.mkdtemp(prefix="sub_agent_test_"))
    try:
        manager = SubAgentManager(str(tmp), str(tmp / "data"))
        terminal = FakeTerminal(tmp)
        manager.set_terminal(terminal)

        # 创建一个最小 PNG：1x1 透明像素
        png_bytes = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452"
            "000000010000000108060000001f15c4"
            "890000000d4944415478da6300180000"
            "03010005fe027f0000000049454e44ae"
            "426082"
        )
        (tmp / "pixel.png").write_bytes(png_bytes)

        responses = [
            [make_tool_chunk([{
                "name": "read_mediafile",
                "args": json.dumps({"path": "pixel.png"})
            }])],
            [make_tool_chunk([{
                "name": "finish_task",
                "args": json.dumps({"success": True, "summary": "媒体读取完成"})
            }])],
        ]
        _, original = install_fake_model(responses)
        try:
            result = manager.create_sub_agent(
                agent_id=3,
                summary="媒体测试",
                task="测试 read_mediafile",
                deliverables_dir="deliverables3",
                timeout_seconds=30,
                conversation_id="conv-test-3",
                thinking_mode="fast",
            )
            assert result["success"], result
            final = manager.wait_for_completion(task_id=result["task_id"], timeout_seconds=10)
            assert final["status"] == "completed", final
        finally:
            restore_model(original)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_terminate():
    """测试手动终止子智能体。"""
    tmp = Path(tempfile.mkdtemp(prefix="sub_agent_test_"))
    try:
        manager = SubAgentManager(str(tmp), str(tmp / "data"))
        terminal = FakeTerminal(tmp)
        manager.set_terminal(terminal)

        # 模型先调用一次 run_command，之后返回空内容，使任务持续运行
        responses = [
            [make_tool_chunk([{
                "name": "run_command",
                "args": json.dumps({"command": "sleep 1000", "timeout": 1200})
            }])],
            [make_finish_chunk()],
            [make_finish_chunk()],
            [make_finish_chunk()],
            [make_finish_chunk()],
        ]
        fake, original = install_fake_model(responses)
        try:
            result = manager.create_sub_agent(
                agent_id=4,
                summary="终止测试",
                task="测试终止功能",
                deliverables_dir="deliverables4",
                timeout_seconds=30,
                conversation_id="conv-test-4",
                thinking_mode="fast",
                run_in_background=True,
            )
            assert result["success"], result
            time.sleep(0.5)
            term_result = manager.terminate_sub_agent(task_id=result["task_id"])
            assert term_result["success"], term_result
            # 通过 task_id 查询最终状态
            task = manager.lookup_task(task_id=result["task_id"])
            assert task is not None
            assert task["status"] == "terminated"
        finally:
            restore_model(original)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_all():
    print("=" * 60)
    print("SubAgent Python rewrite regression tests")
    print("=" * 60)
    tests = [
        ("write_file + edit_file", test_write_file_and_edit),
        ("read_mediafile", test_read_mediafile),
        ("terminate", test_terminate),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\nRunning: {name} ...")
        try:
            fn()
            print(f"  ✅ PASSED: {name}")
            passed += 1
        except Exception as exc:
            print(f"  ❌ FAILED: {name} -> {exc}")
            import traceback
            traceback.print_exc()
            failed += 1
    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
