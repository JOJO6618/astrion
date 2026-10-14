"""Host-wide opt-in CrossPet export settings; no execution instance is created."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from config import IS_HOST_MODE
from modules.crosspet.observer import read_destination
from modules.crosspet.settings import read_enabled, save_enabled
from server.auth_helpers import api_login_required, admin_api_required, is_admin_user
from server.crosspet import OBSERVER

crosspet_settings_bp = Blueprint("crosspet_settings", __name__)
LOGGER = logging.getLogger(__name__)


@crosspet_settings_bp.get("/api/crosspet")
@api_login_required
def get_crosspet_settings():
    supported = IS_HOST_MODE and is_admin_user()
    if not supported:
        return jsonify(success=True, supported=False, enabled=False)
    try:
        return jsonify(success=True, supported=True, enabled=read_enabled(OBSERVER.settings_path))
    except (OSError, ValueError) as exc:
        LOGGER.warning("CrossPet preference read failed (%s)", type(exc).__name__)
        return jsonify(success=False, error="CrossPet settings are unavailable"), 503


@crosspet_settings_bp.post("/api/crosspet")
@api_login_required
@admin_api_required
def update_crosspet_settings():
    if not IS_HOST_MODE:
        return jsonify(success=False, error="CrossPet requires host mode"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        return jsonify(success=False, error="enabled must be a boolean"), 400
    enabled = data["enabled"]
    try:
        if enabled:
            destination = read_destination(OBSERVER.config_path)
            if destination is None:
                raise ValueError("Invalid CrossPet state directory")
            OBSERVER._check_targets(destination)
        save_enabled(OBSERVER.settings_path, enabled)
        if enabled:
            OBSERVER.ensure_started()
        else:
            OBSERVER.wake.set()
        return jsonify(success=True, supported=True, enabled=enabled)
    except (OSError, ValueError) as exc:
        LOGGER.warning("CrossPet preference update failed (%s)", type(exc).__name__)
        return jsonify(success=False, error=str(exc)), 400
