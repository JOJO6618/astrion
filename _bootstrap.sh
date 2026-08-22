#!/usr/bin/env bash
# 共享引导逻辑：被 setup.sh / start.sh 引用（source）。
# 负责定位项目根、准备 Python venv 与依赖、准备 Node 依赖。
# 不直接执行；由入口脚本 source 后调用其中的函数。

# 出错即停；管道任一环节失败也算失败。
set -euo pipefail

# 项目根目录 = 本脚本所在目录。
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"

# 选择系统 Python：优先 python3，回退 python。
_pick_system_python() {
    if command -v python3 >/dev/null 2>&1; then
        echo "python3"
    elif command -v python >/dev/null 2>&1; then
        echo "python"
    else
        echo ""
    fi
}

# 准备虚拟环境与 Python 依赖（依赖系统已装 Python，不内置解释器）。
# 已存在 venv 则复用；缺失则创建并安装 requirements.lock.txt（优先）或 requirements.txt。
ensure_python_env() {
    if [ ! -x "$VENV_PY" ]; then
        local sys_py
        sys_py="$(_pick_system_python)"
        if [ -z "$sys_py" ]; then
            echo "[error] 未找到系统 Python（需要 python3 或 python）。请先安装 Python 3.9+。" >&2
            return 1
        fi
        echo "[setup] 使用 $sys_py 创建虚拟环境：$VENV_DIR"
        "$sys_py" -m venv "$VENV_DIR"
    fi

    echo "[setup] 升级 pip 并安装依赖..."
    "$VENV_PY" -m pip install --upgrade pip >/dev/null
    if [ -f "$ROOT/requirements.lock.txt" ]; then
        "$VENV_PY" -m pip install -r "$ROOT/requirements.lock.txt"
    elif [ -f "$ROOT/requirements.txt" ]; then
        "$VENV_PY" -m pip install -r "$ROOT/requirements.txt"
    else
        echo "[error] 找不到 requirements.lock.txt 或 requirements.txt" >&2
        return 1
    fi
}

# 准备 Node 依赖（依赖系统已装 Node；不内置 Node）。
# easyagent 仅需运行时依赖；前端需要构建产物（vite build）。
ensure_node_env() {
    if ! command -v node >/dev/null 2>&1; then
        echo "[warn] 未找到系统 Node。子智能体（easyagent）与前端构建将不可用。" >&2
        echo "       如需完整功能，请安装 Node.js 18+ 后重跑。" >&2
        return 0
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "[warn] 找到 node 但未找到 npm，跳过 Node 依赖安装。" >&2
        return 0
    fi

    # easyagent 运行时依赖
    if [ -f "$ROOT/easyagent/package.json" ] && [ ! -d "$ROOT/easyagent/node_modules" ]; then
        echo "[setup] 安装 easyagent 依赖（npm ci）..."
        ( cd "$ROOT/easyagent" && npm ci )
    fi

    # 前端构建产物（static/dist 之类）。仅在缺失时构建，避免每次启动都跑。
    if [ -f "$ROOT/package.json" ] && [ ! -d "$ROOT/node_modules" ]; then
        echo "[setup] 安装前端依赖并构建（npm ci && npm run build）..."
        ( cd "$ROOT" && npm ci && npm run build )
    fi
}

# .env 是否存在（首次启动判断依据）。
has_env_file() {
    [ -f "$ROOT/.env" ]
}
