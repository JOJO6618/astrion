"""Synthetic newline-delimited MCP server used by the Rust contract test."""
import json
import os
import sys

initialized = False
calls = 0
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    ident = request.get("id")
    if method == "notifications/initialized":
        initialized = True
        continue
    if method == "initialize":
        assert request["params"]["protocolVersion"] == "2025-06-18"
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "astrion-synthetic", "version": "1"}}
    elif method == "tools/list":
        assert initialized
        if request["params"].get("cursor") == "next":
            result = {"tools": [{"name": "hidden", "inputSchema": {"type": "object"}}]}
        else:
            result = {"tools": [{"name": "echo.synthetic", "inputSchema": {"type": "object",
                                "properties": {"text": {"type": "string"}}}}], "nextCursor": "next"}
    elif method == "tools/call":
        assert initialized
        args = request["params"]["arguments"]
        assert "intent" not in args
        assert request["params"]["name"] == "echo.synthetic"
        calls += 1
        result = {"content": [{"type": "text", "text": args["text"]},
                              {"type": "image", "data": "AA==", "mimeType": "image/png"}],
                  "structuredContent": {"cwd": os.getcwd(), "launch": sys.argv[1:], "calls": calls}}
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
