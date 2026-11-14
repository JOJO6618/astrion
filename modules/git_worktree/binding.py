"""Change execution defaults without moving workspace identity or live processes."""
from pathlib import Path
from typing import Any

from . import current_state, enabled


def checked_root(owner: Any, state: dict) -> Path:
    raw = Path(state.get('directory') or owner.project_path)
    root = raw.resolve()
    root.relative_to(Path(owner.project_path).resolve())
    if (state.get('directory') and raw != root) or not root.is_dir():
        raise ValueError('工作树执行目录已失效或被重定向，请先返回主目录或选择有效工作树；拒绝回退执行。')
    return root


def apply_binding(owner: Any, state: dict) -> None:
    root = checked_root(owner, state)
    owner.execution_root = str(root)
    owner.execution_binding_error = None
    for name in ('file_manager', 'terminal_ops', 'terminal_manager', 'ocr_client'):
        resource = getattr(owner, name, None)
        if resource is not None:
            resource.execution_root = root
            resource.execution_binding_error = None


def _sync_children(owner: Any, state: dict) -> None:
    children = getattr(owner, 'sub_agent_manager', None)
    if children is None:
        return
    children.sync_worktree_binding(state)
    cm = owner.context_manager
    cid = cm.current_conversation_id
    manager = cm._get_conversation_manager_for_id(cid)

    def acknowledge(document):
        meta = document.setdefault('metadata', {})
        if meta.get('git_worktree_children_pending') == state:
            meta.pop('git_worktree_children_pending', None)

    manager.mutate_conversation(cid, acknowledge)
    if cm.conversation_metadata.get('git_worktree_children_pending') == state:
        cm.conversation_metadata.pop('git_worktree_children_pending', None)


def restore_binding(owner: Any, *, strict: bool = True) -> None:
    metadata = getattr(owner.context_manager, 'conversation_metadata', None) or {}
    if not enabled(metadata) and not getattr(owner, 'execution_root', None):
        return
    from modules.execution_plane import is_local_backend
    if not is_local_backend(owner):
        if strict:
            raise ValueError('当前执行后端尚未适配 Git 工作树，拒绝绑定控制端本机路径。')
        return
    state = current_state(metadata) if enabled(metadata) else {}
    try:
        apply_binding(owner, state)
    except (ValueError, OSError) as exc:
        # History must load so the user can select exit. Retain the invalid root;
        # execution paths refuse it rather than silently using the main checkout.
        owner.execution_binding_error = str(exc)
        owner.execution_root = str(state.get('directory') or owner.project_path)
        for name in ('file_manager', 'terminal_ops', 'terminal_manager', 'ocr_client'):
            resource = getattr(owner, name, None)
            if resource is not None:
                resource.execution_root = Path(owner.execution_root)
                resource.execution_binding_error = str(exc)
        if strict:
            raise
        return
    if metadata.get('git_worktree_children_pending'):
        try:
            _sync_children(owner, state)
            owner.worktree_sync_error = None
        except Exception as exc:
            owner.worktree_sync_error = str(exc)
            if strict:
                raise RuntimeError(f'工作树子环境同步尚未完成，已保留重试记录：{exc}') from exc


def persist_binding(owner: Any, state: dict, *, notify_user: bool = False) -> dict:
    checked_root(owner, state)
    cm = owner.context_manager
    cid = cm.current_conversation_id
    if not cid:
        raise ValueError('工作树切换需要明确的对话身份。')
    manager = cm._get_conversation_manager_for_id(cid)
    state = dict(state)

    def update(document):
        meta = document.setdefault('metadata', {})
        if not enabled(meta):
            raise ValueError('当前对话未启用 Git worktree。')
        previous = current_state(meta)
        if (previous.get('directory'), previous.get('branch')) == (state.get('directory'), state.get('branch')):
            state.update(previous)
            return
        state['revision'] = int(previous.get('revision') or 0) + 1
        meta['git_worktree'] = dict(state)
        if getattr(owner, 'sub_agent_manager', None) is not None:
            meta['git_worktree_children_pending'] = dict(state)
        if notify_user and meta.get('frozen_system_prompt'):
            meta['git_worktree_notice'] = dict(state)

    document, _ = manager.mutate_conversation(cid, update)
    for key in ('git_worktree', 'git_worktree_notice', 'git_worktree_children_pending'):
        if key in document['metadata']:
            cm.conversation_metadata[key] = document['metadata'][key]
        else:
            cm.conversation_metadata.pop(key, None)
    apply_binding(owner, state)
    if cm.conversation_metadata.get('git_worktree_children_pending'):
        try:
            _sync_children(owner, state)
        except Exception as exc:
            state['sync_warning'] = f'子智能体同步暂未完成，将在下次执行前重试：{exc}'
    return state
