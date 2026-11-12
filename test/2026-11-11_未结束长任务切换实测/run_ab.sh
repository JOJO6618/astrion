#!/bin/bash
# 同时启动一套：run_ab.sh <W目录> <replay端口> <cdp端口> <app名> <tiles>
set -e
REPO="/Users/jojo/Desktop/agents/正在修复中/agents"; T="$REPO/test/2026-11-11_未结束长任务切换实测"
W="$1"; RP="$2"; CP="$3"; APP="$4"; TILES="$5"
mkdir -p "$W/probe" "$W/electron-profile"
/opt/homebrew/bin/python3.12 -I "$T/prepare_copy.py" "$W" conv_20261031_170429_608 839 1507 "$RP" >/dev/null
cp "$W/replay_script.json" "$W/replay_base.json"
/opt/homebrew/bin/python3.12 -I "$T/gen_tiled.py" "$W" conv_20261031_170429_608 "$TILES" > "$W/probe/gen.json"
REPLAY_CHUNK_DELAY=0 REPLAY_CHUNK_CHARS=4096 nohup /opt/homebrew/bin/python3.12 -I "$T/replay_model_server.py" "$W/replay_script.json" "$RP" "$W/probe/model.jsonl" > "$W/probe/replay_stdout.log" 2>&1 &
echo $! > "$W/probe/replay.pid"
nohup env ASTRION_DESKTOP_DATA_ROOT="$W/dataroot" REPLAYX_WORK="$W" REPLAYX_OUT="$W/probe" REPLAYX_FAST=1 \
  "$W/$APP/Contents/MacOS/Astrion" --user-data-dir="$W/electron-profile" --remote-debugging-port="$CP" > "$W/probe/electron_stdout.log" 2>&1 &
echo $! > "$W/probe/electron.pid"
echo "$W started"
