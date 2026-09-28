#!/usr/bin/env bash
# Astrion 桌面端（Electron 壳）macOS 半一键发布：
#   同步版本号 → 构建前端 → electron-builder（签名 dmg + zip + latest-mac.yml）
#   → 上传服务器 → 重新生成更新清单
#
# 前置条件：
#   1. 代码签名证书 "Astrion Local Sign" 在钥匙串中（同 Tauri 壳；
#      electron-builder 经 electron-builder.yml mac.identity 引用）
#   2. 服务器 SSH 免密（同 Tauri 发布脚本）
#   3. 更新日志 desktop-electron/DESKTOP_CHANGELOG.md 顶部已写好本版本小节
#
# 用法：bash desktop-electron/scripts/release_mac.sh [版本号]
#   缺省读 package.json 的 version；显式传入时同步写回 package.json。
#
# 与 Tauri 链路的差异：
#   - 更新验签走 Apple 代码签名身份匹配，不再需要 minisign 私钥环境变量；
#   - 产物 = dmg（安装）+ <ver>-arm64-mac.zip（updater 包，文件名自带版本号，无需重命名）
#     + latest-mac.yml（electron-updater 清单）；
#   - 服务器 regen_manifest.py 需识别 latest-mac.yml（与 Tauri 的 latest-*.json 并存）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DESKTOP_DIR="$REPO_ROOT/desktop-electron"
PKG="$DESKTOP_DIR/package.json"
CHANGELOG="$DESKTOP_DIR/DESKTOP_CHANGELOG.md"
DEST="59.110.19.30:/var/www/astrion/downloads/"
DIST_DIR="$DESKTOP_DIR/dist"

# ── 版本号：显式参数 > package.json ──
if [ $# -ge 1 ]; then
  VERSION="$1"
  python3 - "$PKG" "$VERSION" <<'PYEOF'
import json, sys
pkg_path, version = sys.argv[1], sys.argv[2]
pkg = json.load(open(pkg_path))
pkg["version"] = version
json.dump(pkg, open(pkg_path, "w"), ensure_ascii=False, indent=2)
open(pkg_path, "a").write("\n")
PYEOF
else
  VERSION="$(python3 -c "import json; print(json.load(open('$PKG'))['version'])")"
fi
echo "==> 发布版本: $VERSION"

# ── 前置检查 ──
[ -f "$CHANGELOG" ] || { echo "!! 缺少更新日志: $CHANGELOG"; exit 1; }
[ -d "$DESKTOP_DIR/node_modules/electron" ] || { echo "!! 未安装依赖，先运行: cd desktop-electron && npm install"; exit 1; }

# ── 1) 前端构建（壳加载的是后端 serve 的 static/dist） ──
echo "==> 构建 Web 前端"
(cd "$REPO_ROOT" && npm run build --silent 2>&1 | tail -n 5)

# ── 2) 桌面端构建（prepare_backend + electron-builder） ──
echo "==> 构建桌面端（electron-builder）"
(cd "$DESKTOP_DIR" && npm run build)

# ── 3) 校验产物 ──
DMG="$DIST_DIR/Astrion-${VERSION}-arm64.dmg"
ZIP="$DIST_DIR/Astrion-${VERSION}-arm64-mac.zip"
YML="$DIST_DIR/latest-mac.yml"
for f in "$DMG" "$ZIP" "$YML"; do
  [ -f "$f" ] || { echo "!! 产物缺失: $f"; exit 1; }
done
echo "==> 产物就绪:"
ls -lh "$DMG" "$ZIP" "$YML" | awk '{print "    " $5 "  " $9}'

# ── 4) 提取本版本更新说明（首个 ## 小节，到第二个 ## 为止） ──
NOTES_FILE="$(mktemp -t astrion-release-notes)"
awk -v ver="$VERSION" '
  BEGIN { n = 0; found = 0 }
  /^## / {
    n++
    if (n == 1) { if (index($0, ver) == 0) { print "!! 警告：CHANGELOG 顶部小节与版本号 " ver " 不符" > "/dev/stderr" } ; found = 1; next }
    exit
  }
  found { print }
' "$CHANGELOG" | sed '/^$/N;/^\n$/D' > "$NOTES_FILE"
[ -s "$NOTES_FILE" ] || { echo "!! 未能从 CHANGELOG 提取更新说明"; exit 1; }

# ── 5) 上传安装包 + 更新清单 + 更新说明 → 服务器重新生成清单 ──
echo "==> 上传服务器"
rsync -az "$DMG" "$ZIP" "$YML" "$DEST"
scp -q "$NOTES_FILE" "59.110.19.30:/var/www/astrion/downloads/release-notes.txt"
rm -f "$NOTES_FILE"

echo "==> 服务器重新生成更新清单"
ssh 59.110.19.30 "python3 /var/www/astrion/downloads/regen_manifest.py"

echo ""
echo "✔ 发布完成: v$VERSION"
echo "  下载页:   https://astrion.cyjai.com/download.html"
echo "  更新清单: https://astrion.cyjai.com/downloads/latest-mac.yml"
