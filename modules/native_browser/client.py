"""桌面控制桥 HTTP 客户端（仅 127.0.0.1，带随机 token）。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from config.desktop_browser import desktop_browser_token
from modules.native_browser.availability import bridge_port


class BridgeError(RuntimeError):
    pass


def _post(path: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    port = bridge_port()
    token = desktop_browser_token()
    if not port or not token:
        raise BridgeError("内置浏览器控制通道不可用。")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-Astrion-Browser-Token": token},
    )
    # 控制桥只监听回环地址；显式绕开系统代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise BridgeError(f"内置浏览器控制通道拒绝请求（HTTP {exc.code}）。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError(f"无法连接内置浏览器：{exc}") from exc
    except ValueError as exc:
        raise BridgeError("内置浏览器返回了无法解析的结果。") from exc
    if not isinstance(data, dict):
        raise BridgeError("内置浏览器返回了无法解析的结果。")
    return data


def call(payload: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
    return _post("/browser/call", payload, timeout)


def cancel(call_id: str) -> None:
    try:
        _post("/browser/cancel", {"call_id": call_id}, 5.0)
    except BridgeError:
        pass
