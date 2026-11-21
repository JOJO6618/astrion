from __future__ import annotations

import mimetypes
import secrets
from datetime import datetime
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, make_response, redirect, request, send_from_directory, session

from config import LOGS_DIR
from modules.host_workspace_manager import resolve_host_workspace
from modules.i18n import tr
from utils.log_rotation import append_line

from . import state
from .auth_helpers import is_logged_in, login_required, resolve_admin_policy
from .context.identity import NoWorkspaceError, RuntimeIdentity
from .context.workspace import resolve_workspace
from .entry_assets import serve_entry_html
from .host_password import authorize_host_login, public_host_status
from .security import get_csrf_token

auth_bp = Blueprint("auth", __name__)
AUTH_DEBUG_FILE = Path(LOGS_DIR).expanduser().resolve() / "auth_debug.log"


def auth_debug_log(message: str):
    try:
        append_line(AUTH_DEBUG_FILE, f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
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


def _issue_login_nonce():
    nonce = secrets.token_urlsafe(16)
    state.active_login_nonces['host'].add(nonce)
    session['login_nonce'] = nonce


def _clear_browser_session():
    username = session.get('username')
    nonce = session.get('login_nonce')
    pool = state.active_login_nonces.get(username)
    if pool is not None and nonce is not None:
        pool.discard(nonce)
    session.clear()


def _expire_session_cookie(response):
    cookie_name = current_app.config.get('SESSION_COOKIE_NAME', 'session')
    configured_domain = current_app.config.get('SESSION_COOKIE_DOMAIN')
    host = (request.host or '').split(':')[0]
    candidate_domains = {None, configured_domain, host}
    if host.count('.') >= 2:
        parent = '.'.join(host.split('.')[1:])
        candidate_domains.update({parent, f'.{parent}'})
    for domain in candidate_domains:
        try:
            response.delete_cookie(cookie_name, path='/', domain=domain)
        except (TypeError, ValueError):
            continue
    response.headers['Cache-Control'] = 'no-store'
    return response


@auth_bp.route('/api/csrf-token', methods=['GET'])
def issue_csrf_token():
    response = jsonify({'success': True, 'token': get_csrf_token()})
    response.headers['Cache-Control'] = 'no-store'
    return response


@auth_bp.route('/api/host-mode-enabled', methods=['GET'])
def host_mode_enabled():
    return jsonify(public_host_status())


@auth_bp.route('/login', methods=['GET'])
def login():
    if is_logged_in():
        return redirect('/new')
    if session.get('username'):
        _clear_browser_session()
        return _expire_session_cookie(serve_entry_html('login.html'))
    return serve_entry_html('login.html')


@auth_bp.route('/host-login', methods=['POST'])
def host_login():
    """Authenticate the browser and prepare the registered Host workspace paths."""
    credential_state, error = authorize_host_login()
    if error is not None:
        return error

    _, host_workspace = resolve_host_workspace()
    workspace_id = host_workspace['workspace_id'] if host_workspace else None
    if workspace_id:
        resolve_workspace('host', workspace_id, identity=RuntimeIdentity(host_mode=True), prepare=True)

    _clear_browser_session()
    session['host_auth_generation'] = credential_state.generation
    session['logged_in'] = True
    session['username'] = 'host'
    session['role'] = 'admin'
    session['host_mode'] = True
    if workspace_id:
        session['host_workspace_id'] = workspace_id
        session['workspace_id'] = workspace_id
    default_thinking = current_app.config.get('DEFAULT_THINKING_MODE', False)
    session['thinking_mode'] = default_thinking
    session['run_mode'] = current_app.config.get('DEFAULT_RUN_MODE', 'deep' if default_thinking else 'fast')
    session.permanent = True
    _issue_login_nonce()
    get_csrf_token(force_new=True)
    return jsonify({'success': True, 'workspace_required': not workspace_id})


@auth_bp.route('/logout', methods=['GET', 'POST'])
def logout():
    _clear_browser_session()
    auth_debug_log(f'[auth_debug] {request.method} /logout cleared browser session')
    if request.method == 'GET':
        return _expire_session_cookie(make_response(redirect('/login?logged_out=1')))
    return _expire_session_cookie(make_response(jsonify({'success': True})))


@auth_bp.route('/api/session-status', methods=['GET'])
def session_status():
    snapshot = _session_debug_snapshot()
    snapshot['logged_in'] = is_logged_in()
    return jsonify({'success': True, 'session': snapshot})


@auth_bp.route('/')
@login_required
def index():
    return redirect('/new')


@auth_bp.route('/new')
@login_required
def new_page():
    return serve_entry_html('index.html')


@auth_bp.route('/chat')
@auth_bp.route('/chat/new')
@auth_bp.route('/chat/<conv:conversation_id>')
@login_required
def chat_mode_page(conversation_id=None):
    return serve_entry_html('index.html')


@auth_bp.route('/chrome')
def chrome_page():
    # The desktop view loads this shell before the browser session is ready.
    return serve_entry_html('chrome.html')


@auth_bp.route('/browser-window')
def browser_window_page():
    # The desktop browser window checks the session through its protected APIs.
    return serve_entry_html('browser-window.html')


@auth_bp.route('/settings')
@auth_bp.route('/settings/<path:section>')
@login_required
def settings_page(section=None):
    return serve_entry_html('index.html')


@auth_bp.route('/<conv:conversation_id>')
@login_required
def conversation_page(conversation_id):
    return serve_entry_html('index.html')


@auth_bp.route('/terminal')
@login_required
def terminal_page():
    if resolve_admin_policy().get('ui_blocks', {}).get('block_realtime_terminal'):
        return tr('auth.terminal_blocked_by_admin'), 403
    return serve_entry_html('terminal.html')


def _serving_workspace():
    # Media requests use the URL to specify the visible workspace in each window.
    workspace_id = (request.args.get('workspace_id') or '').strip() or None
    try:
        return resolve_workspace('host', workspace_id)
    except (NoWorkspaceError, ValueError):
        abort(404)


def _workspace_file(root: Path, filename: str) -> Path:
    root = root.resolve()
    target = (root / filename).resolve()
    if not target.is_relative_to(root):
        abort(403)
    if not target.is_file():
        abort(404)
    return target


@auth_bp.route('/user_upload/<path:filename>')
@login_required
def serve_user_upload(filename: str):
    target = _workspace_file(_serving_workspace().uploads_dir, filename)
    return send_from_directory(str(target.parent), target.name)


@auth_bp.route('/workspace/<path:filename>')
@login_required
def serve_workspace_file(filename: str):
    target = _workspace_file(_serving_workspace().project_path, filename)
    mime_type, _ = mimetypes.guess_type(str(target))
    if not mime_type or not mime_type.startswith('image/'):
        abort(415)
    return send_from_directory(str(target.parent), target.name)


@auth_bp.route('/static/<path:filename>')
def static_files(filename):
    if filename.startswith('admin_dashboard'):
        abort(404)
    return send_from_directory('static', filename)


__all__ = ['auth_bp']
