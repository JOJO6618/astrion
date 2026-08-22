"""Web 服务与运行环境配置（单一事实源）。

集中所有"部署/运行"相关配置，避免端口、监听地址、运行二进制路径等散落多处、
各读各的。全项目应统一从 ``config`` 导入这里导出的常量，禁止再硬编码。

解析优先级：环境变量 > 此处默认值；命令行参数（如 ``--port``）可在入口层再覆盖。
"""

import os


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, "") or "").strip() or default


def _flag(name: str, default: str = "0") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


# === Web 服务 ===============================================================
# 监听端口（历史上散落在 main.py / server.app_legacy / server.state / cli）。
WEB_SERVER_PORT = int(_env("WEB_SERVER_PORT", "8091"))
# 监听地址：多用户/服务器用 0.0.0.0；单机便携包建议设 127.0.0.1 只听本机。
WEB_SERVER_HOST = _env("WEB_SERVER_HOST", "0.0.0.0")
# 调试模式（同时控制 Flask reloader）。
WEB_SERVER_DEBUG = _flag("WEB_SERVER_DEBUG", "0")

# === 运行环境二进制 =========================================================
# 便携 release 包启动脚本会注入这两个绝对路径，使主程序与子智能体使用内置运行时。
# 留空/默认时回退到系统 PATH 上的 node（开发环境或系统已安装时）。
NODE_BIN = _env("AGENT_NODE_BIN", "node")
# Python 解释器路径；留空表示使用当前进程的 sys.executable。
PYTHON_BIN = _env("AGENT_PYTHON_BIN", "")


__all__ = [
    "WEB_SERVER_PORT",
    "WEB_SERVER_HOST",
    "WEB_SERVER_DEBUG",
    "NODE_BIN",
    "PYTHON_BIN",
]
