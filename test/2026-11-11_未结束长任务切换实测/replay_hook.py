"""启动后端副本：业务代码不改，只在进程内加回放与计时探针。

- 工具执行（execute_with_authority）改为返回原记录的 tool_payload（避免真实副作用）；
- define_tools 补齐原记录用过的工具名，避免白名单把它们拒掉；
- WSGI 中间件记录每个请求的耗时与响应字节；
- VIEW_LOCK 换成计时 RLock，记录等待/持有；
- 心跳线程测量解释器调度延迟（GIL 争用的直接指标）；
- 有慢请求在途时，按间隔采样全部线程调用栈。
用法：python launch_backend.py <WORK> <port>
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

WORK = Path(os.environ["REPLAYX_WORK"]).resolve()
OUT = Path(os.environ["REPLAYX_OUT"]).resolve()
OUT.mkdir(exist_ok=True)

_write_lock = threading.Lock()
_files = {}


def emit(name: str, **row):
    row.setdefault("t", time.time())
    with _write_lock:
        handle = _files.get(name)
        if handle is None:
            handle = _files[name] = open(OUT / f"{name}.jsonl", "a", encoding="utf-8")
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()


# ---- VIEW_LOCK 计时 ----
import server.conversation_view.snapshots as snapshots  # noqa: E402


class TimedRLock:
    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()

    def _site(self):
        frame = sys._getframe(2)
        names = []
        while frame is not None and len(names) < 4:
            code = frame.f_code
            if "snapshots.py" not in code.co_filename or code.co_name != "wrapped":
                names.append(f"{Path(code.co_filename).name}:{code.co_name}")
            frame = frame.f_back
        return " < ".join(names)

    def acquire(self, blocking=True, timeout=-1):
        depth = getattr(self._local, "depth", 0)
        start = time.perf_counter()
        ok = self._lock.acquire(blocking, timeout)
        if ok:
            if depth == 0:
                self._local.wait = time.perf_counter() - start
                self._local.start = time.perf_counter()
                self._local.site = self._site()
            self._local.depth = depth + 1
        return ok

    def release(self):
        self._local.depth -= 1
        if self._local.depth == 0:
            hold = time.perf_counter() - self._local.start
            wait = self._local.wait
            if wait > 0.02 or hold > 0.05:
                emit("view_lock", site=self._local.site, wait_ms=round(wait * 1000, 2),
                     hold_ms=round(hold * 1000, 2), thread=threading.current_thread().name)
        self._lock.release()

    __enter__ = lambda self: self.acquire()

    def __exit__(self, *exc):
        self.release()


snapshots.VIEW_LOCK = TimedRLock()

# ---- 业务代码导入 ----
import server.app_legacy as server_app  # noqa: E402
import server.chat_flow_tool_loop as tool_loop  # noqa: E402
from core.web_terminal import WebTerminal  # noqa: E402

assert server_app is not None

# ---- 工具回放 ----
SCRIPT = json.loads((WORK / "replay_script.json").read_text(encoding="utf-8"))
REPLAY = {}
NAMES = set()


def _key(name, arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            pass
    return name + "\x00" + json.dumps(arguments, ensure_ascii=False, sort_keys=True)


BY_NAME = {}
for turn in SCRIPT["turns"]:
    for call in turn["tool_calls"]:
        NAMES.add(call["name"])
        entry = {"used": False, "payload": SCRIPT["tool_payloads"].get(call["id"], {"success": True, "output": ""})}
        REPLAY.setdefault(_key(call["name"], call["arguments"]), deque()).append(entry)
        BY_NAME.setdefault(call["name"], deque()).append(entry)


def _take(queue):
    while queue:
        entry = queue.popleft()
        if not entry["used"]:
            entry["used"] = True
            return entry
    return None


async def replay_execute(terminal, tool_name, arguments, scope, stop_check=None):
    # 先按参数精确匹配；程序可能在执行前规范化参数，退回同名工具的原始顺序。
    entry = _take(REPLAY.get(_key(tool_name, arguments)) or deque())
    match = "exact"
    if entry is None:
        entry = _take(BY_NAME.get(tool_name) or deque())
        match = "by_name"
    if entry is None:
        payload, match = {"success": True, "output": "(replay: no recorded result)"}, "miss"
    else:
        payload = entry["payload"]
    emit("tool", tool=tool_name, match=match, bytes=len(json.dumps(payload, ensure_ascii=False)))
    return json.dumps(payload, ensure_ascii=False)


tool_loop.execute_with_authority = replay_execute
_original_define_tools = WebTerminal.define_tools


def define_tools(self, *args, **kwargs):
    tools = list(_original_define_tools(self, *args, **kwargs) or [])
    have = {((t or {}).get("function") or {}).get("name") for t in tools}
    for name in sorted(NAMES - have):
        tools.append({"type": "function", "function": {
            "name": name, "description": "replayed tool", "parameters": {"type": "object", "properties": {}}}})
    return tools


WebTerminal.define_tools = define_tools

# ---- 请求计时 ----
INFLIGHT = {}
_inflight_lock = threading.Lock()


class Timing:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        query = environ.get("QUERY_STRING", "")
        method = environ.get("REQUEST_METHOD")
        start = time.perf_counter()
        wall = time.time()
        status = {}
        ident = threading.get_ident()
        with _inflight_lock:
            INFLIGHT[ident] = (path, start)

        def capture(code, headers, exc_info=None):
            status["code"] = code.split(" ")[0]
            status["first_ms"] = round((time.perf_counter() - start) * 1000, 2)
            return start_response(code, headers, exc_info)

        size = 0
        try:
            for chunk in self.app(environ, capture):
                size += len(chunk)
                yield chunk
        finally:
            with _inflight_lock:
                INFLIGHT.pop(ident, None)
            if not path.startswith("/assets") and not path.startswith("/static"):
                emit("requests", start=wall, method=method, path=path, query=query[:120],
                     status=status.get("code"), handler_ms=status.get("first_ms"),
                     total_ms=round((time.perf_counter() - start) * 1000, 2), bytes=size,
                     thread=threading.current_thread().name)


server_app.app.wsgi_app = Timing(server_app.app.wsgi_app)


# ---- 解释器调度延迟（心跳）与慢请求栈采样 ----
def heartbeat():
    window = []
    last_flush = time.time()
    while True:
        start = time.perf_counter()
        time.sleep(0.01)
        window.append((time.perf_counter() - start - 0.01) * 1000)
        if time.time() - last_flush >= 1:
            window.sort()
            emit("heartbeat", n=len(window), p50_ms=round(window[len(window) // 2], 2),
                 max_ms=round(window[-1], 2), threads=threading.active_count())
            window, last_flush = [], time.time()


def sampler():
    names = {}
    while True:
        time.sleep(0.5)
        now = time.perf_counter()
        with _inflight_lock:
            slow = {k: v for k, v in INFLIGHT.items() if now - v[1] > 1.5}
        if not slow:
            continue
        for thread in threading.enumerate():
            names[thread.ident] = thread.name
        frames = sys._current_frames()
        stacks = {}
        for ident, frame in frames.items():
            summary = traceback.extract_stack(frame)[-14:]
            text = [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in summary]
            if any("selectors.py" in x or "threading.py:3" in x and "wait" in x for x in text[-2:]):
                continue
            stacks[names.get(ident, str(ident))] = text
        emit("stacks", slow={names.get(k, str(k)): [p, round(now - s, 2)] for k, (p, s) in slow.items()},
             stacks=stacks)


threading.Thread(target=heartbeat, name="probe-heartbeat", daemon=True).start()
threading.Thread(target=sampler, name="probe-sampler", daemon=True).start()
emit("events", kind="hook-loaded", replay_names=sorted(NAMES))


# ---- 快进：去掉任务流水线里的固定等待（每工具最少 1.5s、0.1/0.3s 节拍等），其余逻辑与持久化照常 ----
if os.environ.get("REPLAYX_FAST"):
    import asyncio as _asyncio
    import server.chat_flow_task_main as _task_main
    import server.chat_flow_stream_loop as _stream_loop

    class _FastAsyncio:
        def __getattr__(self, name):
            return getattr(_asyncio, name)

        @staticmethod
        async def sleep(delay, *args, **kwargs):
            await _asyncio.sleep(0 if delay <= 2 else delay, *args, **kwargs)

    for _module in (tool_loop, _task_main, _stream_loop):
        if hasattr(_module, "asyncio"):
            _module.asyncio = _FastAsyncio()
    emit("events", kind="fast-mode")
