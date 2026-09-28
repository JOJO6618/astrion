# server/status/desktop_update.py - 桌面端自动更新代理端点
#
# 链路：前端 →（同源）→ 本模块 →（loopback）→ Tauri 壳控制桥 / 官网更新清单
#   - 版本检查：后端拉取 astrion.cyjai.com 的 latest-<channel>.json 与壳注入的
#     ASTRION_DESKTOP_VERSION 对比（壳版本构建期由 Cargo 写入 env）。
#   - 安装触发/进度：代理到壳控制桥（ASTRION_DESKTOP_BRIDGE_PORT），前端不直接
#     接触桥端口，无 CORS 问题，鉴权沿用现有会话。
# 两个环境变量由 desktop/src-tauri 壳在 spawn 后端时注入；缺失即非桌面环境，
# 端点返回 code="not_desktop"（HTTP 200，避免 Web 端控制台噪音）。
from __future__ import annotations

import os
import platform
import sys
import time

import httpx
from flask import jsonify, request

from server.auth_helpers import api_login_required
from server.status import status_bp
from modules.i18n import tr

# 更新清单地址（官网静态站 downloads/ 目录，与 Tauri updater endpoints 同源）
_UPDATE_MANIFEST_BASE = "https://astrion.cyjai.com/downloads"

# 成功结果轻量进程内缓存：启动静默检查与手动打开弹窗往往连发，避免重复打远端。
# 失败不缓存（用户重试应真实回源）。
_CHECK_CACHE_TTL_SECONDS = 30.0
_check_cache: dict = {"at": 0.0, "payload": None}


def _desktop_context() -> dict | None:
    """读取壳注入的桌面身份；非桌面环境（Web/CLI/服务器部署）返回 None。"""
    version = (os.environ.get("ASTRION_DESKTOP_VERSION") or "").strip()
    if not version:
        return None
    bridge_port = (os.environ.get("ASTRION_DESKTOP_BRIDGE_PORT") or "").strip()
    return {"version": version, "bridge_port": bridge_port or None}


def _update_channel() -> str | None:
    """当前系统对应的 updater 清单通道（与 Tauri 端点模板 {{target}}-{{arch}} 对齐）。"""
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        return "darwin-aarch64" if machine in ("arm64", "aarch64") else "darwin-x86_64"
    if sys.platform.startswith("win"):
        return "windows-x86_64"
    return None


def _parse_version(text: str) -> tuple:
    """宽松语义化版本解析：'0.2.0' → (0, 2, 0)，非法段按 0 计。"""
    parts = []
    for piece in str(text).strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _not_desktop():
    return jsonify({
        "success": False,
        "code": "not_desktop",
        "error": tr("desktop_update.not_desktop"),
    }), 200


def _bridge_request(ctx: dict, method: str, path: str, timeout: float, json_body: dict | None = None):
    """调用壳控制桥；返回 (payload, error_response)。"""
    if not ctx.get("bridge_port"):
        return None, (jsonify({
            "success": False,
            "code": "bridge_unavailable",
            "error": tr("desktop_update.bridge_unavailable"),
        }), 200)
    url = f"http://127.0.0.1:{ctx['bridge_port']}{path}"
    try:
        resp = httpx.request(method, url, timeout=timeout, json=json_body)
        return resp.json(), None
    except Exception as exc:  # noqa: BLE001 - 桥不可达统一包装，细节进 detail
        return None, (jsonify({
            "success": False,
            "code": "bridge_unreachable",
            "error": tr("desktop_update.bridge_unreachable"),
            "detail": str(exc)[:200],
        }), 200)


@status_bp.route('/api/desktop/update/check')
@api_login_required
def desktop_update_check():
    """检查是否有新版本（对比官网清单与壳注入的当前版本）。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()

    now = time.time()
    force = request.args.get("force") == "1"
    cached = _check_cache["payload"]
    if not force and cached and (now - _check_cache["at"]) < _CHECK_CACHE_TTL_SECONDS:
        return jsonify(cached)

    channel = _update_channel()
    if not channel:
        return jsonify({
            "success": False,
            "code": "unsupported_platform",
            "error": tr("desktop_update.unsupported_platform"),
        }), 200

    manifest_url = f"{_UPDATE_MANIFEST_BASE}/latest-{channel}.json"
    try:
        resp = httpx.get(manifest_url, timeout=8.0, follow_redirects=True)
        resp.raise_for_status()
        manifest = resp.json()
    except Exception as exc:  # noqa: BLE001 - 网络/解析失败统一包装，细节进 detail
        return jsonify({
            "success": False,
            "code": "check_failed",
            "error": tr("desktop_update.check_failed"),
            "detail": str(exc)[:200],
        }), 200

    latest = str(manifest.get("version") or "").strip()
    current = ctx["version"]
    update_available = bool(latest) and _parse_version(latest) > _parse_version(current)
    payload = {
        "success": True,
        "data": {
            "current_version": current,
            "latest_version": latest or current,
            "update_available": update_available,
            "notes": str(manifest.get("notes") or ""),
            "pub_date": str(manifest.get("pub_date") or ""),
            "channel": channel,
            "bridge_available": bool(ctx["bridge_port"]),
            "checked_at": int(now),
        },
    }
    _check_cache["at"] = now
    _check_cache["payload"] = payload
    return jsonify(payload)


@status_bp.route('/api/desktop/update/install', methods=['POST'])
@api_login_required
def desktop_update_install():
    """触发无感更新：壳侧 updater 下载 → 验签 → 安装 → 自动重启。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    body, err = _bridge_request(ctx, "POST", "/update/install", timeout=5.0)
    if err:
        return err
    started = bool(body.get("started"))
    return jsonify({"success": started, "data": body}), (202 if started else 409)


@status_bp.route('/api/desktop/update/progress')
@api_login_required
def desktop_update_progress():
    """查询更新进度（state/downloaded/total/error），安装期间由前端轮询。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    body, err = _bridge_request(ctx, "GET", "/update/progress", timeout=3.0)
    if err:
        return err
    return jsonify({"success": True, "data": body})


@status_bp.route('/api/desktop/window/drag', methods=['POST'])
@api_login_required
def desktop_window_drag():
    """开始拖拽移动窗口（桌面壳顶部对话标签条空白区域按下时调用）。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    body, err = _bridge_request(ctx, "POST", "/window/drag", timeout=3.0)
    if err:
        return err
    return jsonify({"success": bool(body.get("started")), "data": body})


# Windows 无边框模式自绘三大键的动作白名单——与 chrome-dispatch 同理收窄，
# 防止借道向壳注入任意窗口操作。
_WINDOW_CONTROL_ACTIONS = {"minimize", "maximize-toggle", "close"}


@status_bp.route('/api/desktop/window/control', methods=['POST'])
@api_login_required
def desktop_window_control():
    """窗口控制（Windows 无边框模式的自绘三大键）→ 壳控制桥 /window/control。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip()
    if action not in _WINDOW_CONTROL_ACTIONS:
        return jsonify({
            "success": False,
            "code": "invalid_action",
            "error": tr("desktop_update.chrome_invalid_action"),
        }), 200
    body, err = _bridge_request(
        ctx, "POST", "/window/control", timeout=3.0, json_body={"action": action},
    )
    if err:
        return err
    return jsonify({"success": True, "data": body})


@status_bp.route('/api/desktop/window/state')
@api_login_required
def desktop_window_state():
    """窗口状态（当前仅 maximized）：自绘三大键据以切换最大化/还原图标。"""
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    body, err = _bridge_request(ctx, "GET", "/window/state", timeout=3.0)
    if err:
        return err
    return jsonify({"success": True, "data": body})


# chrome 标签条（独立 webview）允许派发的意图白名单——桥侧会 eval 进主 webview，
# 必须收窄动作集，防止借道向主页面注入任意脚本。
# open-settings：Windows 标签条左侧「设置」入口按钮（主页面整跳 /settings）。
_CHROME_DISPATCH_ACTIONS = {"activate", "new", "close", "open-settings"}


@status_bp.route('/api/desktop/chrome-dispatch', methods=['POST'])
@api_login_required
def desktop_chrome_dispatch():
    """chrome 标签条 → 主页面的意图中继（同源代理到壳控制桥 /chrome/dispatch）。

    chrome webview 与主页面同为 External URL，页面 JS 拿不到 Tauri API；
    壳控制桥暴露 /chrome/dispatch，壳侧收到后 eval 注入主 webview 的
    window.__astrionChromeDispatch。本端点做同源代理 + action 白名单。
    """
    ctx = _desktop_context()
    if not ctx:
        return _not_desktop()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip()
    if action not in _CHROME_DISPATCH_ACTIONS:
        return jsonify({
            "success": False,
            "code": "invalid_action",
            "error": tr("desktop_update.chrome_invalid_action"),
        }), 200
    body, err = _bridge_request(
        ctx, "POST", "/chrome/dispatch", timeout=5.0,
        json_body={"action": action, "payload": payload.get("payload")},
    )
    if err:
        return err
    return jsonify({"success": True, "data": body})
