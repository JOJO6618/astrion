#!/bin/bash
# 快进复现：重置副本 → 回放服务（无延迟）→ 克隆壳（快进钩子）→ 检查点探针
set -e
REPO="/Users/jojo/Desktop/agents/正在修复中/agents"; T="$REPO/test/2026-11-11_未结束长任务切换实测"; W="$REPO/_experiments/switch-live-20261111"
TILES="${1:-4}"
pkill -f "electron-profile" 2>/dev/null || true; pkill -f replay_model_server 2>/dev/null || true; pkill -f switch_probe 2>/dev/null || true; sleep 3
[ -n "$(ls -A $W/probe 2>/dev/null)" ] && mv "$W/probe" "$W/runs/ff-prev-$(date +%H%M%S)"; mkdir -p "$W/probe" "$W/electron-profile"
/opt/homebrew/bin/python3.12 -I "$T/prepare_copy.py" "$W" conv_20261031_170429_608 839 1507 18766 >/dev/null
cp "$W/replay_script.json" "$W/replay_base.json"
/opt/homebrew/bin/python3.12 -I "$T/gen_tiled.py" "$W" conv_20261031_170429_608 "$TILES" | tee "$W/probe/gen.json"
REPLAY_CHUNK_DELAY=0 REPLAY_CHUNK_CHARS=4096 nohup /opt/homebrew/bin/python3.12 -I "$T/replay_model_server.py" "$W/replay_script.json" 18766 "$W/probe/model.jsonl" > "$W/probe/replay_stdout.log" 2>&1 &
echo $! > "$W/probe/replay.pid"
nohup env ASTRION_DESKTOP_DATA_ROOT="$W/dataroot" REPLAYX_WORK="$W" REPLAYX_OUT="$W/probe" REPLAYX_FAST=1 \
  "$W/${APP:-Astrion-copy.app}/Contents/MacOS/Astrion" --user-data-dir="$W/electron-profile" --remote-debugging-port=19222 > "$W/probe/electron_stdout.log" 2>&1 &
echo $! > "$W/probe/electron.pid"
sleep 30
cd "$REPO"
CHECKPOINTS="${CHECKPOINTS:-50,100,200,400,800}" nohup node "$T/switch_probe_ff.mjs" x conv_20261111_014826_508 conv_20261031_170429_608 http://127.0.0.1:18766/control/state "$W/probe" > "$W/probe/probe_stdout.log" 2>&1 &
echo $! > "$W/probe/probe.pid"; echo started
