"""整理数据副本：隔离真实凭据、把模型指向本地回放服务、把目标对话截到工作段开始前。

只改 WORK 下的副本；原始数据目录只读。用法：
  python3 -I prepare_copy.py <WORK> <conv_id> <start_index> <end_index> <replay_port>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
CONV_ID = sys.argv[2]
START = int(sys.argv[3])
END = int(sys.argv[4])
PORT = int(sys.argv[5])
ORIGINAL = Path("/Users/jojo/.astrion/astrion-desktop/host/data/conversations/agents") / f"{CONV_ID}.json"
DATA = WORK / "dataroot" / "host" / "data"
WS_COPY = WORK / "workspace_agents"
REAL_REPO = "/Users/jojo/Desktop/agents/正在修复中/agents"
BASE_URL = f"http://127.0.0.1:{PORT}/v1"

assert "_experiments" in str(WORK) and DATA.is_dir(), WORK


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 1) 提供商：全部改指本地回放服务，密钥换成占位；新增 replay 提供商。
providers = load(DATA / "providers.json")
for record in providers["providers"].values():
    record["base_url"] = BASE_URL
    record["api_key"] = "replay-dummy"
    record["headers"] = {}
providers["providers"]["replay"] = {
    "name": "Replay", "base_name": "Replay", "base_url": BASE_URL, "api_key": "replay-dummy",
    "headers": {}, "enabled": True, "display_index": 0, "requested_id": "replay",
    "models": [{"id": "replay-main", "name": "Replay Main", "context_length": 1000000},
               {"id": "replay-aux", "name": "Replay Aux", "context_length": 1000000}],
}
dump(DATA / "providers.json", providers)

# 2) Codex OAuth 凭据从副本删除，杜绝副本访问真实服务。
for name in ("codex_auth.json", "codex_accounts.json"):
    (DATA / name).unlink(missing_ok=True)

# 3) 个性化：主/子/辅助模型都指向回放服务。
for path in (DATA / "personalization.json", WORK / "dataroot" / "personalization.json"):
    if not path.exists():
        continue
    prefs = load(path)
    for key in ("default_model", "sub_agent_model_high"):
        if key in prefs:
            prefs[key] = "replay/replay-main"
    for key in ("thinking_summary_model", "sub_agent_model_medium", "sub_agent_model_low", "title_model"):
        if prefs.get(key):
            prefs[key] = "replay/replay-aux"
    dump(path, prefs)

# 4) agents 工作区改指仓库副本。
ws_file = WORK / "dataroot" / "config" / "host_workspaces.json"
workspaces = load(ws_file)
for item in workspaces["workspaces"]:
    if item["workspace_id"] == "agents":
        item["path"] = str(WS_COPY)
dump(ws_file, workspaces)

# 5) 目标对话：保留到工作段的真实用户输入之前；其余内容原样。
original_bytes = ORIGINAL.read_bytes()
document = json.loads(original_bytes)
messages = document["messages"]
trigger = messages[START]
assert trigger["role"] == "user" and (trigger.get("metadata") or {}).get("message_source") == "user"
segment = messages[START + 1:END]
document["messages"] = messages[:START]
meta = document["metadata"]
meta["model_key"] = "replay/replay-main"
meta["project_path"] = str(WS_COPY)
meta["runtime_message_queue"] = {"pending": [], "guidance": [], "paused": False}
for key in ("compression_in_progress", "compression_job_id", "compression_resume_payload", "compression_pid"):
    meta[key] = None if key != "compression_in_progress" else False
target = DATA / "conversations" / "agents" / f"{CONV_ID}.json"
dump(target, document)

# 6) 回放脚本：助手轮次（思考/正文/工具调用）与工具原始结果。
turns, payloads, injected = [], {}, []
for msg in segment:
    if msg["role"] == "assistant":
        turns.append({
            "reasoning": msg.get("reasoning_content") or "",
            "content": msg.get("content") or "",
            "tool_calls": [{"id": tc.get("id"), "name": tc["function"]["name"],
                            "arguments": tc["function"].get("arguments") or "{}"}
                           for tc in msg.get("tool_calls") or []],
        })
    elif msg["role"] == "tool":
        payload = (msg.get("metadata") or {}).get("tool_payload")
        if payload is None:
            payload = {"success": True, "output": msg.get("content") or ""}
        payloads[msg.get("tool_call_id")] = payload
    elif msg["role"] == "user":
        injected.append((msg.get("metadata") or {}).get("message_source"))
replay = {"conversation_id": CONV_ID, "trigger": trigger["content"], "turns": turns, "tool_payloads": payloads,
          "skipped_injected_user_messages": injected,
          "source_sha256": hashlib.sha256(original_bytes).hexdigest()}
dump(WORK / "replay_script.json", replay)
print(json.dumps({
    "seed_messages": START, "turns": len(turns),
    "tool_calls": sum(len(t["tool_calls"]) for t in turns), "tool_payloads": len(payloads),
    "payload_bytes": len(json.dumps(payloads, ensure_ascii=False).encode()),
    "skipped_injected": len(injected), "source_sha256": replay["source_sha256"][:16],
}, ensure_ascii=False))
