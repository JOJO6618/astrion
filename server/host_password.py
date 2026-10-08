"""Host browser authentication policy and settings endpoints; CLI stays separate."""
from __future__ import annotations

import os

from flask import Blueprint, g, jsonify, request, session

from config import DATA_DIR, TERMINAL_SANDBOX_MODE, LINUX_SAFETY
from modules.host_auth import (
    HostAuthChanged, HostAuthError, HostAuthState, disable_host_password,
    load_host_auth, verify_host_password,
)
from modules.i18n import tr
from server.auth_helpers import api_login_required
from server.security import (
    check_rate_limit, clear_failures, get_client_ip,
    is_action_blocked, register_failure,
)

host_password_bp = Blueprint("host_password", __name__)
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def host_enabled() -> bool:
    return (TERMINAL_SANDBOX_MODE or "").lower() == "host" and not LINUX_SAFETY


def is_desktop_backend() -> bool:
    # Set by both native shells when spawning their loopback-only backend.
    # Browser JS flags, request headers and query parameters cannot grant this.
    return bool(os.environ.get("ASTRION_DESKTOP_VERSION"))


def web_auth_state() -> HostAuthState:
    if not host_enabled() or is_desktop_backend():
        return HostAuthState()
    return load_host_auth(DATA_DIR)


def browser_session_valid() -> bool:
    if getattr(g, "host_bearer_authenticated", False):
        return True
    if not host_enabled() or is_desktop_backend():
        return True
    try:
        state = web_auth_state()
    except HostAuthError:
        return False
    if state.enabled and not session.get("host_mode"):
        return False
    if session.get("host_mode"):
        return session.get("host_auth_generation", "") == state.generation
    return not state.enabled


def public_host_status() -> dict:
    try:
        state = web_auth_state()
        return {"success": True, "enabled": host_enabled(),
                "password_required": state.enabled, "config_valid": True}
    except HostAuthError:
        return {"success": True, "enabled": host_enabled(),
                "password_required": True, "config_valid": False,
                "error": tr("auth.host_password_unavailable")}


def ordinary_login_allowed() -> bool:
    try:
        return not web_auth_state().enabled
    except HostAuthError:
        return False


def _password_check(state: HostAuthState, password: object):
    ip = get_client_ip()
    for action, limit, identifier in (("host_password", 10, ip),
                                      ("host_password_total", 30, "host")):
        limited, retry = check_rate_limit(action, limit, 60, identifier)
        if limited:
            return jsonify(success=False, error=tr("auth.login_rate_limited"), retry_after=retry), 429
    blocked, retry = is_action_blocked("host_password", identifier=ip)
    if blocked:
        return jsonify(success=False, error=tr("auth.too_many_attempts", seconds=retry), retry_after=retry), 429
    if not verify_host_password(state, password):
        register_failure("host_password", 5, 300, identifier=ip)
        return jsonify(success=False, error=tr("auth.host_password_incorrect")), 401
    clear_failures("host_password", identifier=ip)
    return None


def authorize_host_login():
    """Return the exact verified credential generation and an optional error."""
    try:
        state = web_auth_state()
        if state.enabled:
            data = request.get_json(silent=True)
            password = data.get("password") if isinstance(data, dict) else None
            return state, _password_check(state, password)
        if (request.remote_addr or "").strip() not in _LOOPBACK:
            return state, (jsonify(success=False, error=tr("auth.host_mode_disabled")), 403)
        return state, None
    except HostAuthError:
        return None, (jsonify(success=False, error=tr("auth.host_password_unavailable")), 503)


def _settings_applicable() -> bool:
    return host_enabled() and not is_desktop_backend() and bool(session.get("host_mode"))


@host_password_bp.get("/api/host-password")
@api_login_required
def get_host_password_status():
    if not _settings_applicable():
        return jsonify(success=True, applicable=False)
    try:
        return jsonify(success=True, applicable=True, enabled=web_auth_state().enabled)
    except HostAuthError:
        return jsonify(success=False, error=tr("auth.host_password_unavailable")), 503


@host_password_bp.post("/api/host-password/disable")
@api_login_required
def disable_host_password_protection():
    if not _settings_applicable():
        return jsonify(success=False, error=tr("auth.host_mode_disabled")), 403
    try:
        state = web_auth_state()
        if not state.enabled:
            return jsonify(success=False, error=tr("auth.host_password_not_enabled")), 409
        data = request.get_json(silent=True)
        password = data.get("password") if isinstance(data, dict) else None
        error = _password_check(state, password)
        if error is not None:
            return error
        # Reverify password and generation under the interprocess lock.
        disable_host_password(DATA_DIR, password, expected_generation=state.generation)
    except HostAuthChanged:
        return jsonify(success=False, error=tr("auth.session_expired")), 401
    except HostAuthError:
        return jsonify(success=False, error=tr("auth.host_password_incorrect")), 409
    except OSError:
        return jsonify(success=False, error=tr("auth.host_password_unavailable")), 503
    session.clear()
    return jsonify(success=True, enabled=False)
