#!/usr/bin/env bash
# 打包前准备：把后端源码 staging 到 desktop/src-tauri/runtime/backend/
#
# 原则：
# - 目录结构与源码树一致（Flask 静态目录、import 路径依赖相对布局）
# - 排除一切私有/部署级配置（.env、custom_models.json 等）——全新用户
#   必须走「部署目录无配置 → .example 种子/空注册表」的干净初始态
# - 用内嵌 Python 预编译 pyc（.app 内只读，运行时 PYTHONDONTWRITEBYTECODE=1 读现成）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUNTIME_DIR="$REPO_ROOT/desktop/src-tauri/runtime"
BACKEND_DIR="$RUNTIME_DIR/backend"
PYBIN="$RUNTIME_DIR/python/bin/python3.12"

if [ ! -x "$PYBIN" ]; then
  echo "[prepare-backend] 内嵌 Python 不存在: $PYBIN" >&2
  echo "[prepare-backend] 请先完成 python-build-standalone 运行时就位" >&2
  exit 1
fi

echo "[prepare-backend] 清理旧 staging: $BACKEND_DIR"
rm -rf "$BACKEND_DIR"
mkdir -p "$BACKEND_DIR"

# 1) 源码与程序级资源（保持相对结构）
ITEMS=(server core modules utils config prompts agentskills multi_agent_roles)
for item in "${ITEMS[@]}"; do
  rsync -a \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    "$REPO_ROOT/$item" "$BACKEND_DIR/"
done

# 2) 前端构建产物（Flask 同源 serve）
mkdir -p "$BACKEND_DIR/static"
rsync -a --delete "$REPO_ROOT/static/dist" "$BACKEND_DIR/static/"
# favicon 等 static 根散文件
rsync -a --exclude 'dist' --exclude 'src' --exclude 'icons/providers' \
  "$REPO_ROOT/static/" "$BACKEND_DIR/static/" 2>/dev/null || true
# 提供商图标（设置页需要）
mkdir -p "$BACKEND_DIR/static/icons"
rsync -a "$REPO_ROOT/static/icons/providers" "$BACKEND_DIR/static/icons/"

# 3) 私有/部署级配置剔除（打包卫生，防泄露 + 保证全新初始态）
rm -f "$BACKEND_DIR/.env"
rm -f "$BACKEND_DIR/config/custom_models.json" \
      "$BACKEND_DIR/config/host_workspaces.json" \
      "$BACKEND_DIR/config/auto_approval.json" \
      "$BACKEND_DIR/config/goal_review.json" \
      "$BACKEND_DIR/config/forbidden_commands.json" \
      "$BACKEND_DIR/config/host_sandbox_policy.json"
# custom_models.json.example 是开发者写法示例（含 Kimi-K3/deepseek-chat 示例条目），
# 随包分发会让全新用户经回退链（部署目录→源码树.json→.example）预装示例模型。
# 删除后回退链落空，模型注册表为干净空态（加载器对缺失文件返回 {}）。
# host_sandbox_policy.json.example 是有益安全种子（禁读 ~/.ssh 等），保留。
rm -f "$BACKEND_DIR/config/custom_models.json.example"

# 4) 预编译 pyc（用内嵌解释器，magic 版本一致）
echo "[prepare-backend] 预编译 pyc..."
"$PYBIN" -m compileall -q -f "$BACKEND_DIR/server" "$BACKEND_DIR/core" \
  "$BACKEND_DIR/modules" "$BACKEND_DIR/utils" "$BACKEND_DIR/config" || true

echo "[prepare-backend] staging 完成: $(du -sh "$BACKEND_DIR" | cut -f1)"
