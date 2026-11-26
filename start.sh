#!/usr/bin/env bash
# 启动 Astrion Host Web。首次配置可运行 ./setup.sh。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_bootstrap.sh
source "$ROOT/_bootstrap.sh"

cd "$ROOT"
PYTHON="$VENV_PY"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(_pick_system_python)"
    if [ -z "$PYTHON" ]; then
        echo "[start] 未找到 Python 3，请安装后重试。" >&2
        exit 1
    fi
fi

# 已通过 settings.json 或环境配置的部署可以直接使用现有数据根。
case "${ASTRION_IGNORE_DOTENV:-}:${1:-}" in
    1:*|true:*|yes:*|on:*|*:--help|*:-h) ;;
    *)
        SETTINGS_ROOT="${ASTRION_DATA_ROOT:-$HOME/.astrion/astrion}"
        if ! has_env_file && [ ! -f "$SETTINGS_ROOT/settings.json" ] && [ -z "${ASTRION_DATA_ROOT:-}${DATA_DIR:-}" ]; then
            ensure_python_env
            "$VENV_PY" -m scripts.setup
            if ! has_env_file; then
                echo "[start] 初始化未完成，未生成 .env。" >&2
                exit 1
            fi
            PYTHON="$VENV_PY"
        fi
        ;;
esac

exec "$PYTHON" -m server.app "$@"
