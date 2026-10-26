"""Manage only Chat memory, never Agent's global/project memory."""
from flask import jsonify, request
from server.auth_helpers import api_login_required
from modules.chat_mode.memory import ChatMemory
from . import chat_mode_bp
from .context import resources, body


@chat_mode_bp.route("/memory", methods=["GET", "POST"])
@api_login_required
def memory_api():
    _, scope, _ = resources()
    memory = ChatMemory(scope.root)
    if request.method == "POST":
        payload = body()
        if payload.get("kind") == "topic":
            memory.write_topic(name=payload.get("name"), description=payload.get("description"),
                               content=payload.get("content"))
        else:
            memory.update(operation=payload.get("operation"), content=payload.get("content"),
                          index=payload.get("index"))
    return jsonify(success=True, data=memory.snapshot())
