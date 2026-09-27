# server/preview_server.py - 独立预览服务器（2026-09-27 新增）
#
# 为什么存在：预览内容（智能体写的 HTML / 代理的 dev server）若以主应用同源
# 端点提供，iframe sandbox 的 allow-scripts+allow-same-origin 组合对同源内容
# 等于没有沙箱——预览页 JS 可读父页面 DOM/localStorage、携带会话 cookie 调
# 应用 API（提示注入 → 会话失陷的管道）。业界同功能产品全部用「换源」隔离
# （VS Code 自定义 scheme、Codespaces 泛子域、Live Preview 独立端口），本地
# 场景能选的最强方案就是独立端口。
#
# 机制：主应用懒启动一个只绑 127.0.0.1 的随机高位端口迷你服务，路径带每对话
# 随机 token（持久化在对话 metadata.preview_token）；请求经 Host 头白名单
# （防 DNS rebinding）+ token 注册表反查 terminal 两道关卡。
# cookie 侧：主应用会话 cookie 已是 SameSite=Strict + HttpOnly，且预览源
# （127.0.0.1:随机端口）与主应用（通常 localhost:8091）不同 host = 不同站，
# 跨站请求一律不带 cookie；同 host 访问场景由 CSRF token + 无 CORS 头兜底。
#
# 已知边界：远端域名访问（云端部署）浏览器到不了这个 127.0.0.1 服务，由
# server/chat/preview.py 的旧同源端点回退（残余风险见该文件头注释）。

from __future__ import annotations

import threading
from typing import Dict, Optional

from flask import Flask, request, jsonify

# token -> (username, workspace_id, conversation_id)，进程内注册表；
# 每次向主应用拉取 targets/bootstrap 都会重建，重启后前端重新拉取即自愈。
# 存会话身份而非 terminal 引用：对话级 terminal 会被 24h 回收器销毁，
# 身份三元组可随时重建工作区级 terminal（容器句柄工作区级共享）。
_REGISTRY: Dict[str, tuple] = {}
_REGISTRY_LOCK = threading.RLock()

_SERVER_PORT: Optional[int] = None
_SERVER_LOCK = threading.Lock()

preview_app = Flask("astrion_preview")
# 预览服务自身不带会话/ cookie，日志从简
preview_app.config["SESSION_COOKIE_NAME"] = "preview_void"


def register_token(token: str, username: str, workspace_id: str, conversation_id: str) -> None:
    if not token or not conversation_id:
        return
    with _REGISTRY_LOCK:
        _REGISTRY[token] = (username, workspace_id, conversation_id)


def _resolve_context(token: str):
    """token → (terminal, conversation_id)。工作区级 terminal 足以承载
    文件读边界（file_manager）与容器会话（container_session 工作区级共享）。"""
    with _REGISTRY_LOCK:
        entry = _REGISTRY.get(token)
    if not entry:
        return None, None
    username, workspace_id, conversation_id = entry
    try:
        from server.context.resources import get_user_resources
        terminal, _workspace = get_user_resources(
            username, workspace_id=workspace_id, update_session=False,
        )
    except Exception:
        terminal = None
    return terminal, conversation_id


@preview_app.before_request
def _host_guard():
    """Host 头白名单：只允许指向本服务端口上的回环地址（防 DNS rebinding）。"""
    allowed = {f"127.0.0.1:{_SERVER_PORT}", f"localhost:{_SERVER_PORT}"}
    if (request.host or "").lower() not in allowed:
        return jsonify({"success": False, "error": "forbidden"}), 403
    return None


@preview_app.route("/<token>/file/<path:rel_path>", methods=["GET"])
def preview_file(token: str, rel_path: str):
    terminal, _conv_id = _resolve_context(token)
    if terminal is None:
        return jsonify({"success": False, "error": "unknown token"}), 404
    from server.chat.preview import serve_file_response
    return serve_file_response(terminal, rel_path)


@preview_app.route("/<token>/proxy/<int:port>/", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@preview_app.route("/<token>/proxy/<int:port>/<path:sub_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def preview_proxy(token: str, port: int, sub_path: str = ""):
    terminal, conversation_id = _resolve_context(token)
    if terminal is None:
        return jsonify({"success": False, "error": "unknown token"}), 404
    from server.chat.preview import serve_proxy_response
    return serve_proxy_response(terminal, port, sub_path, conversation_id=conversation_id)


def ensure_preview_server() -> int:
    """懒启动预览服务器（线程安全单例），返回实际监听端口。"""
    global _SERVER_PORT
    if _SERVER_PORT is not None:
        return _SERVER_PORT
    with _SERVER_LOCK:
        if _SERVER_PORT is not None:
            return _SERVER_PORT
        from werkzeug.serving import make_server
        server = make_server("127.0.0.1", 0, preview_app, threaded=True)
        _SERVER_PORT = server.server_port
        thread = threading.Thread(
            target=server.serve_forever,
            name="preview-server",
            daemon=True,
        )
        thread.start()
        print(f"[preview] 预览服务器已启动: http://127.0.0.1:{_SERVER_PORT}")
        return _SERVER_PORT
