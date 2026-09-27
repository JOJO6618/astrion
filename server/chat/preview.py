# server/chat/preview.py - 预览面板 API（2026-09-27 新增）
#
# 端点：
#   GET  /api/preview/targets          当前对话的预览目标列表（metadata.preview_targets）
#   POST /api/preview/targets/remove   删除指定预览目标 {key: "server:<url>" | "file:<path>"}
#   GET  /api/preview/file/<path>      工作区文件预览（HTML 注入 <base> 使相对资源可解析；
#                                      读边界与 read_file 工具同源 = file_manager._validate_path）
#   GET  /api/preview/proxy/<port>/…   dev server 反向代理（SSRF 防护：仅允许当前对话
#                                      已登记的 localhost 目标端口；剥离 X-Frame-Options）
#
# 已知限制（MVP）：不代理 WebSocket（dev server HMR 在代理预览里不生效，改代码后手动刷新）；
# 页面内绝对路径的 JS 动态 import/fetch 不做重写（HTML 的 src/href 绝对路径会重写）。

from __future__ import annotations

import re
import secrets
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import httpx
from flask import jsonify, request, Response

from server.chat import chat_bp
from server.auth_helpers import api_login_required
from server.context import with_terminal
from server.security import rate_limited
from modules.i18n import tr
from modules.preview_targets import remove_target, _normalize, _origin, is_preview_enabled
from core.web_terminal import WebTerminal
from modules.user_manager import UserWorkspace

# 代理响应体上限（防御性；正常页面远小于此）
_PROXY_MAX_BYTES = 50 * 1024 * 1024
_PROXY_TIMEOUT = 30.0

_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}
# 这些响应头会阻止 iframe 嵌入或破坏代理路径，统一剥离
_STRIP_HEADERS = _HOP_BY_HOP_HEADERS | {"x-frame-options", "content-security-policy"}


def _docker_exec_fetch(
    container_name: str,
    docker_bin: str,
    method: str,
    target_url: str,
    headers: Dict[str, str],
    body: Optional[bytes],
) -> Tuple[int, Dict[str, str], bytes]:
    """docker 模式的代理取数：目标服务器活在工具箱容器的网络命名空间里，
    后端进程的 127.0.0.1 根本到不了（2026-09-27 实测 502 根因）——
    经 docker exec curl 在容器内发请求。不跟随重定向：3xx 透传给浏览器，
    Location 由调用方改写成代理路径。
    """
    args = [
        docker_bin or "docker", "exec", "-i", container_name,
        "curl", "-sS", "-i", "--max-time", str(int(_PROXY_TIMEOUT)), "-X", method,
    ]
    for key, value in headers.items():
        args += ["-H", f"{key}: {value}"]
    if body:
        args += ["--data-binary", "@-"]
    args.append(target_url)
    proc = subprocess.run(
        args, input=body or None, capture_output=True, timeout=_PROXY_TIMEOUT + 5,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise RuntimeError(detail or f"curl exit {proc.returncode}")
    raw = proc.stdout
    # 跳过 100 Continue 等中间状态块
    while True:
        head, sep, rest = raw.partition(b"\r\n\r\n")
        if not sep:
            head, sep, rest = raw.partition(b"\n\n")
        if not sep:
            raise RuntimeError("bad upstream response")
        status_line = head.split(b"\n", 1)[0].decode("latin-1", errors="replace")
        match = re.search(r"\b(\d{3})\b", status_line)
        status = int(match.group(1)) if match else 502
        if status == 100 and rest:
            raw = rest
            continue
        break
    resp_headers: Dict[str, str] = {}
    for line in head.split(b"\n")[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            resp_headers[k.decode("latin-1").strip()] = v.decode("latin-1").strip()
    return status, resp_headers, rest


def _rewrite_location(location: str, proxy_prefix: str, port: int) -> str:
    """重定向 Location 改道代理：本地绝对 URL → 代理前缀；根相对路径同样改道。"""
    rewritten = re.sub(
        rf"^https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):{port}",
        proxy_prefix,
        location,
        flags=re.IGNORECASE,
    )
    if rewritten.startswith("/") and not rewritten.startswith(proxy_prefix):
        rewritten = f"{proxy_prefix}{rewritten}"
    return rewritten


def _load_targets(terminal: WebTerminal, conversation_id: Optional[str]) -> List[Dict[str, Any]]:
    if not conversation_id:
        return []
    cm = getattr(terminal, "context_manager", None)
    if cm is None:
        return []
    # 优先走按对话路由的 manager（工作区级 terminal 也能正确加载任意对话）
    manager = None
    router = getattr(cm, "_get_conversation_manager_for_id", None)
    if callable(router):
        try:
            manager = router(conversation_id)
        except Exception:
            manager = None
    if manager is None:
        manager = getattr(cm, "conversation_manager", None)
    if manager is None:
        return []
    data = manager.load_conversation(conversation_id)
    if not data:
        return []
    return _normalize((data.get("metadata") or {}).get("preview_targets"))


# ----------------------------------------------------------------------
# 预览安全隔离（2026-09-27，调研：.astrion/sub_agent_results/agent_2_4）
#
# 同源缺口的修法 = 换源：预览内容改由独立预览服务器提供（127.0.0.1 随机高位
# 端口 + 每对话随机 token 路径 + Host 头校验），iframe 与主应用不同源不同站
# （127.0.0.1 ≠ localhost），SOP + SameSite=Strict cookie 双重隔离。
# 业界同功能产品无一靠 sandbox flag/CSP 隔离，全部是换源（VS Code 自定义
# scheme、Codespaces/CodeSandbox 泛子域、Live Preview 独立端口）。
# 远端域名访问（云端部署）无法用 127.0.0.1 预览服务器，回退旧的同源端点，
# 属已知残余风险（有 CSRF token + 无 CORS 头兜底，写操作/读响应均被挡）。
# ----------------------------------------------------------------------

# 请求的 Host 属于本机回环时才能用独立预览服务器（远端域名访问走旧端点回退）
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _get_or_create_preview_token(terminal: WebTerminal, conversation_id: str) -> Optional[str]:
    """每对话随机预览 token，持久化在 metadata.preview_token（重启后不变，
    前端无需因服务重启重新拉取——注册表重建即可）。"""
    if not conversation_id:
        return None
    cm = getattr(terminal, "context_manager", None)
    if cm is None:
        return None
    manager = None
    router = getattr(cm, "_get_conversation_manager_for_id", None)
    if callable(router):
        try:
            manager = router(conversation_id)
        except Exception:
            manager = None
    if manager is None:
        manager = getattr(cm, "conversation_manager", None)
    if manager is None:
        return None
    try:
        data = manager.load_conversation(conversation_id) or {}
        metadata = data.get("metadata") or {}
        token = metadata.get("preview_token")
        if isinstance(token, str) and len(token) >= 16:
            return token
        token = secrets.token_urlsafe(24)
        if manager.update_conversation_metadata(conversation_id, {"preview_token": token}):
            return token
    except Exception:
        pass
    return None


def build_preview_runtime(
    terminal: WebTerminal,
    username: str,
    workspace: UserWorkspace,
    conversation_id: Optional[str],
) -> Dict[str, Optional[str]]:
    """为前端序列化预览运行时：{preview_base, preview_token}。

    仅当当前请求来自本机回环地址时启用独立预览服务器（云端域名访问回退旧
    同源端点，preview_base=None）；每次调用都重建 token→terminal 注册表项，
    保证预览服务器能反查到 terminal（容器会话 / 文件读边界都挂在它上面）。
    """
    empty: Dict[str, Optional[str]] = {"preview_base": None, "preview_token": None}
    try:
        if not is_preview_enabled():
            return empty
        host = (request.host or "").split(":", 1)[0].strip().lower()
        if host not in _LOOPBACK_HOSTS:
            return empty
        if not conversation_id:
            return empty
        token = _get_or_create_preview_token(terminal, conversation_id)
        if not token:
            return empty
        from server.preview_server import ensure_preview_server, register_token
        port = ensure_preview_server()
        workspace_id = getattr(workspace, "workspace_id", None) or "default"
        register_token(token, username, workspace_id, conversation_id)
        return {"preview_base": f"http://127.0.0.1:{port}", "preview_token": token}
    except Exception:
        return empty


def _current_conversation_id(terminal: WebTerminal) -> Optional[str]:
    cm = getattr(terminal, "context_manager", None)
    return getattr(cm, "current_conversation_id", None) if cm else None


@chat_bp.route('/api/preview/targets', methods=['GET'])
@api_login_required
@with_terminal
def get_preview_targets(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    # docker/web 模式整体禁用预览（红队结论见文件头注释与项目记忆）
    if not is_preview_enabled():
        return jsonify({
            "success": True,
            "preview_targets": [],
            "preview_base": None,
            "preview_token": None,
        })
    targets = _load_targets(terminal, _current_conversation_id(terminal))
    # 文件目标过滤已不存在的文件（与 edited_files 回填同一语义）
    file_manager = getattr(terminal, "file_manager", None)
    filtered: List[Dict[str, Any]] = []
    for item in targets:
        if item.get("type") == "file":
            rel = str(item.get("path") or "")
            try:
                valid, _err, full_path = file_manager._validate_path(rel) if file_manager else (False, None)
            except Exception:
                valid, full_path = False, None
            if not (valid and full_path is not None and full_path.exists() and full_path.is_file()):
                continue
        filtered.append(item)
    runtime = build_preview_runtime(terminal, username, workspace, _current_conversation_id(terminal))
    return jsonify({
        "success": True,
        "preview_targets": filtered,
        "preview_base": runtime["preview_base"],
        "preview_token": runtime["preview_token"],
    })


@chat_bp.route('/api/preview/targets/remove', methods=['POST'])
@api_login_required
@with_terminal
@rate_limited("preview_target_remove", 30, 60, scope="user")
def remove_preview_target(terminal: WebTerminal, workspace: UserWorkspace, username: str):
    if not is_preview_enabled():
        return jsonify({"success": False, "error": tr("preview.disabled_in_docker")}), 403
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or "").strip()
    if not key:
        return jsonify({"success": False, "error": tr("preview.invalid_key")}), 400
    remove_target(getattr(terminal, "context_manager", None), _current_conversation_id(terminal), key)
    return jsonify({
        "success": True,
        "preview_targets": _load_targets(terminal, _current_conversation_id(terminal)),
    })


@chat_bp.route('/api/preview/file/<path:rel_path>', methods=['GET'])
@api_login_required
@with_terminal
def preview_file(terminal: WebTerminal, workspace: UserWorkspace, username: str, rel_path: str):
    # 旧同源端点：保留给远端域名访问回退（本机访问走独立预览服务器，见文件头注释）
    if not is_preview_enabled():
        return jsonify({"success": False, "error": tr("preview.disabled_in_docker")}), 403
    return serve_file_response(terminal, rel_path)


def serve_file_response(terminal: WebTerminal, rel_path: str) -> Response:
    """文件预览响应（主应用与独立预览服务器共用的实现）。"""
    file_manager = getattr(terminal, "file_manager", None)
    if file_manager is None:
        return jsonify({"success": False, "error": tr("preview.file_unavailable")}), 503
    try:
        valid, err, full_path = file_manager._validate_path(rel_path)
    except Exception:
        valid, err, full_path = False, tr("preview.file_unavailable"), None
    if not valid or full_path is None or not full_path.exists() or not full_path.is_file():
        return jsonify({"success": False, "error": err or tr("preview.file_unavailable")}), 404
    try:
        content = full_path.read_bytes()
    except Exception:
        return jsonify({"success": False, "error": tr("preview.file_read_failed")}), 500

    import mimetypes
    mime = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    if mime == "text/html":
        # 注入 <base>：页面里的相对资源路径（css/js/图片）解析回本端点的目录前缀
        # 注意：独立预览服务器路径多一层 <token> 前缀，base 必须基于当前请求路径推导，
        # 不能写死 /api/preview/file/——统一用「当前请求 URL 去掉最后一段」作前缀
        base_href = request.path.rsplit('/', 1)[0] + '/' if '/' in request.path.lstrip('/') else request.path
        try:
            text = content.decode("utf-8", errors="replace")
            base_tag = f'<base href="{base_href}">'
            if "<head>" in text:
                text = text.replace("<head>", "<head>" + base_tag, 1)
            elif re.search(r"<head[^>]*>", text):
                text = re.sub(r"<head([^>]*)>", r"<head\1>" + base_tag, text, count=1)
            else:
                text = base_tag + text
            content = text.encode("utf-8")
        except Exception:
            pass
    return Response(content, mimetype=mime)


def _registered_ports(terminal: WebTerminal, conversation_id: Optional[str] = None) -> set:
    ports = set()
    for item in _load_targets(terminal, conversation_id or _current_conversation_id(terminal)):
        if item.get("type") != "server":
            continue
        match = re.search(r":(\d{1,5})(?:/|$)", str(item.get("origin") or item.get("url") or ""))
        if match:
            ports.add(int(match.group(1)))
    return ports


def _rewrite_html(text: str, proxy_prefix: str) -> str:
    """HTML 重写：把根绝对路径的 src/href/action 改道代理前缀 + 注入 <base>。

    顺序敏感：必须先改道再注入 <base>——否则 base 标签自身的 href 会被
    二次改写（base href="/api/.../" 命中改道规则变成双重前缀）。
    """
    # 根绝对路径（"/xxx"）改道代理；跳过协议相对（"//host"）
    text = re.sub(
        r'(src|href|action)\s*=\s*(["\'])/(?!/)',
        lambda m: f'{m.group(1)}={m.group(2)}{proxy_prefix}/',
        text,
    )
    base_tag = f'<base href="{proxy_prefix}/">'
    if "<head>" in text:
        text = text.replace("<head>", "<head>" + base_tag, 1)
    elif re.search(r"<head[^>]*>", text):
        text = re.sub(r"<head([^>]*)>", r"<head\1>" + base_tag, text, count=1)
    else:
        text = base_tag + text
    return text


@chat_bp.route('/api/preview/proxy/<int:port>/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@chat_bp.route('/api/preview/proxy/<int:port>/<path:sub_path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
@api_login_required
@with_terminal
@rate_limited("preview_proxy", 300, 60, scope="user")
def preview_proxy(terminal: WebTerminal, workspace: UserWorkspace, username: str, port: int, sub_path: str = ""):
    # 旧同源端点：保留给远端域名访问回退（本机访问走独立预览服务器，见文件头注释）
    if not is_preview_enabled():
        return jsonify({"success": False, "error": tr("preview.disabled_in_docker")}), 403
    return serve_proxy_response(terminal, port, sub_path)


def serve_proxy_response(terminal: WebTerminal, port: int, sub_path: str = "",
                         conversation_id: Optional[str] = None) -> Response:
    """dev server 反向代理响应（主应用与独立预览服务器共用的实现）。"""
    # SSRF 防护：只允许代理「当前对话已登记」的服务器端口，且目标固定 127.0.0.1
    if port not in _registered_ports(terminal, conversation_id):
        return jsonify({"success": False, "error": tr("preview.target_not_registered")}), 403

    target_url = f"http://127.0.0.1:{port}/{sub_path}"
    if request.query_string:
        target_url += "?" + request.query_string.decode("utf-8", errors="replace")

    # accept-encoding 一并剔除：httpx/curl 两条链路都保持原始字节流转，
    # 避免上游 gzip 后头体不一致
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
        and k.lower() not in ("host", "cookie", "accept-encoding")
    }
    # docker 模式：目标服务器在工具箱容器网络命名空间内，后端的 127.0.0.1
    # 到不了它，必须经 docker exec 在容器内取数；host 模式直连保真
    container = getattr(terminal, "container_session", None)
    docker_container = ""
    docker_bin = "docker"
    if container is not None and getattr(container, "mode", "") == "docker":
        docker_container = getattr(container, "container_name", None) or ""
        docker_bin = getattr(container, "sandbox_bin", None) or "docker"
    try:
        body = request.get_data() if request.method != "GET" else None
        if docker_container:
            status, upstream_headers, content = _docker_exec_fetch(
                docker_container, docker_bin, request.method, target_url, headers, body,
            )
        else:
            with httpx.Client(timeout=_PROXY_TIMEOUT, follow_redirects=True) as client:
                upstream = client.request(request.method, target_url, content=body, headers=headers)
                status, upstream_headers, content = (
                    upstream.status_code, dict(upstream.headers), upstream.content,
                )
        if len(content) > _PROXY_MAX_BYTES:
            return jsonify({"success": False, "error": tr("preview.response_too_large")}), 502
        content_type = ""
        for hk, hv in upstream_headers.items():
            if hk.lower() == "content-type":
                content_type = hv
                break
        # 代理前缀从当前请求路径推导：主应用是 /api/preview/proxy/<port>，
        # 独立预览服务器是 /<token>/proxy/<port>——不能写死
        m_prefix = re.match(rf"^(.*?/proxy/{port})", request.path)
        proxy_prefix = m_prefix.group(1) if m_prefix else f"/api/preview/proxy/{port}"
        if "text/html" in content_type:
            content = _rewrite_html(
                content.decode("utf-8", errors="replace"),
                proxy_prefix,
            ).encode("utf-8")
        resp_headers: Dict[str, str] = {}
        for k, v in upstream_headers.items():
            if k.lower() in _STRIP_HEADERS:
                continue
            if k.lower() == "location":
                v = _rewrite_location(v, proxy_prefix, port)
            resp_headers[k] = v
        return Response(content, status=status, headers=resp_headers)
    except httpx.ConnectError:
        return jsonify({"success": False, "error": tr("preview.server_unreachable", port=port)}), 502
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": tr("preview.server_unreachable", port=port)}), 502
    except Exception as exc:
        return jsonify({"success": False, "error": tr("preview.proxy_failed", detail=str(exc)[:200])}), 502
