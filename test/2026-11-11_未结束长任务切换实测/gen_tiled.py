"""把真实工作段（replay_base.json）重复拼接成一个超长任务；对话历史只保留 4 条短消息。
用法：python3 -I gen_tiled.py <WORK> <conv_id> <tiles>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
CONV_ID = sys.argv[2]
TILES = int(sys.argv[3])
assert "_experiments" in str(WORK)
base = json.loads((WORK / "replay_base.json").read_text(encoding="utf-8"))
turns = [t for t in base["turns"] if t["tool_calls"] or t["reasoning"] or t["content"]]
final = turns[-1] if not turns[-1]["tool_calls"] else None
body = turns[:-1] if final else turns
out_turns, payloads = [], {}
for k in range(TILES):
    for turn in body:
        calls = []
        for call in turn["tool_calls"]:
            new_id = f"{call['id']}_t{k}"
            calls.append({**call, "id": new_id})
            payloads[new_id] = base["tool_payloads"].get(call["id"], {"success": True, "output": ""})
        out_turns.append({**turn, "tool_calls": calls})
out_turns.append(final or {"reasoning": "", "content": "（收尾）", "tool_calls": []})
trigger = base["trigger"]
(WORK / "replay_script.json").write_text(json.dumps({
    "conversation_id": CONV_ID, "trigger": trigger, "turns": out_turns, "tool_payloads": payloads,
    "tiles": TILES}, ensure_ascii=False), encoding="utf-8")

path = WORK / "dataroot" / "host" / "data" / "conversations" / "agents" / f"{CONV_ID}.json"
doc = json.loads(path.read_text(encoding="utf-8"))
doc["messages"] = [
    {"role": "user", "content": "你好", "timestamp": "2026-11-01T10:00:00.000000", "message_id": "syn-u1",
     "metadata": {"message_source": "user", "visibility": "chat", "starts_work": True}},
    {"role": "assistant", "content": "你好，有什么可以帮你？", "timestamp": "2026-11-01T10:00:02.000000",
     "message_id": "syn-a1", "metadata": {}},
    {"role": "user", "content": "先放着吧", "timestamp": "2026-11-01T10:01:00.000000", "message_id": "syn-u2",
     "metadata": {"message_source": "user", "visibility": "chat", "starts_work": True}},
    {"role": "assistant", "content": "好的。", "timestamp": "2026-11-01T10:01:02.000000",
     "message_id": "syn-a2", "metadata": {}},
]
meta = doc["metadata"]
for key in ("compression_count", "deep_compression_records", "last_deep_compression_record"):
    meta.pop(key, None)
meta["compression_count"] = 0
path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"turns": len(out_turns), "tools": sum(len(t["tool_calls"]) for t in out_turns),
                  "payload_MB": round(len(json.dumps(payloads, ensure_ascii=False).encode()) / 1e6, 1),
                  "history_messages": 4, "history_bytes": path.stat().st_size}, ensure_ascii=False))
