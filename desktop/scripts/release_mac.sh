#!/usr/bin/env bash
# Astrion 桌面端 macOS 一键发布：
#   同步版本号 → 构建前端 → tauri build（签名 updater 产物）→ 上传服务器 → 重新生成更新清单
#
# 前置条件：
#   1. 更新签名私钥 ~/.astrion-desktop-keys/updater.key（一次性生成：
#      cd desktop && npx tauri signer generate -w ~/.astrion-desktop-keys/updater.key --password ""）
#   2. 服务器 SSH 免密（同 deploy_website.sh 的裸主机名用法）
#   3. 更新日志 desktop/DESKTOP_CHANGELOG.md 顶部已写好本版本小节
#
# 用法：bash desktop/scripts/release_mac.sh [版本号]
#   缺省读 tauri.conf.json 的 version；显式传入时会同步写回 tauri.conf.json 与 Cargo.toml。
#
# Windows 半（在 Windows 构建机上执行，本脚本不覆盖）：
#   同一版本号 tauri build（需同样的 TAURI_SIGNING_PRIVATE_KEY 环境变量）→
#   把 Astrion_<ver>_x64-setup.exe 与 .sig 传到服务器 /var/www/astrion/downloads/ →
#   在服务器上运行 python3 /var/www/astrion/downloads/regen_manifest.py。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DESKTOP_DIR="$REPO_ROOT/desktop"
TAURI_CONF="$DESKTOP_DIR/src-tauri/tauri.conf.json"
CARGO_TOML="$DESKTOP_DIR/src-tauri/Cargo.toml"
CHANGELOG="$DESKTOP_DIR/DESKTOP_CHANGELOG.md"
KEY_FILE="$HOME/.astrion-desktop-keys/updater.key"
DEST="59.110.19.30:/var/www/astrion/downloads/"
BUNDLE_DIR="$DESKTOP_DIR/src-tauri/target/release/bundle"

# ── 版本号：显式参数 > tauri.conf.json ──
if [ $# -ge 1 ]; then
  VERSION="$1"
  python3 - "$TAURI_CONF" "$CARGO_TOML" "$DESKTOP_DIR/package.json" "$VERSION" <<'PYEOF'
import json, re, sys
conf_path, cargo_path, pkg_path, version = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
conf = json.load(open(conf_path))
conf["version"] = version
json.dump(conf, open(conf_path, "w"), ensure_ascii=False, indent=2)
open(conf_path, "a").write("\n")
text = open(cargo_path).read()
# 只替换 [package] 段的第一个 version（依赖段不动）
text, n = re.subn(r'(?m)^version = ".*"$', f'version = "{version}"', text, count=1)
assert n == 1, "Cargo.toml 未找到 [package] version 行"
open(cargo_path, "w").write(text)
pkg = json.load(open(pkg_path))
pkg["version"] = version
json.dump(pkg, open(pkg_path, "w"), ensure_ascii=False, indent=2)
open(pkg_path, "a").write("\n")
PYEOF
else
  VERSION="$(python3 -c "import json; print(json.load(open('$TAURI_CONF'))['version'])")"
fi
echo "==> 发布版本: $VERSION"

# ── 前置检查 ──
[ -f "$KEY_FILE" ] || { echo "!! 缺少更新签名私钥: $KEY_FILE"; exit 1; }
[ -f "$CHANGELOG" ] || { echo "!! 缺少更新日志: $CHANGELOG"; exit 1; }

export PATH="$HOME/.cargo/bin:$PATH"
export TAURI_SIGNING_PRIVATE_KEY="$(cat "$KEY_FILE")"
# 密钥生成时为空密码：不显式置空 bundler 会停下来交互式询问密码（无人值守卡死）
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

# ── 1) 前端构建（壳加载的是后端 serve 的 static/dist） ──
echo "==> 构建 Web 前端"
(cd "$REPO_ROOT" && npm run build --silent 2>&1 | tail -n 5)

# ── 2) 桌面端构建（prepare_backend + tauri build，产出 dmg + updater 产物） ──
echo "==> 构建桌面端（tauri build）"
(cd "$DESKTOP_DIR" && npm run build)

# ── 3) 校验产物 ──
# 注意命名差异：dmg 带版本号，而 macOS updater 产物恒为 Astrion.app.tar.gz（无版本号），
# 上传时重命名为 Astrion_<ver>_aarch64.app.tar.gz（服务器清单脚本按版本号解析）。
DMG="$BUNDLE_DIR/dmg/Astrion_${VERSION}_aarch64.dmg"
TAR="$BUNDLE_DIR/macos/Astrion.app.tar.gz"
SIG="$TAR.sig"
for f in "$DMG" "$TAR" "$SIG"; do
  [ -f "$f" ] || { echo "!! 产物缺失: $f"; exit 1; }
done
echo "==> 产物就绪:"
ls -lh "$DMG" "$TAR" "$SIG" | awk '{print "    " $5 "  " $9}'

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

# ── 5) 上传安装包 + 更新说明 → 服务器重新生成清单 ──
# updater 产物上传时重命名加版本号（见上「3) 校验产物」注释）
echo "==> 上传服务器"
rsync -az "$DMG" "$DEST"
rsync -az "$TAR" "59.110.19.30:/var/www/astrion/downloads/Astrion_${VERSION}_aarch64.app.tar.gz"
rsync -az "$SIG" "59.110.19.30:/var/www/astrion/downloads/Astrion_${VERSION}_aarch64.app.tar.gz.sig"
scp -q "$NOTES_FILE" "59.110.19.30:/var/www/astrion/downloads/release-notes.txt"
rm -f "$NOTES_FILE"

echo "==> 服务器重新生成更新清单"
ssh 59.110.19.30 "python3 /var/www/astrion/downloads/regen_manifest.py"

echo ""
echo "✔ 发布完成: v$VERSION"
echo "  下载页:   https://astrion.cyjai.com/download.html"
echo "  更新清单: https://astrion.cyjai.com/downloads/latest-darwin-aarch64.json"
