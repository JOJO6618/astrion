#!/usr/bin/env bash
# 启动入口：准备运行环境（按需），首次运行自动跑配置向导，然后启动 Web 服务。
#
# 做的事：
#   1. 确保 Python venv 与依赖就绪（缺失才安装）
#   2. 确保 Node 依赖就绪（缺失才安装）
#   3. 若没有 .env（首次启动），自动运行配置向导
#   4. 启动 python -m server.app
#
# 端口/监听地址/模式等由 .env 决定（见 config/server.py、config/paths.py）。
# 透传的命令行参数会传给 server.app（如 --port / --path / --thinking-mode）。
#
# 用法：
#   ./start.sh                       # 正常启动（首次会自动初始化）
#   ./start.sh --thinking-mode       # 透传参数给 server.app

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_bootstrap.sh
source "$ROOT/_bootstrap.sh"

# 1+2) 环境就绪（已存在则很快跳过）
ensure_python_env
ensure_node_env

# 3) 首次启动：没有 .env 则先跑向导
if ! has_env_file; then
    echo ""
    echo "[start] 未检测到 .env，进入首次初始化向导..."
    "$VENV_PY" -m scripts.setup
    if ! has_env_file; then
        echo "[start] 初始化未完成（未生成 .env），已退出。" >&2
        exit 1
    fi
fi

# 4) 启动服务
echo ""
echo "[start] 启动 Web 服务..."
cd "$ROOT"
exec "$VENV_PY" -m server.app "$@"
