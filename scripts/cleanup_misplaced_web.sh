#!/usr/bin/env bash
# 清理误迁到 ~/.agents/web 的历史运行态数据副本（已废弃路径）。
#
# 背景：首次迁移时脚本未加载 .env、误判为 web 模式，把数据复制到了
#       ~/.agents/web/。源数据仍在源码树，web 只是程序读不到的错位副本。
#       本机终端有完整权限，可安全删除。
#
# 注意：当前项目运行态数据根目录已改为 ~/.astrion/astrion/，本脚本仅用于
#       清理旧路径 ~/.agents/web 的历史遗留，不应用于新路径。
#
# 用法：bash scripts/cleanup_misplaced_web.sh

set -euo pipefail

WEB_DIR="${HOME}/.agents/web"

if [ ! -e "$WEB_DIR" ]; then
  echo "目标不存在，无需清理：$WEB_DIR"
  exit 0
fi

echo "将删除错位副本：$WEB_DIR"
du -sh "$WEB_DIR" 2>/dev/null || true
printf "确认删除？[y/N] "
read -r ans
case "$ans" in
  y|Y|yes|YES)
    # 先清掉可能存在的不可变标志（docx skill 等附带的 uchg/schg），再删除。
    chflags -R nouchg,noschg "$WEB_DIR" 2>/dev/null || true
    rm -rf "$WEB_DIR"
    echo "已删除 $WEB_DIR"
    ;;
  *)
    echo "已取消，未删除任何文件。"
    ;;
esac
