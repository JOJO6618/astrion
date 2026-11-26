"""Resolve the Host launch configuration and replace this process with Rust."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]


def _absolute_path(value: str) -> Path:
    path = Path(value).expanduser()
    return ((_ROOT / path) if not path.is_absolute() else path).resolve()


def _backend_binary(environment: dict[str, str]) -> Path:
    name = "astrion-headless.exe" if sys.platform == "win32" else "astrion-headless"
    override = environment.get("ASTRION_RUST_BACKEND_BIN")
    if override:
        candidates = (_absolute_path(override),)
    else:
        target = _absolute_path(environment.get("CARGO_TARGET_DIR") or "backend-rust/target")
        candidates = (target / "release" / name, target / "debug" / name)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError(
        "未找到 Rust 后端。请运行 cargo build --locked --release "
        "--manifest-path backend-rust/Cargo.toml，或配置 ASTRION_RUST_BACKEND_BIN。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Astrion Host Web")
    parser.add_argument("--backend", choices=("rust", "python"), default="rust", help="服务后端，默认 Rust")
    parser.add_argument("--path", help="默认工作区目录")
    parser.add_argument("--port", type=int, help="Web 端口，默认 8091")
    parser.add_argument("--bind", help="Web 监听地址，默认 127.0.0.1")
    parser.add_argument("--thinking-mode", action="store_true", help="新对话默认使用思考模式")
    parser.add_argument("--debug", action="store_true", help="启用诊断输出")
    args = parser.parse_args(argv)
    if args.backend == "python":
        if args.bind is not None:
            os.environ["WEB_SERVER_HOST"] = args.bind
        from config import DEFAULT_PROJECT_PATH, WEB_SERVER_PORT
        from server.app_legacy import run_server

        run_server(
            path=str(_absolute_path(args.path)) if args.path is not None else DEFAULT_PROJECT_PATH,
            thinking_mode=args.thinking_mode,
            port=args.port if args.port is not None else WEB_SERVER_PORT,
            debug=args.debug,
        )
        return 0

    # Reuse the existing Host precedence for the root, settings.json and .env.
    # The configuration package uses the standard library and does not start Flask.
    from config import (
        DATA_DIR, DEFAULT_PROJECT_PATH, DEPLOY_CONFIG_DIR, HOST_WORKSPACES_FILE,
        LOGS_DIR, RUNTIME_ROOT, WEB_SERVER_PORT,
    )

    environment = os.environ.copy()
    resource_root = _absolute_path(environment.get("ASTRION_RUST_RESOURCE_ROOT")
                                  or environment.get("ASTRION_RESOURCE_ROOT") or str(_ROOT))
    static_root = resource_root / "static"
    data_root = Path(RUNTIME_ROOT)
    environment.update(
        ASTRION_DATA_ROOT=str(data_root),
        ASTRION_RESOURCE_ROOT=str(resource_root),
        DEPLOY_CONFIG_DIR=DEPLOY_CONFIG_DIR,
        HOST_WORKSPACES_FILE=HOST_WORKSPACES_FILE,
        LOGS_DIR=LOGS_DIR,
    )
    if args.thinking_mode:
        environment["ASTRION_HOST_THINKING_DEFAULT"] = "1"
    if args.debug:
        environment.setdefault("RUST_BACKTRACE", "1")
    try:
        binary = _backend_binary(environment)
        project = Path(DEFAULT_PROJECT_PATH) if args.path is None else _absolute_path(args.path)
        if not project.is_dir():
            if args.path is not None or environment.get("DEFAULT_PROJECT_PATH"):
                raise ValueError(f"工作区目录不存在：{project}")
            project = _ROOT
        bind = args.bind or environment.get("WEB_SERVER_HOST") or "127.0.0.1"
        bind = "127.0.0.1" if bind == "localhost" else str(ipaddress.ip_address(bind))
        port = WEB_SERVER_PORT if args.port is None else args.port
        manifest = static_root / "dist" / ".vite" / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"未找到前端构建产物 {manifest}。请运行 npm run build。")
        command = [
            str(binary), "--web", "--workspace", str(project), "--data-dir", DATA_DIR,
            "--static-root", str(static_root), "--port", str(port), "--bind", bind,
        ]
        if sys.platform == "win32":
            # Windows cannot replace the process in place with the Unix exec semantics.
            import subprocess

            return subprocess.call(command, env=environment)
        os.execve(binary, command, environment)
    except (OSError, ValueError) as error:
        print(f"Astrion Host: {error}", file=sys.stderr)
        return 1
