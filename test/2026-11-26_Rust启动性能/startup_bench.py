#!/usr/bin/env python3.11
"""macOS arm64 本地启动基准：Python headless vs Rust headless，隔离合成旧数据。

测什么
------
从 `spawn` 到**同一就绪端点**首次返回 HTTP 200 的墙钟时间：`GET /api/health`
（两个入口都注册；Python `server/status/base.py:66`，Rust `host_web.rs:107`）。
多轮交替执行，全程串行：前一个进程退出、端口关闭后才起下一个，不并行。

沙箱约束
--------
本机沙箱禁止父进程向子进程发任何信号（SIGTERM / SIGKILL 都返回 EPERM）。
因此停止方式避开信号：
- Rust：产品自带 `ASTRION_DESKTOP_STOP_STDIN=1`，stdin 收到 EOF 即优雅退出；
- Python：用本目录 `py_bootstrap.py` 的停止文件 `/` os._exit 自杀退出（不改产品代码）。

不测什么
--------
- 不是安装包/发行包性能：Rust 是 **debug** 二进制，Python 是 Homebrew 解释器 + 仓库源码；
- 不接触真实数据（数据由 make_dataset.py 合成）；
- 不申请提权、不改产品代码、不改默认后端。

用法
----
python3.11 startup_bench.py --root <数据根> --rounds 7 --label small
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_closed(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.02)
    return False


def probe(port: int, path: str, token: str | None = None) -> tuple[int | None, dict | None]:
    """返回 (HTTP 状态码, 解析后的 JSON)；连接层未就绪时状态码为 None。

    带 `Authorization: Bearer <host_api_token>` 访问真实业务端点：
    - 只有状态码 200 且 JSON 含业务字段才算就绪（由调用方判定）。
    """
    import http.client
    import json

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        conn.request("GET", path, headers=headers)
        response = conn.getresponse()
        body = response.read()
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = None
        return response.status, payload
    except (ConnectionError, OSError, TimeoutError):
        return None, None
    finally:
        conn.close()


def read_token(token_file: Path) -> str | None:
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None


def rss_kb(pid: int) -> int | None:
    """读子进程 RSS（KB）。沙箱禁 ps，改用 libproc 的 proc_pidinfo(PROC_PIDTASKINFO)。

    proc_taskinfo 布局：pti_virtual_size(0), pti_resident_size(8) 均为 uint64。
    """
    try:
        import ctypes
        import ctypes.util

        lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
        lib.proc_pidinfo.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(256)
        size = lib.proc_pidinfo(pid, 4, 0, buf, ctypes.sizeof(buf))
        if not size:
            return None
        resident = ctypes.c_uint64.from_buffer(buf, 8).value
        return int(resident // 1024)
    except Exception:
        return None


def base_env(root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ASTRION_", "AGENT_", "WEB_"))}
    env.update(
        DATA_DIR=str(root / "host" / "data"),
        LOGS_DIR=str(root / "host" / "logs"),
        DEPLOY_CONFIG_DIR=str(root / "config"),
        HOST_WORKSPACES_FILE=str(root / "config" / "host_workspaces.json"),
        ASTRION_DATA_ROOT=str(root),
        ASTRION_RESOURCE_ROOT=str(REPO),
        ASTRION_IGNORE_DOTENV="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return env


def launch_python(python: str, root: Path, port: int, stop_file: Path) -> dict:
    return {
        "command": [
            python,
            "-B",
            str(HERE / "py_bootstrap.py"),
            str(stop_file),
            "server.headless_app",
            "--port",
            str(port),
            "--path",
            str(root / "ws"),
        ],
        "cwd": str(REPO),
        "env": base_env(root),
        "stdin": subprocess.DEVNULL,
    }


def launch_rust(binary: Path, root: Path, port: int) -> dict:
    env = base_env(root)
    env["ASTRION_DESKTOP_STOP_STDIN"] = "1"
    return {
        "command": [
            str(binary),
            "--workspace",
            str(root / "ws"),
            "--data-dir",
            str(root / "host" / "data"),
            "--port",
            str(port),
        ],
        "cwd": str(REPO),
        "env": env,
        "stdin": subprocess.PIPE,
    }


def run_once(
    kind: str,
    spec: dict,
    root: Path,
    timeout: float,
    probe_path: str,
    expect_total: int | None = None,
) -> dict:
    port = free_port()
    stop_file = HERE / ".tmp" / f"stop-{kind}-{port}.flag"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.unlink(missing_ok=True)
    launch = (
        launch_python(spec["python"], root, port, stop_file)
        if kind == "python"
        else launch_rust(spec["rust"], root, port)
    )

    log_dir = HERE / ".tmp" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{kind}-{time.strftime('%H%M%S')}-{port}.log"
    log = log_path.open("wb")

    started = time.perf_counter()
    proc = subprocess.Popen(
        launch["command"],
        cwd=launch["cwd"],
        env=launch["env"],
        stdin=launch["stdin"],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    ready_ms = None
    ready_status = None
    ready_body = None
    rss_at_ready = None
    error = None
    token_file = root / "host" / "data" / "host_api_token"
    deadline = started + timeout
    try:
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                error = f"exit {proc.returncode} before ready"
                break
            token = read_token(token_file)
            status, payload = probe(port, probe_path, token)
            # 就绪 = 真实业务端点 200 且 JSON 业务字段成立
            if status == 200 and payload is not None:
                list_key = None
                items = None
                if isinstance(payload, dict):
                    for candidate in ("conversations", "items", "sessions"):
                        value = payload.get(candidate)
                        if isinstance(value, list):
                            list_key, items = candidate, value
                            break
                if isinstance(payload, dict) and payload.get("success") is True and items is not None:
                    total = payload.get("total")
                    # 可选：逐轮对总数做硬校验（--expect-total）
                    if expect_total is not None and total != expect_total:
                        error = f"total mismatch: got {total!r}, expect {expect_total}"
                        break
                    ready_ms = (time.perf_counter() - started) * 1000
                    ready_status = status
                    ready_body = {
                        "keys": sorted(payload.keys()),
                        "list_key": list_key,
                        "items": len(items),
                        "total": payload.get("total"),
                        "has_token": bool(token),
                    }
                    rss_at_ready = rss_kb(proc.pid)
                    break
                # 端点已应答但不是预期业务体：立即失败，不白等超时
                error = f"http 200 but unexpected body keys: {sorted(payload)[:12]}"
                break
            time.sleep(0.01)
        else:
            error = "timeout"
    finally:
        # 无信号停止：Rust 关 stdin；Python 落停止文件
        try:
            if kind == "rust" and proc.stdin:
                proc.stdin.close()
            else:
                stop_file.write_text("stop", encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            error = (error + f"; stop failed: {exc}").strip("; ") if error else f"stop failed: {exc}"
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            error = (error + "; child did not exit").strip("; ") if error else "child did not exit"
        log.close()
        stop_file.unlink(missing_ok=True)
        if not port_closed(port, timeout=5):
            error = (error + "; port still open").strip("; ") if error else "port still open"

    return {
        "kind": kind,
        "ready_ms": round(ready_ms, 1) if ready_ms is not None else None,
        "ready_status": ready_status,
        "ready_body": ready_body,
        "rss_kb": rss_at_ready,
        "error": error,
        "log": str(log_path),
    }


def p50(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120.0, help="单次就绪超时秒数")
    parser.add_argument("--probe", default="/api/runtime/sessions")
    parser.add_argument(
        "--expect-total",
        type=int,
        default=None,
        help="逐轮硬校验 JSON total 等于该值（不给则只校验业务结构）",
    )
    parser.add_argument("--label", default="dataset")
    parser.add_argument("--python", default="/opt/homebrew/bin/python3.11")
    parser.add_argument(
        "--rust",
        default=str(REPO / "backend-rust" / "target-master" / "debug" / "astrion-headless"),
        help="默认用主侧 target-master/debug 构建",
    )
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    spec = {"python": args.python, "rust": Path(args.rust)}
    if not Path(spec["python"]).is_file():
        print(f"python 不存在: {spec['python']}", file=sys.stderr)
        return 2
    if not spec["rust"].is_file():
        print(f"rust 二进制不存在: {spec['rust']}", file=sys.stderr)
        return 2

    import hashlib

    rust_bytes = spec["rust"].read_bytes()
    rust_meta = {
        "path": str(spec["rust"]),
        "size": len(rust_bytes),
        "sha256": hashlib.sha256(rust_bytes).hexdigest(),
    }

    runs: list[dict] = []
    for kind in ("python", "rust"):
        for _ in range(args.warmup):
            warm = run_once(kind, spec, root, args.timeout, args.probe, args.expect_total)
            if warm["error"]:
                print(json.dumps({"warmup": kind, "failed": warm}, ensure_ascii=False), file=sys.stderr)
                return 3

    for round_index in range(args.rounds):
        order = ("python", "rust") if round_index % 2 == 0 else ("rust", "python")
        for kind in order:
            result = run_once(kind, spec, root, args.timeout, args.probe, args.expect_total)
            result["round"] = round_index + 1
            result["order"] = "->".join(order)
            runs.append(result)
            print(
                f"[{args.label}] round {round_index + 1} {kind:6s} "
                f"ready={result['ready_ms']} ms rss={result['rss_kb']} KB error={result['error']}",
                flush=True,
            )
            if result["error"]:
                print(f"log: {result['log']}", file=sys.stderr)
                return 4

    summary = {}
    for kind in ("python", "rust"):
        values = [r["ready_ms"] for r in runs if r["kind"] == kind and r["ready_ms"]]
        rss_values = [r["rss_kb"] for r in runs if r["kind"] == kind and r["rss_kb"]]
        summary[kind] = {
            "runs": len(values),
            "p50_ms": p50(values),
            "min_ms": round(min(values), 1) if values else None,
            "max_ms": round(max(values), 1) if values else None,
            "all_ms": values,
            "p50_rss_kb": p50(rss_values),
            "all_rss_kb": rss_values,
            "ready_status": [r["ready_status"] for r in runs if r["kind"] == kind],
            "first_ready_body": next(
                (r["ready_body"] for r in runs if r["kind"] == kind and r["ready_body"]), None
            ),
        }

    payload = {
        "label": args.label,
        "root": str(root),
        "probe": args.probe,
        "expect_total": args.expect_total,
        "python": args.python,
        "rust": str(spec["rust"]),
        "rust_meta": rust_meta,
        "rounds": args.rounds,
        "warmup": args.warmup,
        "summary": summary,
        "runs": runs,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"label": args.label, "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
