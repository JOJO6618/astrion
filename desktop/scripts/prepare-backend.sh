#!/usr/bin/env bash
# 兼容入口：跨平台实现已收敛到 prepare_backend.py（排除清单单一来源，勿再改这里）。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/prepare_backend.py" "$@"
