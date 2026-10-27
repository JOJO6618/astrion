"""Authenticated workspace-free Chat HTTP surface."""
from flask import Blueprint, jsonify
from config import IS_HOST_MODE
from .handoff_view import public_error

chat_mode_bp = Blueprint("chat_mode", __name__, url_prefix="/api/chat")


@chat_mode_bp.errorhandler(ValueError)
def invalid_request(error):
    return jsonify(success=False, error=public_error(error, host_mode=IS_HOST_MODE)), 400


@chat_mode_bp.errorhandler(FileNotFoundError)
def missing_resource(error):
    return jsonify(success=False, error=public_error(error, host_mode=IS_HOST_MODE)), 404


@chat_mode_bp.errorhandler(PermissionError)
def forbidden(error):
    return jsonify(success=False, error=public_error(error, host_mode=IS_HOST_MODE)), 403


@chat_mode_bp.errorhandler(RuntimeError)
def conflict(error):
    return jsonify(success=False, error=public_error(error, host_mode=IS_HOST_MODE)), 409


from . import conversations, drafts, handoff, media, memory, settings, tasks  # noqa: E402,F401
