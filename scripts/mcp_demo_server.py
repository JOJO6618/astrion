#!/usr/bin/env python3
"""最小可运行 MCP stdio 示例服务。

功能：
- tools/list: 返回 echo_upper / add_numbers 两个工具
- tools/call: 执行对应逻辑并返回 text + structuredContent
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

PROTOCOL_VERSION = "2025-06-18"


def _send(payload: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok(msg_id: Any, result: Dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _err(msg_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _tools_list() -> Dict[str, Any]:
    return {
        "tools": [
            {
                "name": "echo_upper",
                "description": "将 text 转为大写后返回",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "输入文本"}},
                    "required": ["text"],
                },
            },
            {
                "name": "add_numbers",
                "description": "计算两个数字相加",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number", "description": "加数A"},
                        "b": {"type": "number", "description": "加数B"},
                    },
                    "required": ["a", "b"],
                },
            },
        ]
    }


def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name == "echo_upper":
        text = str((arguments or {}).get("text") or "")
        result = text.upper()
        return {
            "isError": False,
            "content": [{"type": "text", "text": result}],
            "structuredContent": {"result": result},
        }
    if name == "add_numbers":
        try:
            a = float((arguments or {}).get("a"))
            b = float((arguments or {}).get("b"))
        except Exception:
            return {
                "isError": True,
                "content": [{"type": "text", "text": "参数 a/b 必须是数字"}],
                "structuredContent": {"error": "invalid_arguments"},
            }
        value = a + b
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"{a} + {b} = {value}"}],
            "structuredContent": {"a": a, "b": b, "sum": value},
        }
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"未知工具: {name}"}],
        "structuredContent": {"error": "tool_not_found", "name": name},
    }


def main() -> int:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        msg_id = req.get("id")
        params = req.get("params") or {}

        # 通知无需响应
        if msg_id is None:
            continue

        if method == "initialize":
            _ok(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "demo-stdio-mcp", "version": "1.0.0"},
                },
            )
            continue

        if method == "tools/list":
            _ok(msg_id, _tools_list())
            continue

        if method == "tools/call":
            name = str(params.get("name") or "").strip()
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            _ok(msg_id, _call_tool(name, arguments))
            continue

        _err(msg_id, -32601, f"Method not found: {method}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

