"""Workspace queries and idle-only conversation worktree changes."""
import asyncio
from pathlib import Path

from flask import jsonify, request

from modules.git_worktree import enabled
from modules.git_worktree.query import view
from modules.git_worktree.service import Worktrees
from modules.personalization_manager import load_personalization_config
from server.auth_helpers import api_login_required
from server.context import with_terminal
from server.context.data_decorators import with_workspace_data
from server.main_task_gate import try_acquire_main_task_gate, release_main_task_gate
from server.status import status_bp
from server.tasks import task_manager


def metadata_for(data_context, conversation_id):
    if not conversation_id:
        return None
    document = data_context.manager_for(conversation_id).read_conversation(conversation_id)
    if not document:
        raise ValueError('找不到当前工作区中的对话。')
    return document.get('metadata') or {}


@status_bp.get('/api/git-worktree')
@api_login_required
@with_workspace_data
def get_worktree(data_context, workspace, username):
    try:
        meta = metadata_for(data_context, request.args.get('conversation_id'))
        if meta is None:
            meta = {'git_worktree_enabled': load_personalization_config(workspace.data_dir).get('git_worktree_enabled') is True}
        if not enabled(meta):
            return jsonify(success=True, data={'enabled': False})
        return jsonify(success=True, data=view(workspace, meta))
    except (ValueError, OSError, RuntimeError) as exc:
        return jsonify(success=False, error=str(exc)), 400


@status_bp.post('/api/git-worktree/inspect')
@api_login_required
@with_terminal
def inspect_worktree_path(terminal, workspace, username):
    """只读：告知前端这个路径能否作为 enter 目标（不改绑定、不创建任何东西）。"""
    payload = request.get_json(silent=True) or {}
    path = payload.get('path')
    if not isinstance(path, str) or not path.strip():
        return jsonify(success=False, error='必须提供工作树路径。'), 400
    try:
        info = asyncio.run(Worktrees(terminal).describe(path.strip()))
    except (ValueError, OSError, RuntimeError) as exc:
        info = {'valid': False, 'error': str(exc)}
    return jsonify(success=True, data=info)


@status_bp.post('/api/git-worktree')
@api_login_required
@with_terminal
def change_worktree(terminal, workspace, username):
    payload = request.get_json(silent=True)
    cid = terminal.context_manager.current_conversation_id
    if not isinstance(payload, dict) or not cid or payload.get('conversation_id') != cid:
        return jsonify(success=False, error='必须提供当前对话身份。'), 400
    token = try_acquire_main_task_gate(terminal)
    if token is None:
        return jsonify(success=False, error='主智能体运行中，不能切换工作树。'), 409
    try:
        with task_manager._lock:
            busy = any(record.username == username and record.conversation_id == cid
                       and record.workspace_id == workspace.workspace_id
                       and record.status in {'pending', 'running', 'cancel_requested'}
                       for record in task_manager._tasks.values())
        if busy:
            return jsonify(success=False, error='主智能体运行中，不能切换工作树。'), 409
        operation = payload.get('action')
        if operation not in {'enter', 'exit'}:
            raise ValueError('无效的工作树操作。')
        result = asyncio.run(Worktrees(terminal).change(
            payload.get('target'), exit=operation == 'exit', notify_user=True, origin='user',
        ))
        return jsonify(result), 200 if result.get('success') else 400
    except (ValueError, OSError, RuntimeError) as exc:
        return jsonify(success=False, error=str(exc)), 400
    finally:
        release_main_task_gate(terminal, token)
