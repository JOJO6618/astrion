"""生成纯合成场景：历史很短，一条 user 消息触发一个超长任务（数千次工具调用）。

- 工具调用只用 read_file，读 workspace 副本里生成的合成文件（无害、真实走工具与持久化链路）；
- 文件大小、每轮调用数取自真实工作段的“数字分布”（real_distribution.json，不含任何内容）；
- 对话文件 B 被替换为只有 4 条短消息的历史；回放脚本为 N 轮 assistant 回复。
用法：python3 -I gen_synthetic.py <WORK> <conv_id> <total_tools> [seed]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
CONV_ID = sys.argv[2]
TOTAL_TOOLS = int(sys.argv[3])
rng = random.Random(int(sys.argv[4]) if len(sys.argv) > 4 else 7)
dist = json.loads((WORK / "real_distribution.json").read_text())
WS = WORK / "workspace_agents"
DATA = WORK / "dataroot" / "host" / "data"
assert "_experiments" in str(WORK)

WORDS = ("alpha beta gamma delta component layout render token session buffer cursor window panel "
         "snapshot message bubble stream resolve commit module import export config style theme").split()


def filler(n: int, seed: int) -> str:
    r = random.Random(seed)
    lines, size = [], 0
    while size < n:
        line = f"{r.randint(1, 99999):05d}  " + " ".join(r.choice(WORDS) for _ in range(r.randint(6, 16)))
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)[:n]


# 1) 合成文件：按真实结果大小分位数做 60 档。
sizes = sorted(max(40, min(s, 52000)) for s in dist["tool_sizes"])
tiers = sorted({sizes[int(len(sizes) * q / 60)] for q in range(60)})
folder = WS / "switch_synth"
folder.mkdir(exist_ok=True)
for i, size in enumerate(tiers):
    (folder / f"f{i:02d}.txt").write_text(filler(size, i), encoding="utf-8")

# 2) 回放脚本。
turns, made, turn_index = [], 0, 0
while made < TOTAL_TOOLS:
    count = max(1, min(rng.choice(dist["calls_per_turn"]), TOTAL_TOOLS - made))
    calls = []
    for _ in range(count):
        want = rng.choice(dist["tool_sizes"])
        pick = min(range(len(tiers)), key=lambda k: abs(tiers[k] - want))
        args = {"intent": f"读取合成文件{pick}", "path": f"switch_synth/f{pick:02d}.txt",
                "type": "read", "max_chars": 60000}
        calls.append({"id": f"call_synth_{made:05d}", "name": "read_file",
                      "arguments": json.dumps(args, ensure_ascii=False)})
        made += 1
    think = filler(max(16, rng.choice(dist["think_len"])), 1000 + turn_index).replace("\n", " ")
    turns.append({"reasoning": think, "content": "", "tool_calls": calls})
    turn_index += 1
turns.append({"reasoning": "", "content": "（合成任务收尾）", "tool_calls": []})
trigger = "请逐个读取合成文件并核对内容，完成后汇报。"
(WORK / "replay_script.json").write_text(json.dumps({
    "conversation_id": CONV_ID, "trigger": trigger, "turns": turns, "tool_payloads": {},
    "synthetic": True, "total_tools": made}, ensure_ascii=False), encoding="utf-8")

# 3) 对话副本：只有几条短历史，不含任何长任务内容。
path = DATA / "conversations" / "agents" / f"{CONV_ID}.json"
doc = json.loads(path.read_text(encoding="utf-8"))
doc["messages"] = [
    {"role": "user", "content": "你好", "timestamp": "2026-11-01T10:00:00.000000", "message_id": "syn-u1",
     "metadata": {"message_source": "user", "visibility": "chat", "starts_work": True}},
    {"role": "assistant", "content": "你好，有什么可以帮你？", "timestamp": "2026-11-01T10:00:02.000000",
     "message_id": "syn-a1", "metadata": {}},
    {"role": "user", "content": "先放着吧", "timestamp": "2026-11-01T10:01:00.000000", "message_id": "syn-u2",
     "metadata": {"message_source": "user", "visibility": "chat", "starts_work": True}},
    {"role": "assistant", "content": "好的。", "timestamp": "2026-11-01T10:01:02.000000", "message_id": "syn-a2",
     "metadata": {}},
]
meta = doc["metadata"]
meta["model_key"] = "replay/replay-main"
meta["project_path"] = str(WS)
meta["permission_mode"] = "unrestricted"
meta["work_mode"] = "execute"
meta["runtime_message_queue"] = {"pending": [], "guidance": [], "paused": False}
for key in ("compression_count", "deep_compression_records", "last_deep_compression_record"):
    meta.pop(key, None)
meta["compression_count"] = 0
doc["title"] = "组件设计库规范梳理"
path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"turns": len(turns), "tools": made, "files": len(tiers), "history_messages": 4,
                  "history_bytes": path.stat().st_size}, ensure_ascii=False))
