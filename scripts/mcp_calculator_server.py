#!/usr/bin/env python3
"""测试用 MCP 计算器服务（stdio）。

提供工具：
- calculator: 基础四则运算 + 幂运算 + 取模
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any, Dict, Tuple

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
                "name": "calculator",
                "description": "简单计算器：支持 add/sub/mul/div/pow/mod",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "enum": ["add", "sub", "mul", "div", "pow", "mod"],
                            "description": "运算类型",
                        },
                        "a": {"type": "number", "description": "左操作数"},
                        "b": {"type": "number", "description": "右操作数"},
                    },
                    "required": ["operation", "a", "b"],
                },
            }
        ]
    }


def _parse_operands(arguments: Dict[str, Any]) -> Tuple[float, float]:
    a = float((arguments or {}).get("a"))
    b = float((arguments or {}).get("b"))
    if math.isinf(a) or math.isinf(b) or math.isnan(a) or math.isnan(b):
        raise ValueError("a/b 不能是 NaN 或 Infinity")
    return a, b


def _calculate(operation: str, a: float, b: float) -> float:
    if operation == "add":
        return a + b
    if operation == "sub":
        return a - b
    if operation == "mul":
        return a * b
    if operation == "div":
        if b == 0:
            raise ZeroDivisionError("除数不能为 0")
        return a / b
    if operation == "pow":
        return a**b
    if operation == "mod":
        if b == 0:
            raise ZeroDivisionError("取模除数不能为 0")
        return a % b
    raise ValueError(f"不支持的 operation: {operation}")


def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if name != "calculator":
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"未知工具: {name}"}],
            "structuredContent": {"error": "tool_not_found", "name": name},
        }
    try:
        operation = str((arguments or {}).get("operation") or "").strip().lower()
        a, b = _parse_operands(arguments or {})
        result = _calculate(operation, a, b)
    except Exception as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": f"计算失败: {exc}"}],
            "structuredContent": {"error": "invalid_arguments", "detail": str(exc)},
        }
    return {
        "isError": False,
        "content": [{"type": "text", "text": f"{operation}({a}, {b}) = {result}"}],
        "structuredContent": {"operation": operation, "a": a, "b": b, "result": result},
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

        # 通知不需要响应
        if msg_id is None:
            continue

        if method == "initialize":
            _ok(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "calculator-stdio-mcp", "version": "1.0.0"},
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

