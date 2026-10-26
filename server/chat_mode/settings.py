"""Account Chat defaults and sanitized model catalog; no runtime provisioning."""
from flask import jsonify, request
from config.model_profiles import get_registered_model_profiles, get_default_model_key
from modules.personalization_manager import load_personalization_config, save_personalization_config
from modules.chat_mode.settings import CHAT_DEFAULTS, CHAT_FIELDS, SHARED_FIELDS
from server.auth_helpers import api_login_required
from . import chat_mode_bp
from .context import resources, body


def settings_payload(scope):
    preferences = load_personalization_config(scope.preferences_dir)
    hidden = set(preferences.get("hidden_models") or [])
    profiles = get_registered_model_profiles()
    models = [{"key": key, "name": value.get("display_name") or value.get("name") or key,
               "api_protocol": value.get("api_protocol", "chat"),
               "supports_thinking": value.get("supports_thinking", False),
               "supports_images": value.get("supports_images", False),
               "supports_videos": value.get("supports_videos", False),
               "provider_id": value.get("provider_id"),
               "provider_name": value.get("provider_name"),
               "context_window": value.get("context_window", 128000)}
              for key, value in profiles.items() if key not in hidden and not value.get("hidden")]
    settings = {key: preferences.get(key, default) for key, default in CHAT_DEFAULTS.items()}
    if not settings["chat_default_model"] or settings["chat_default_model"] not in profiles:
        settings["chat_default_model"] = get_default_model_key() if profiles else ""
    # Same account preferences consumed by the shared UI store. Runtime resources
    # and credentials are stored separately; reading this payload provisions none.
    return {"settings": settings, "models": models,
            "personalization": preferences,
            "theme": preferences.get("theme"), "username": scope.username}


@chat_mode_bp.route("/settings", methods=["GET", "PATCH"])
@api_login_required
def settings_api():
    _, scope, _ = resources()
    if request.method == "PATCH":
        payload = body()
        if set(payload) - (CHAT_FIELDS | SHARED_FIELDS):
            raise ValueError("Unsupported Chat preference")
        save_personalization_config(scope.preferences_dir, payload)
    return jsonify(success=True, data=settings_payload(scope))
