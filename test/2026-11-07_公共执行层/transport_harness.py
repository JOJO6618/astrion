"""test_transport 的客户端与断言工具：起探针子进程、按 id 收发帧。

从 test_transport.py 拆出，纯粹为了让单文件保持在 500 行以内；语义未变：
- 子进程 cwd 故意不是 workspace；
- `wait()` 只按 id 匹配响应，通知单独缓存；
- `SkipCase` 表示“后端不可用”，不是通过。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("probe_transport.py")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
BACKEND_UNAVAILABLE = -32010
CONTRACT_MISSING = -32011


class SkipCase(Exception):
    """后端尚未落地时的显式跳过（不是通过）。"""


class ProbeClient:
    """极简同步客户端：一行一帧，按 id 匹配响应，通知单独缓存。"""

    def __init__(self, workspace: Path, data_dir: Path, control_cwd: Path,
                 execution_mode: str = "sandbox") -> None:
        env = os.environ.copy()
        env["ASTRION_IGNORE_DOTENV"] = "1"          # 禁真实配置加载
        env["PYTHONDONTWRITEBYTECODE"] = "1"        # 不在业务目录写 __pycache__
        env["PYTHONUNBUFFERED"] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(ROOT) + ((os.pathsep + existing) if existing else "")

        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                str(PROBE),
                "--workspace",
                str(workspace),
                "--data-dir",
                str(data_dir),
                "--execution-mode",
                execution_mode,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(control_cwd),   # 关键：子进程 cwd 故意不是 workspace
            env=env,
        )
        self._frames: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stderr: List[str] = []
        self.notifications: List[Dict[str, Any]] = []
        self._next_id = 0
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---- IO ----
    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                self._frames.put(json.loads(line))
            except Exception as exc:
                self._frames.put({"__malformed__": line, "__error__": repr(exc)})
        self._frames.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def send_raw(self, payload: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(payload)
        self.proc.stdin.flush()

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> int:
        self._next_id += 1
        req_id = self._next_id
        frame = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        self.send_raw((json.dumps(frame, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
        return req_id

    def wait(self, req_id: int, timeout: float = 30.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise AssertionError("timeout waiting for response id=%s" % req_id)
            try:
                frame = self._frames.get(timeout=remain)
            except queue.Empty:
                raise AssertionError("timeout waiting for response id=%s" % req_id)
            if frame is None:
                raise AssertionError(
                    "probe exited before responding (id=%s); stderr tail:\n%s"
                    % (req_id, "\n".join(self._stderr[-20:]))
                )
            if "__malformed__" in frame:
                raise AssertionError("probe emitted a non-JSON frame: %r" % frame["__malformed__"])
            if "id" not in frame:
                self.notifications.append(frame)
                continue
            if frame.get("id") == req_id:
                return frame
            # 其他 id 的响应（并发场景）暂存，避免丢帧
            self._frames.put(frame)

    def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                timeout: float = 30.0) -> Dict[str, Any]:
        return self.wait(self.send(method, params), timeout=timeout)

    def wait_notification(self, method: str, timeout: float = 10.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        for frame in list(self.notifications):
            if frame.get("method") == method:
                return frame
        while time.time() < deadline:
            try:
                frame = self._frames.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            if frame is None:
                break
            if "id" not in frame:
                self.notifications.append(frame)
                if frame.get("method") == method:
                    return frame
            else:
                self._frames.put(frame)
        raise AssertionError("notification %r not received" % method)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()

    @property
    def stderr_text(self) -> str:
        return "\n".join(self._stderr)


class Ctx:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspace = root / "workspace"
        self.data_dir = root / "data"
        self.control = root / "control"
        for p in (self.workspace, self.data_dir, self.control):
            p.mkdir(parents=True, exist_ok=True)
        self.client = ProbeClient(self.workspace, self.data_dir, self.control, "sandbox")
        self.ready = self.client.wait_notification("ready")
        self.info = self.client.request("env.info")
        self.backend_available = bool(self.ready["params"]["backend_available"])
        # direct 模式探针按需创建：用于区分「代码错」与「宿主禁止嵌套 seatbelt」
        self._direct_client: Optional[ProbeClient] = None
        self._direct_ready: Optional[Dict[str, Any]] = None
        self.sandbox_exec_blocked = False
        self.sandbox_exec_detail = ""

    def require_backend(self) -> None:
        if not self.backend_available:
            detail = self.ready["params"].get("backend_error") or "unknown"
            raise SkipCase("LocalExecutionBackend 不可用：%s" % detail)

    @property
    def direct_client(self) -> ProbeClient:
        if self._direct_client is None:
            self._direct_client = ProbeClient(
                self.workspace, self.data_dir, self.control, "direct"
            )
            self._direct_ready = self._direct_client.wait_notification("ready")
        return self._direct_client

    @property
    def exec_client(self) -> ProbeClient:
        """命令类用例用的探针。

        宿主若禁止嵌套 seatbelt（本测试进程自身已在沙箱内），sandbox 模式无法
        执行任何命令，此时回退到 direct 探针——它验证的仍是「命令在哪里跑」，
        只是不验证沙箱约束本身。
        """
        return self.direct_client if self.sandbox_exec_blocked else self.client

    def control_listing(self) -> List[str]:
        return sorted(p.name for p in self.control.iterdir())

    def close(self) -> None:
        for client in (self._direct_client, self.client):
            if client is not None:
                client.close()


def expect(cond: bool, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def expect_ok(resp: Dict[str, Any], label: str) -> Dict[str, Any]:
    expect("error" not in resp, "%s 返回错误：%s" % (label, json.dumps(resp.get("error"), ensure_ascii=False)))
    return resp["result"]




__all__ = [
    "ROOT",
    "PROBE",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "BACKEND_UNAVAILABLE",
    "CONTRACT_MISSING",
    "SkipCase",
    "ProbeClient",
    "Ctx",
    "expect",
    "expect_ok",
]
