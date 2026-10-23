"""Shared child-agent configuration; conversation types and role APIs are removed."""
from flask import Blueprint, jsonify, request

from server.auth_helpers import api_login_required
from server.context.data_decorators import with_workspace_data

multi_agent_bp = Blueprint("sub_agent_config", __name__)


@multi_agent_bp.route("/api/sub_agents/settings", methods=["GET", "PUT"])
@api_login_required
@with_workspace_data
def sub_agent_settings(data_context, workspace, username):
    from modules.personalization_manager import load_personalization_config, save_personalization_config
    prefs = load_personalization_config(workspace.data_dir) or {}
    if request.method == "PUT":
        payload = request.get_json(silent=True) or {}
        settings = payload.get("settings")
        if not isinstance(settings, dict):
            return jsonify(success=False, error="settings必须为对象。"), 400
        threshold = settings.get("sub_agent_compress_threshold_tokens")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 10000:
            return jsonify(success=False, error="子智能体压缩阈值必须是不小于10000的整数。"), 400
        prefs["sub_agent_compress_threshold_tokens"] = threshold
        save_personalization_config(workspace.data_dir, prefs)
    return jsonify(success=True, settings={
        "sub_agent_compress_threshold_tokens": prefs.get("sub_agent_compress_threshold_tokens", 250000),
    })


@multi_agent_bp.route("/api/sub_agents/models", methods=["GET"])
@api_login_required
def sub_agent_models():
    from modules.aux_model_resolver import list_aux_models
    return jsonify(success=True, models=list_aux_models())
