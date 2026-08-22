"""统一入口：复用 app_legacy，便于逐步替换/收敛。"""
import argparse

from .app_legacy import (
    app,
    socketio,
    run_server as _run_server,
    parse_arguments as _parse_arguments,
    initialize_system,
    resource_busy_page,
    DEFAULT_PORT,
)


def parse_arguments():
    """向后兼容的参数解析，默认端口、路径等与 app_legacy 一致。"""
    return _parse_arguments()


def run_server(path: str, thinking_mode: bool = False, port: int = DEFAULT_PORT, debug: bool = False):
    """统一 run_server 入口，便于 future 替换实现。"""
    return _run_server(path=path, thinking_mode=thinking_mode, port=port, debug=debug)


def main():
    args = parse_arguments()
    run_server(
        path=args.path,
        thinking_mode=args.thinking_mode,
        port=args.port,
        debug=args.debug,
    )


__all__ = [
    "app",
    "socketio",
    "run_server",
    "parse_arguments",
    "initialize_system",
    "resource_busy_page",
    "main",
    "DEFAULT_PORT",
]


if __name__ == "__main__":
    main()
