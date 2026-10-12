"""Effective mode views share execution's normalization and pending state."""
from __future__ import annotations

from config import HOST_EXECUTION_MODE_DEFAULT, HOST_SANDBOX_NETWORK_PERMISSION
from modules.conversation_modes import resolve_conversation_modes, runtime_modes
from modules.personalization_manager import load_personalization_config
from server.context.resources import find_runtime


class ConversationModeNotFound(ValueError):
    """The requested mode view does not belong to this workspace."""


def _bound_runtime(data_context, conversation_id):
    terminal = find_runtime(data_context.username, data_context.workspace, conversation_id)
    if terminal is not None and (not conversation_id or terminal.context_manager.current_conversation_id == conversation_id):
        return terminal
    return None


def effective_modes(data_context, metadata, conversation_id):
    terminal = _bound_runtime(data_context, conversation_id)
    if terminal is not None:
        return runtime_modes(terminal)
    prefs = load_personalization_config(data_context.workspace.data_dir)
    return resolve_conversation_modes(metadata, {
        'work_mode': prefs.get('default_work_mode'),
        'permission_mode': prefs.get('default_permission_mode'),
        'execution_mode': HOST_EXECUTION_MODE_DEFAULT,
        'network_permission': HOST_SANDBOX_NETWORK_PERMISSION,
    }, host_mode=data_context.workspace.username == 'host')


def pending_modes(data_context, metadata, conversation_id):
    terminal = _bound_runtime(data_context, conversation_id)
    return terminal.get_pending_runtime_modes() if terminal else {
        key: metadata.get(f'pending_{key}')
        for key in ('permission_mode', 'execution_mode', 'network_permission')}


def mode_view(data_context, conversation_id=None):
    metadata = {}
    if conversation_id:
        conversation_id = str(conversation_id)
        if not conversation_id.startswith('conv_'):
            conversation_id = f'conv_{conversation_id}'
        data = data_context.manager_for(conversation_id).read_conversation(conversation_id)
        if data is None:
            raise ConversationModeNotFound('Conversation not found')
        metadata = data.get('metadata') or {}
    return (effective_modes(data_context, metadata, conversation_id),
            pending_modes(data_context, metadata, conversation_id), conversation_id)
