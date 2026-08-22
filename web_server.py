"""
兼容入口（deprecated）：转发到 server.app。
保留旧命令 `python web_server.py`，但推荐使用 `python -m server.app` 或 `python server/app.py`。
"""
import warnings
from server.app import (
    app,
    socketio,
    run_server,
    parse_arguments,
    resource_busy_page,
    DEFAULT_PORT,
)

__all__ = [
    "app",
    "socketio",
    "run_server",
    "parse_arguments",
    "resource_busy_page",
    "DEFAULT_PORT",
]


def _warn_deprecated():
    warnings.warn(
        "web_server.py 已弃用，建议使用 `python -m server.app` 启动（仍向下兼容）。",
        DeprecationWarning,
        stacklevel=2,
    )


if __name__ == "__main__":
    _warn_deprecated()
    args = parse_arguments()
    run_server(
        path=args.path,
        thinking_mode=args.thinking_mode,
        port=args.port,
        debug=args.debug,
    )
