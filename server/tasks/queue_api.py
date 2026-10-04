"""对话级持久队列的空闲管理面。"""
from flask import jsonify, request

from server.auth_helpers import get_current_username
from server.context import get_user_resources
from server.gateway_auth import api_login_or_host_token_required
from server.tasks import task_manager
from server.tasks.blueprint import tasks_bp
from server.tasks.queue_state import QUEUE_KEY, guidance_to_pending, queue_snapshot


@tasks_bp.route("/api/conversations/<conversation_id>/runtime_queue/<message_id>", methods=["DELETE"])
@api_login_or_host_token_required
def delete_conversation_queue_message(conversation_id, message_id):
    username = get_current_username()
    cid = conversation_id if conversation_id.startswith("conv_") else f"conv_{conversation_id}"
    workspace_id = request.args.get("workspace_id") or None
    terminal, workspace = get_user_resources(username, workspace_id=workspace_id, conversation_id=cid)
    manager = terminal.context_manager._get_conversation_manager_for_id(cid)
    # 活跃任务的内存队列和磁盘必须同时修改，不能只删磁盘后被任务写回。
    tasks = sorted(task_manager.list_tasks(username, workspace.workspace_id), key=lambda rec: rec.created_at, reverse=True)
    rec = next((rec for rec in tasks if rec.conversation_id == cid), None)
    if rec:
        result = task_manager.remove_runtime_pending_message(username, rec.task_id, message_id)
        return jsonify({"success": result.get("success", False), "data": result}), (200 if result.get("success") else 404)
    with manager._io_lock:
        data = manager.load_conversation(cid)
        if not data:
            return jsonify({"success": False}), 404
        state = data.setdefault("metadata", {}).setdefault(QUEUE_KEY, {})
        pending = state.get("pending") or []
        guidance = guidance_to_pending(state.get("guidance"))
        kept = [item for item in pending if item.get("id") != message_id]
        kept_guidance = [item for item in guidance if item.get("id") != message_id]
        if len(kept) == len(pending) and len(kept_guidance) == len(guidance):
            return jsonify({"success": False}), 404
        state["pending"] = kept
        state["guidance"] = kept_guidance
        manager._atomic_write_json(manager._get_conversation_file_path(cid), data)
    return jsonify({"success": True, "data": queue_snapshot(manager, cid)})
