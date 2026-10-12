"""Idle/startup status reads do not allocate execution resources."""
from __future__ import annotations

from config import HOST_EXECUTION_MODE_DEFAULT
from modules.memory_manager import MemoryManager
from modules.personalization_manager import load_personalization_config
from server.context.modes import mode_view
from server.context.resources import find_runtime


def workspace_status(data_context, conversation_id=None):
    terminal = find_runtime(data_context.username, data_context.workspace, conversation_id)
    if terminal is not None:
        return terminal.get_status(), terminal
    modes, pending, cid = mode_view(data_context, conversation_id)
    prefs = load_personalization_config(data_context.workspace.data_dir)
    run_mode = prefs.get('default_run_mode') or 'fast'
    manageable = data_context.identity.host_mode and data_context.identity.role == 'admin'
    memory = MemoryManager(str(data_context.workspace.data_dir), read_only=True).get_memory_stats()
    conversation = {'current_id': cid, 'total_conversations': 0, 'total_messages': 0, 'total_tools': 0}
    data = data_context.manager_for(cid).read_conversation(cid) if cid else None
    if data:
        conversation.update({key: data.get(key) for key in ('title', 'created_at', 'updated_at')})
    return {
        'thinking_mode': run_mode != 'fast', 'run_mode': run_mode,
        'thinking_status': '思考模式' if run_mode != 'fast' else '快速模式',
        'model_key': prefs.get('default_model'), 'reasoning_effort': prefs.get('default_reasoning_effort'),
        'permission_mode': modes['permission_mode'], 'work_mode': modes['work_mode'],
        'execution_mode': {'mode': modes['execution_mode'], 'default_mode': HOST_EXECUTION_MODE_DEFAULT},
        'network_permission': modes['network_permission'], 'pending_runtime_modes': pending,
        'execution_mode_enabled': manageable, 'network_permission_enabled': manageable,
        'focused_files': {}, 'focused_files_count': 0, 'terminals': {'terminals': [], 'active': None},
        'memory': {'main': memory['main_memory']['lines'], 'task': memory['task_memory']['lines']},
        'conversation': conversation, 'todo_list': data.get('todo_list') if data else None,
    }, None
