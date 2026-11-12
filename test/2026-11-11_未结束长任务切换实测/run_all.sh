#!/bin/bash
# 停掉上一轮 → 重置目标对话副本 → 启动回放服务与后端副本 → 启动切换探针。
# 用法：bash run_all.sh <run_name> [interval_sec] [max_minutes] [extra replay env...]
set -e
REPO="/Users/jojo/Desktop/agents/正在修复中/agents"
T="$REPO/test/2026-11-11_未结束长任务切换实测"
WORK="$REPO/_experiments/switch-live-20261111"
RUN="${1:?run name}"; INTERVAL="${2:-60}"; MAXMIN="${3:-45}"
CONV_A=conv_20261111_014826_508
CONV_B=conv_20261031_170429_608
for f in probe backend replay; do
  pid_file="$WORK/probe/$f.pid"
  [ -f "$pid_file" ] && kill "$(cat "$pid_file")" 2>/dev/null || true
done
sleep 2
for port in 18765 18766; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "port $port still in use"; exit 1; fi
done
mkdir -p "$WORK/runs"
[ -d "$WORK/probe" ] && [ -n "$(ls -A "$WORK/probe" 2>/dev/null)" ] && mv "$WORK/probe" "$WORK/runs/prev-$(date +%H%M%S)"
mkdir -p "$WORK/probe"
/opt/homebrew/bin/python3.12 -I "$T/prepare_copy.py" "$WORK" "$CONV_B" 839 1507 18766 > "$WORK/probe/prepare.json"
nohup env ${REPLAY_ENV:-} /opt/homebrew/bin/python3.12 -I "$T/replay_model_server.py" "$WORK/replay_script.json" 18766 "$WORK/probe/model.jsonl" > "$WORK/probe/replay_stdout.log" 2>&1 &
echo $! > "$WORK/probe/replay.pid"
cd "$WORK"
env -i HOME=/Users/jojo USER=jojo LOGNAME=jojo SHELL=/bin/zsh \
  PATH="/Users/jojo/.bun/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  TMPDIR="$TMPDIR" LANG=zh_CN.UTF-8 PYTHONDONTWRITEBYTECODE=1 ASTRION_IGNORE_DOTENV=1 TERMINAL_SANDBOX_MODE=host \
  WEB_SERVER_HOST=127.0.0.1 ASTRION_DATA_ROOT="$WORK/dataroot" ASTRION_DESKTOP_VERSION=0.9.3 \
  nohup /Applications/Astrion.app/Contents/Resources/runtime/python/bin/python3.12 "$T/launch_backend.py" "$WORK" 18765 \
  > "$WORK/probe/backend_stdout.log" 2>&1 &
echo $! > "$WORK/probe/backend.pid"
for i in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:18765/api/health && break; sleep 1; done
echo "$RUN" > "$WORK/probe/RUN_NAME"
cd "$REPO"
nohup node "$T/switch_probe.mjs" http://127.0.0.1:18765 "$CONV_A" "$CONV_B" http://127.0.0.1:18766/control/state \
  "$WORK/probe" "$INTERVAL" "$MAXMIN" ${PROBE_FLAGS:-} > "$WORK/probe/probe_stdout.log" 2>&1 &
echo $! > "$WORK/probe/probe.pid"
echo "started $RUN"
