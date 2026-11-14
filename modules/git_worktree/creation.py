"""Prepare /new selections before freezing a conversation's first system."""
import asyncio
import copy

from modules.execution_plane import LocalExecutionBackend
from .service import Worktrees


def prepare(owner, settings, target):
    if target is None:
        return None
    from modules.execution_plane import is_local_backend
    if not is_local_backend(owner):
        raise ValueError('当前执行后端尚未适配 Git 工作树，拒绝创建控制端本机工作树。')
    temporary = copy.copy(owner)
    temporary.context_manager = copy.copy(owner.context_manager)
    temporary.context_manager.conversation_metadata = {'git_worktree_enabled': True}
    temporary.execution_root = str(owner.project_path)
    temporary.file_manager = copy.copy(owner.file_manager)
    temporary.file_manager.execution_root = owner.project_path
    temporary.terminal_ops = copy.copy(owner.terminal_ops)
    temporary.terminal_ops.execution_root = owner.project_path
    temporary.file_manager.set_host_execution_mode(settings['execution_mode'])
    temporary.terminal_ops.set_host_execution_mode(settings['execution_mode'])
    temporary.get_permission_mode = lambda: settings['permission_mode']
    temporary.get_work_mode = lambda: settings['work_mode']
    temporary.get_execution_mode = lambda: settings['execution_mode']
    temporary.get_network_permission = lambda: settings['network_permission']
    temporary.execution_backend = LocalExecutionBackend(temporary)
    result = asyncio.run(Worktrees(temporary).change(target, persist=False))
    if not result.get('success'):
        detail = result.get('error') or '工作树准备失败。'
        if result.get('retained_directory'):
            detail += f"\n已保留分支 {result.get('retained_branch')}，目录 {result['retained_directory']}；可选择进入已有工作树处理。"
        raise ValueError(detail)
    return result['state']
