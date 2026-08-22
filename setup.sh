#!/usr/bin/env bash
# 首次初始化入口：准备运行环境，然后运行交互式配置向导。
#
# 做的事：
#   1. 创建/复用 Python 虚拟环境并安装依赖
#   2. 准备 Node 依赖（easyagent / 前端，依赖系统已装 Node）
#   3. 运行 python -m scripts.setup 交互式向导，写出 .env 与模型配置
#
# 用法：
#   ./setup.sh            # 首次初始化（已存在 .env 时向导会提示备份后重配）
#   ./setup.sh --force    # 跳过『已存在 .env』确认（仍会备份）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_bootstrap.sh
source "$ROOT/_bootstrap.sh"

echo "========================================"
echo "  AI Agent 初始化"
echo "========================================"

ensure_python_env
ensure_node_env

echo ""
echo "[setup] 启动配置向导..."
exec "$VENV_PY" -m scripts.setup "$@"
