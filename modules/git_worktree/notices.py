"""Publish user-initiated changes through the existing runtime notice mechanism."""
from . import notification


def consume(owner, *, sender, conversation_id):
    cm = owner.context_manager
    state = (cm.conversation_metadata or {}).get('git_worktree_notice')
    if not isinstance(state, dict):
        return
    manager = cm._get_conversation_manager_for_id(conversation_id)
    document = manager.read_conversation(conversation_id) or {}
    delivered = any(
        message.get('role') == 'user'
        and (message.get('metadata') or {}).get('source') == 'execution_change'
        and (message.get('metadata') or {}).get('worktree_revision') == state.get('revision')
        for message in document.get('messages', [])
    )
    if not delivered:
        from server.chat_flow_task_support import inject_runtime_user_message
        inserted = inject_runtime_user_message(
            web_terminal=owner, messages=None, text=notification(state), source='execution_change',
            sender=sender, conversation_id=conversation_id, inline=False, require_persist=True,
            extra_metadata={'worktree_revision': state.get('revision'), 'starts_work': False},
        )
        if not inserted:
            raise RuntimeError('工作树变更通知未能写入。')

    def acknowledge(document):
        meta = document['metadata']
        if meta.get('git_worktree_notice') == state:
            meta.pop('git_worktree_notice', None)

    manager.mutate_conversation(conversation_id, acknowledge)
    if cm.conversation_metadata.get('git_worktree_notice') == state:
        cm.conversation_metadata.pop('git_worktree_notice', None)
