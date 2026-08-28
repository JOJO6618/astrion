from __future__ import annotations
import mimetypes
import secrets
from pathlib import Path
from datetime import datetime
from flask import Blueprint, request, jsonify, session, redirect, send_from_directory, abort, current_app, make_response

from modules.personalization_manager import load_personalization_config
from modules.host_workspace_manager import resolve_host_workspace
from config import (
    TERMINAL_SANDBOX_MODE,
    DATA_DIR,
    LOGS_DIR,
    UPLOAD_QUARANTINE_SUBDIR,
    LINUX_SAFETY,
    LOGS_DIR,
)

from .auth_helpers import login_required, api_login_required, get_current_user_record, get_current_username, is_logged_in
from .security import (
    get_csrf_token,
    check_rate_limit,
    register_failure,
    is_action_blocked,
    clear_failures,
)
from . import state
from .utils_common import debug_log
from modules.i18n import tr

auth_bp = Blueprint("auth", __name__)
AUTH_DEBUG_FILE = Path(LOGS_DIR).expanduser().resolve() / "auth_debug.log"


def auth_debug_log(message: str):
    AUTH_DEBUG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    try:
        with AUTH_DEBUG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        debug_log(line)
    except Exception:
        pass


def _session_debug_snapshot():
    return {
        "logged_in": bool(session.get("logged_in")),
        "username": session.get("username"),
        "role": session.get("role"),
        "run_mode": session.get("run_mode"),
        "thinking_mode": session.get("thinking_mode"),
        "host_mode": bool(session.get("host_mode")),
    }


def _issue_login_nonce(username: str) -> str:
    nonce = secrets.token_urlsafe(16)
    user = (username or "").strip().lower()
    if user:
        state.active_login_nonces[user].add(nonce)
    session["login_nonce"] = nonce
    return nonce


def _revoke_login_nonce(username: str, nonce: str | None):
    user = (username or "").strip().lower()
    if not user or not nonce:
        return
    pool = state.active_login_nonces.get(user)
    if not pool:
        return
    pool.discard(nonce)
    if not pool:
        state.active_login_nonces.pop(user, None)


def _cookie_debug_snapshot():
    raw = request.headers.get("Cookie", "") or ""
    return raw[:300]


def _expire_session_cookie(response):
    """尽可能清理不同 domain/path 组合下的 session cookie（线上反向代理场景兜底）。"""
    cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "session")
    configured_domain = current_app.config.get("SESSION_COOKIE_DOMAIN")
    host = (request.host or "").split(":")[0]
    candidate_domains = {None, configured_domain, host}
    if host.count(".") >= 2:
        # 兼容 .example.com / example.com 两种历史写法
        parent = ".".join(host.split(".")[1:])
        candidate_domains.add(parent)
        candidate_domains.add(f".{parent}")
    for domain in candidate_domains:
        try:
            response.delete_cookie(cookie_name, path="/", domain=domain)
        except Exception:
            continue
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.route('/api/csrf-token', methods=['GET'])
def issue_csrf_token():
    token = get_csrf_token()
    response = jsonify({"success": True, "token": token})
    response.headers['Cache-Control'] = 'no-store'
    return response


@auth_bp.route('/api/host-mode-enabled', methods=['GET'])
def host_mode_enabled():
    enabled = (TERMINAL_SANDBOX_MODE or "").lower() == "host" and not LINUX_SAFETY
    return jsonify({"success": True, "enabled": enabled})


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        auth_debug_log(f"[auth_debug] GET /login session={_session_debug_snapshot()} cookie={_cookie_debug_snapshot()}")
        if is_logged_in():
            return redirect('/new')
        # 避免“session 内残留 username 但已失效”导致 /login <-> /new 重定向循环
        if session.get('username'):
            stale_username = session.get('username')
            stale_nonce = session.get('login_nonce')
            _revoke_login_nonce(stale_username, stale_nonce)
            session.clear()
            resp = make_response(current_app.send_static_file('login.html'))
            return _expire_session_cookie(resp)
        if not state.container_manager.has_capacity():
            return current_app.send_static_file('resource_busy.html'), 503
        return current_app.send_static_file('login.html')

    data = request.get_json() or {}
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    client_ip = request.headers.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote_addr or 'unknown'

    limited, retry_after = check_rate_limit("login", 10, 60, client_ip)
    if limited:
        return jsonify({"success": False, "error": tr("auth.login_rate_limited"), "retry_after": retry_after}), 429

    blocked, block_for = is_action_blocked("login", identifier=client_ip)
    if blocked:
        return jsonify({"success": False, "error": tr("auth.too_many_attempts", seconds=block_for), "retry_after": block_for}), 429

    record = state.user_manager.authenticate(email, password)
    if not record:
        wait_seconds = register_failure("login", state.FAILED_LOGIN_LIMIT, state.FAILED_LOGIN_LOCK_SECONDS, identifier=client_ip)
        error_payload = {"success": False, "error": tr("auth.invalid_credentials")}
        status_code = 401
        if wait_seconds:
            error_payload.update({"error": tr("auth.too_many_attempts", seconds=wait_seconds), "retry_after": wait_seconds})
            status_code = 429
        return jsonify(error_payload), status_code

    workspace = state.user_manager.ensure_user_workspace(record.username)
    preferred_run_mode = None
    try:
        personal_config = load_personalization_config(workspace.data_dir)
        candidate_mode = (personal_config or {}).get('default_run_mode')
        if isinstance(candidate_mode, str):
            normalized_mode = candidate_mode.lower()
            if normalized_mode == "deep":  # 旧版标识符映射
                normalized_mode = "thinking"
            if normalized_mode in {"fast", "thinking"}:
                preferred_run_mode = normalized_mode
    except Exception as exc:
        debug_log(f"加载个性化偏好失败: {exc}")

    # 清理旧会话（尤其是从 host-login 切换到普通登录时遗留的 host_mode）
    prev_username = session.get('username')
    prev_nonce = session.get('login_nonce')
    _revoke_login_nonce(prev_username, prev_nonce)
    session.clear()

    session['logged_in'] = True
    session['username'] = record.username
    session['role'] = record.role or 'user'
    session['host_mode'] = False
    session['workspace_id'] = 'default'
    default_thinking = current_app.config.get('DEFAULT_THINKING_MODE', False)
    session['thinking_mode'] = default_thinking
    session['run_mode'] = current_app.config.get('DEFAULT_RUN_MODE', "deep" if default_thinking else "fast")
    if preferred_run_mode:
        session['run_mode'] = preferred_run_mode
        session['thinking_mode'] = preferred_run_mode != 'fast'
    session.permanent = True
    _issue_login_nonce(record.username)
    clear_failures("login", identifier=client_ip)
    try:
        state.container_manager.ensure_container(
            record.username,
            str(workspace.project_path),
            container_key=f"{record.username}::default",
            preferred_mode="docker",
        )
    except RuntimeError as exc:
        session.clear()
        return jsonify({"success": False, "error": str(exc), "code": "resource_busy"}), 503
    from .usage import record_user_activity
    record_user_activity(record.username)
    get_csrf_token(force_new=True)
    return jsonify({"success": True})


@auth_bp.route('/host-login', methods=['POST'])
def host_login():
    """宿主机模式一键进入（仅当 TERMINAL_SANDBOX_MODE=host 时可用）。"""
    if (TERMINAL_SANDBOX_MODE or "").lower() != "host":
        return jsonify({"success": False, "error": tr("auth.host_mode_disabled")}), 403
    if not state.container_manager.has_capacity("host"):
        return jsonify({"success": False, "error": tr("auth.resource_busy")}), 503

    _, host_workspace = resolve_host_workspace()
    # 初始化 session，跳过账号体系
    session.clear()
    session['logged_in'] = True
    session['username'] = 'host'
    session['role'] = 'admin'
    session['host_mode'] = True

    workspace_required = False
    if host_workspace:
        host_workspace_id = host_workspace.get("workspace_id") or "default"
        host_path = Path(host_workspace.get("path") or "").expanduser().resolve()
        host_path.mkdir(parents=True, exist_ok=True)
        uploads_dir = host_path / ".astrion" / "user_upload"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        quarantine_root = Path(UPLOAD_QUARANTINE_SUBDIR).expanduser()
        if not quarantine_root.is_absolute():
            quarantine_root = (host_path.parent / UPLOAD_QUARANTINE_SUBDIR).resolve()
        quarantine_root.mkdir(parents=True, exist_ok=True)
        session['host_workspace_id'] = host_workspace_id
        session['workspace_id'] = host_workspace_id
    else:
        # 没有任何工作区：允许先登录，由用户在前端手动创建工作区
        workspace_required = True

    data_dir = Path(DATA_DIR).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = Path(LOGS_DIR).expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    default_thinking = current_app.config.get('DEFAULT_THINKING_MODE', False)
    session['thinking_mode'] = default_thinking
    session['run_mode'] = current_app.config.get('DEFAULT_RUN_MODE', "deep" if default_thinking else "fast")
    session.permanent = True
    _issue_login_nonce('host')

    # 预先创建宿主机模式的终端/容器句柄（host 模式不会启动 Docker）
    # 无工作区时跳过，待用户创建工作区并选择后再初始化
    if not workspace_required:
        try:
            state.container_manager.ensure_container(
                "host",
                str(host_path),
                container_key=f"host::{host_workspace_id}",
                preferred_mode="host",
            )
        except RuntimeError as exc:
            session.clear()
            return jsonify({"success": False, "error": str(exc)}), 503

    get_csrf_token(force_new=True)
    return jsonify({"success": True, "workspace_required": workspace_required})


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        auth_debug_log(f"[auth_debug] GET /register session={_session_debug_snapshot()} cookie={_cookie_debug_snapshot()}")
        if is_logged_in():
            auth_debug_log("[auth_debug] GET /register redirected to /new because session.username exists")
            return redirect('/new')
        if session.get('username'):
            stale_username = session.get('username')
            stale_nonce = session.get('login_nonce')
            _revoke_login_nonce(stale_username, stale_nonce)
            session.clear()
            resp = make_response(current_app.send_static_file('register.html'))
            return _expire_session_cookie(resp)
        return current_app.send_static_file('register.html')

    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    password = data.get('password') or ''
    invite_code = (data.get('invite_code') or '').strip()

    from .security import get_client_ip
    limited, retry_after = check_rate_limit("register", 5, 300, get_client_ip())
    if limited:
        return jsonify({"success": False, "error": tr("auth.register_rate_limited"), "retry_after": retry_after}), 429
    try:
        state.user_manager.register_user(username, email, password, invite_code)
        auth_debug_log(f"[auth_debug] POST /register success username={username}")
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@auth_bp.route('/logout', methods=['GET', 'POST'])
def logout():
    auth_debug_log(
        f"[auth_debug] {request.method} /logout before_clear "
        f"session={_session_debug_snapshot()} cookie={_cookie_debug_snapshot()}"
    )
    username = session.get('username')
    login_nonce = session.get('login_nonce')
    session.clear()
    _revoke_login_nonce(username, login_nonce)
    if username:
        # 清理该用户相关的所有终端/容器（包含 API 多工作区）
        term_keys = [k for k in list(state.user_terminals.keys()) if k == username or k.startswith(f"{username}::")]
        for key in term_keys:
            state.user_terminals.pop(key, None)
            try:
                state.container_manager.release_container(key, reason="logout")
            except Exception:
                pass
        for token_value, meta in list(state.pending_socket_tokens.items()):
            if meta.get("username") == username:
                state.pending_socket_tokens.pop(token_value, None)
    auth_debug_log(f"[auth_debug] {request.method} /logout after_clear session={_session_debug_snapshot()}")
    if request.method == 'GET':
        resp = make_response(redirect('/login?logged_out=1'))
        resp = _expire_session_cookie(resp)
        auth_debug_log(f"[auth_debug] GET /logout set-cookie={resp.headers.get('Set-Cookie', '')[:300]}")
        return resp
    resp = make_response(jsonify({"success": True}))
    resp = _expire_session_cookie(resp)
    auth_debug_log(f"[auth_debug] POST /logout set-cookie={resp.headers.get('Set-Cookie', '')[:300]}")
    return resp


@auth_bp.route('/api/session-status', methods=['GET'])
def session_status():
    """前端调试用：查看当前会话是否已清理。"""
    snapshot = _session_debug_snapshot()
    auth_debug_log(f"[auth_debug] GET /api/session-status session={snapshot} cookie={_cookie_debug_snapshot()}")
    return jsonify({"success": True, "session": snapshot})


@auth_bp.route('/api/tutorial-status', methods=['GET'])
@api_login_required
def get_tutorial_status():
    username = (get_current_username() or "").strip().lower()
    if not username:
        return jsonify({"success": False, "error": tr("auth.not_logged_in")}), 401

    if bool(session.get("host_mode")) or username == "host":
        return jsonify({
            "success": True,
            "data": {
                "username": username,
                "applicable": False,
                "tutorial_completed": True,
                "should_prompt": False,
            }
        })

    record = state.user_manager.get_user(username)
    if not record:
        return jsonify({
            "success": True,
            "data": {
                "username": username,
                "applicable": False,
                "tutorial_completed": True,
                "should_prompt": False,
            }
        })

    completed = bool(getattr(record, "tutorial_completed", False))
    return jsonify({
        "success": True,
        "data": {
            "username": record.username,
            "applicable": True,
            "tutorial_completed": completed,
            "should_prompt": not completed,
        }
    })


@auth_bp.route('/api/tutorial-status', methods=['POST'])
@api_login_required
def set_tutorial_status():
    username = (get_current_username() or "").strip().lower()
    if not username:
        return jsonify({"success": False, "error": tr("auth.not_logged_in")}), 401
    if bool(session.get("host_mode")) or username == "host":
        return jsonify({"success": False, "error": tr("auth.host_mode_tutorial_not_applicable")}), 400

    payload = request.get_json(silent=True) or {}
    completed = payload.get("tutorial_completed", True)
    record = state.user_manager.set_tutorial_completed(username, bool(completed))
    if not record:
        return jsonify({"success": False, "error": tr("auth.user_not_found")}), 404
    return jsonify({
        "success": True,
        "data": {
            "username": record.username,
            "tutorial_completed": bool(record.tutorial_completed),
        }
    })


@auth_bp.route('/')
@login_required
def index():
    return redirect('/new')


@auth_bp.route('/new')
@login_required
def new_page():
    return current_app.send_static_file('index.html')


@auth_bp.route('/<conv:conversation_id>')
@login_required
def conversation_page(conversation_id):
    return current_app.send_static_file('index.html')


@auth_bp.route('/terminal')
@login_required
def terminal_page():
    from .auth_helpers import resolve_admin_policy
    policy = resolve_admin_policy(get_current_user_record())
    if policy.get("ui_blocks", {}).get("block_realtime_terminal"):
        return tr("auth.terminal_blocked_by_admin"), 403
    return current_app.send_static_file('terminal.html')


@auth_bp.route('/user_upload/<path:filename>')
@login_required
def serve_user_upload(filename: str):
    user = get_current_user_record()
    if not user:
        return redirect('/login')
    workspace = state.user_manager.ensure_user_workspace(
        user.username,
        session.get("workspace_id") or "default",
    )
    uploads_dir = workspace.uploads_dir.resolve()
    target = (uploads_dir / filename).resolve()
    try:
        target.relative_to(uploads_dir)
    except ValueError:
        abort(403)
    if not target.exists() or not target.is_file():
        abort(404)
    return send_from_directory(str(uploads_dir), str(target.relative_to(uploads_dir)))


@auth_bp.route('/workspace/<path:filename>')
@login_required
def serve_workspace_file(filename: str):
    user = get_current_user_record()
    if not user:
        return redirect('/login')
    workspace = state.user_manager.ensure_user_workspace(
        user.username,
        session.get("workspace_id") or "default",
    )
    project_root = workspace.project_path.resolve()
    target = (project_root / filename).resolve()
    try:
        target.relative_to(project_root)
    except ValueError:
        abort(403)
    if not target.exists() or not target.is_file():
        abort(404)
    mime_type, _ = mimetypes.guess_type(str(target))
    if not mime_type or not mime_type.startswith("image/"):
        abort(415)
    return send_from_directory(str(target.parent), target.name)


@auth_bp.route('/static/<path:filename>')
def static_files(filename):
    if filename.startswith('admin_dashboard'):
        abort(404)
    return send_from_directory('static', filename)

__all__ = ["auth_bp"]
