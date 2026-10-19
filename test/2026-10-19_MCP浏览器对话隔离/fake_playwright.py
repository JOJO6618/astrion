"""Offline MCP fixture: emulate an exclusive persistent browser profile."""

import json
import os
from pathlib import Path
import sqlite3
import sys


profile = None
profile_lock = None
page = "about:blank"


def call(arguments):
    global profile, profile_lock, page
    if profile is None:
        index = sys.argv.index("--user-data-dir")
        profile = Path(sys.argv[index + 1])
        profile.mkdir(parents=True, exist_ok=True)
        try:
            profile_lock = sqlite3.connect(profile / "lock.sqlite", timeout=0)
            profile_lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            profile = None
            return {"isError": True, "content": [{"type": "text", "text": "Browser is already in use"}]}
    if "url" in arguments:
        page = arguments["url"]
    storage = profile / "storage.json"
    if "login" in arguments:
        storage.write_text(json.dumps(arguments["login"]), encoding="utf-8")
    result = {
        "profile": str(profile), "cwd": os.getcwd(), "pid": os.getpid(),
        "page": page,
        "login": json.loads(storage.read_text(encoding="utf-8")) if storage.exists() else None,
    }
    return {"content": [{"type": "text", "text": json.dumps(result)}]}


try:
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "browser_probe", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = call(message.get("params", {}).get("arguments", {}))
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
finally:
    if profile_lock is not None:
        profile_lock.close()
