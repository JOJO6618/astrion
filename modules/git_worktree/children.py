"""Persistent child updates consumed exclusively at model/tool batch boundaries."""
from dataclasses import replace
from pathlib import Path

from modules.scoped_execution_policy import fixed_execution_root
from . import child_environment


def queue_binding(manager, state):
    parent = manager.terminal
    if parent is None:
        raise RuntimeError('子智能体管理器尚未绑定父对话，保留环境同步记录。')
    cid = (getattr(parent, '_bound_conversation_id', None)
           or parent.context_manager.current_conversation_id)
    with manager._state_lock:
        for record in manager.tasks.values():
            if record.get('conversation_id') != cid or record.get('status') == 'terminated':
                continue
            if int(record.get('worktree_revision') or 0) >= int(state.get('revision') or 0):
                continue
            pending = record.get('pending_worktree_binding') or {}
            if int(pending.get('revision') or 0) <= int(state.get('revision') or 0):
                record['pending_worktree_binding'] = dict(state)
        manager._save_state()


def apply_pending(instance):
    # Only the child event loop calls this, after all tool results are filled in.
    with instance._work_lock:
        state = instance.task_record.get('pending_worktree_binding')
        if not isinstance(state, dict):
            return False
        state = dict(state)
        directory = state.get('directory')
        if not directory:
            raise ValueError('待同步的工作树缺少执行目录。')
        scope = replace(instance.execution_scope, execution_root=Path(directory))
        fixed_execution_root(scope)
        content = '[工作树环境变更]\n' + child_environment(
            directory, state.get('branch'), access_level=scope.access_level, working_dir=scope.working_dir)
        revision = state.get('revision', 0)
        # A retry after registry-write failure must not duplicate a durable notice.
        if not any(message.get('name') == f'worktree_change_{revision}' for message in instance.messages if message.get('role') == 'user'):
            instance.messages.append({'role': 'user', 'content': content, 'name': f'worktree_change_{revision}'})
        instance._persist_conversation(strict=True)
        # Preserve pending in the registry until the notice itself is durable.
        instance.execution_scope = scope
        instance._execution_terminal = None  # Child toolkit exposes no persistent terminal tools.
        instance.task_record['execution_root'] = directory
        instance.task_record['worktree_revision'] = revision
        with instance.manager._state_lock:
            if instance.task_record.get('pending_worktree_binding') == state:
                instance.task_record.pop('pending_worktree_binding', None)
            try:
                instance.manager._save_state()
            except Exception:
                instance.task_record['pending_worktree_binding'] = state
                raise
    return True


def sync_before_work(instance):
    try:
        apply_pending(instance)
        fixed_execution_root(instance.execution_scope)
        return True
    except (ValueError, OSError, RuntimeError) as exc:
        # Do not consume queued messages or issue another model request on the old
        # root. Sending new work later can retry after selecting a valid binding.
        instance._work_paused = True
        instance.task_record['work_paused'] = True
        instance._finish_round(f'工作树环境同步失败，任务消息已保留：{exc}', reason='error')
        return False
