#!/usr/bin/env bash
# =============================================================================
# 公开发布脚本：main → GitHub 公开仓库
#
# 机制：
#   1. 本地 public 分支为 orphan 分支（与 main 无共同历史），防止 main 历史中
#      曾出现过的敏感文件（如旧版 AGENTS.md）随历史泄漏到公开仓库。
#   2. 内容来源是 `git archive main`（仅含被跟踪文件），未跟踪文件
#      （.env / node_modules / .astrion/ 等）天然不会混入。
#   3. 按 scripts/public_release_excludes.txt 剔除「被跟踪但不公开」的条目。
#   4. 发布前执行泄漏扫描（硬闸），命中即中止。
#
# 用法：
#   bash scripts/release_public.sh            # 交互式（摘要后逐确认 commit / push）
#   bash scripts/release_public.sh --dry-run  # 只导出+扫描+展示摘要，不提交不推送
#   bash scripts/release_public.sh --yes      # 跳过确认（仅限已获得明确授权时）
# =============================================================================

set -euo pipefail

# ---- 配置 -------------------------------------------------------------------
PUBLIC_BRANCH="public"                 # 本地公开分支（orphan）
GITHUB_REMOTE="github"                 # GitHub remote 名
GITHUB_TARGET_BRANCH="main"            # 推送到 GitHub 的目标分支
EXCLUDES_FILE="scripts/public_release_excludes.txt"

# 泄漏扫描模式（上下文特征，非裸词；避免误报公开信息如 GitHub 用户名）
LEAK_PATTERNS=(
  '/Users/jojo'
  'KOJO JOTARO'
  'C:\\Users\\KOJO'
  'cyjai\.com'
  'git\.cyjai'
  '正在修复中'
  'astrion-clone'
  'E:\\astrion'
)

# ---- 参数 -------------------------------------------------------------------
DRY_RUN=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --yes)     ASSUME_YES=true ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
      exit 0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

confirm() {  # $1=提示语
  if $ASSUME_YES; then return 0; fi
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

# ---- 前置检查 ---------------------------------------------------------------
cd "$(dirname "$0")/.."
echo "==> 仓库根: $(pwd)"

if ! git remote get-url "$GITHUB_REMOTE" >/dev/null 2>&1; then
  echo "!! 未找到 remote '$GITHUB_REMOTE'。请先执行："
  echo "   git remote add $GITHUB_REMOTE <GitHub 仓库地址>"
  exit 1
fi

CURRENT_BRANCH=$(git symbolic-ref --short HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "!! 当前分支是 $CURRENT_BRANCH，请先切换到 main。"
  exit 1
fi

MAIN_SHA=$(git rev-parse --short main)
echo "==> 发布来源: main @ ${MAIN_SHA}（未提交到 main 的改动不会包含）"

# 工作区脏检测：未提交改动不会进入发布内容，明确警告防止「改了忘提交」
if [[ -n "$(git status --porcelain)" ]]; then
  echo "!! 警告：工作区存在未提交改动，以下内容【不会】出现在本次发布中："
  git status --short | head -20
  if ! $DRY_RUN; then
    confirm "仍要基于 main 最新提交继续吗？" || { echo "已取消。请先提交或暂存改动。"; exit 1; }
  fi
fi

# ---- 准备 public orphan 分支 ------------------------------------------------
if ! git show-ref --verify --quiet "refs/heads/$PUBLIC_BRANCH"; then
  echo "==> 首次发布：创建 orphan 分支 '$PUBLIC_BRANCH'（空初始提交，与 main 无历史关联）"
  EMPTY_TREE=$(git mktree </dev/null)
  INIT_COMMIT=$(git commit-tree "$EMPTY_TREE" -m "chore: initialize public release branch")
  git update-ref "refs/heads/$PUBLIC_BRANCH" "$INIT_COMMIT"
fi

# ---- 准备临时 worktree（不碰当前工作区） -------------------------------------
WT=$(mktemp -d /tmp/astrion_public_release.XXXXXX)
cleanup() {
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  git worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT

git worktree add --force "$WT" "$PUBLIC_BRANCH" >/dev/null
echo "==> 临时 worktree: $WT"

# 清空 worktree 内容（保留 .git），保证被删除的文件能反映为删除
find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

# ---- 导出 main 内容 ----------------------------------------------------------
git archive main | tar -x -C "$WT"
echo "==> 已从 main 导出被跟踪文件"

# ---- 按排除清单剔除 ----------------------------------------------------------
if [[ -f "$EXCLUDES_FILE" ]]; then
  while IFS= read -r pattern; do
    [[ -z "$pattern" || "$pattern" =~ ^[[:space:]]*# ]] && continue
    target="$WT/${pattern%/}"
    if [[ -e "$target" ]]; then
      rm -rf "$target"
      echo "==> 已剔除: $pattern"
    fi
  done < "$EXCLUDES_FILE"
else
  echo "!! 警告：排除清单 $EXCLUDES_FILE 不存在"
fi

# ---- 泄漏扫描（硬闸） --------------------------------------------------------
echo "==> 执行泄漏扫描..."
LEAK_HIT=false
for pattern in "${LEAK_PATTERNS[@]}"; do
  hits=$(grep -rInE --exclude-dir=.git --exclude=.git --exclude=release_public.sh -- "$pattern" "$WT" 2>/dev/null || true)
  if [[ -n "$hits" ]]; then
    LEAK_HIT=true
    echo "!! 命中模式 [$pattern]:"
    echo "$hits" | sed "s|$WT/||" | head -10
  fi
done
if $LEAK_HIT; then
  echo "!! 泄漏扫描未通过，已中止。请净化后重试，或确认误报后调整扫描模式。"
  exit 1
fi
echo "==> 泄漏扫描通过"

# ---- 摘要 --------------------------------------------------------------------
cd "$WT"
git add -A
echo
echo "==================== 变更摘要（相对上一次公开提交） ===================="
git status --short | head -60
CHANGED=$(git status --short | wc -l | tr -d ' ')
TOTAL=$(find . -type f ! -path './.git/*' | wc -l | tr -d ' ')
echo "--------------------------------------------------------------------"
echo "变更条目: $CHANGED    公开文件总数: $TOTAL"
echo "======================================================================"
echo

if $DRY_RUN; then
  echo "==> dry-run 结束（未提交、未推送）。临时 worktree 已自动清理。"
  exit 0
fi

if [[ "$CHANGED" == "0" ]]; then
  echo "==> 与上一次公开提交相比无变化。"
  confirm "仍然创建一个空发布提交并推送吗？" || { echo "已取消。"; exit 0; }
fi

# ---- 提交 --------------------------------------------------------------------
DATE=$(date +%Y-%m-%d)
echo "==> 提交信息: release: sync from main @$MAIN_SHA ($DATE)"
confirm "确认在 '$PUBLIC_BRANCH' 分支上创建发布提交？" || { echo "已取消。"; exit 0; }
git commit -m "release: sync from main @$MAIN_SHA ($DATE)"

# ---- 推送 --------------------------------------------------------------------
echo "==> 推送目标: $GITHUB_REMOTE  $PUBLIC_BRANCH:$GITHUB_TARGET_BRANCH"
confirm "确认推送到 GitHub（公开仓库）？" || { echo "已取消推送（本地提交已保留）。 "; exit 0; }
git push "$GITHUB_REMOTE" "$PUBLIC_BRANCH:$GITHUB_TARGET_BRANCH"

echo "==> 完成。公开仓库已更新到 main @$MAIN_SHA 的快照。"
