"""Conversation-scoped worktree state; workspace identity never changes."""
from pathlib import Path
from typing import Any

TOOLS = frozenset({'enter_worktree', 'exit_worktree'})


def enabled(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get('git_worktree_enabled') is True


def execution_root(owner: Any) -> str:
    return str(getattr(owner, 'execution_root', None) or owner.project_path)


def current_state(metadata: Any) -> dict:
    state = metadata.get('git_worktree') if isinstance(metadata, dict) else None
    return dict(state) if isinstance(state, dict) else {}


def branch_label(state: dict) -> str:
    """工作树当前状态的显示名；detached 工作树没有分支名。"""
    branch = str(state.get('branch') or '').strip()
    if branch:
        return branch
    head = str(state.get('head') or '').strip()
    if head:
        return f'detached @ {head[:7]}'
    return '主目录当前分支'


def prompt(metadata: Any, workspace_root: str) -> str:
    if not enabled(metadata):
        return ''
    state = current_state(metadata)
    root = state.get('directory') or str(workspace_root)
    branch = branch_label(state)
    from config import PROMPTS_DIR
    template = (Path(PROMPTS_DIR) / 'main_agent/dynamic/git_worktree.txt').read_text(encoding='utf-8')
    return template.format(directory=root, branch=branch).strip()


def child_environment(directory, branch, *, access_level, working_dir, head=None):
    label = str(branch or '').strip() or (f'detached @ {str(head)[:7]}' if head else '主目录当前分支')
    text = (f'当前执行目录：{directory}\n当前分支：{label}\n'
            '相对文件路径和新命令以当前执行目录为基准；跟随主对话切换，不能自行调用工作树工具。'
            'MCP 等外置工具继续使用自身配置的 cwd。')
    if access_level == 'working_dir_write':
        text += (f'\n读权限覆盖已授权的工作区；固定写权限仍仅为 {working_dir}。'
                 '相对写入不再以固定写目录为基准，写自己的产物请使用固定写目录的绝对路径。'
                 '执行根内其它代码只读，需要修改时交回主智能体。')
    return text



def notification(state: dict) -> str:
    return (
        '[工作树环境变更]\n'
        f'当前分支：{branch_label(state)}\n'
        f'当前执行目录：{state["directory"]}\n'
        '后续文件操作和新命令使用该执行目录。已有进程保留原目录。'
        '环境选择已经明确，不要重复询问。MCP 等外置工具继续使用自身配置的 cwd。'
    )


def definitions() -> list[dict]:
    intent = {'type': 'string', 'description': '用不超过15个字说明操作目的。'}
    branch = {'type': 'string', 'description': '完整 Git 分支名，例如 astrion/fix-login。'}
    create = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['create']},
            'branch_name': branch,
            'start_ref': {'type': 'string', 'description': '必填起始分支、标签或提交 SHA；HEAD 相对当前执行环境解析。'},
            'include_uncommitted': {'type': 'boolean', 'description': '必填。是否将当前执行目录的未提交修改复制到新工作树；原修改保留，忽略文件不自动复制。'},
        },
        'required': ['action', 'branch_name', 'start_ref', 'include_uncommitted'],
        'additionalProperties': False,
    }
    enter = {
        'type': 'object',
        'properties': {
            'action': {'type': 'string', 'enum': ['enter']},
            'branch_name': branch,
            'directory': {'type': 'string', 'description': '工作树的绝对路径。仅当目标工作树是 detached HEAD（没有分支名）时使用；与 branch_name 二选一。'},
        },
        'required': ['action'], 'additionalProperties': False,
    }
    return [
        {'type': 'function', 'function': {
            'name': 'enter_worktree',
            'description': '为当前对话创建并进入 Git 工作树，或进入已有工作树。文件操作和命令执行跟随新的执行目录。create 的落点在当前工作区 .astrion/worktrees/ 下；enter 可进入工作区内任意已注册工作树，工作区外的需在沙箱路径授权内。不提交、不合并；不得与其它工具并行调用。',
            'parameters': {'type': 'object', 'properties': {
                'intent': intent, 'target': {'anyOf': [create, enter]},
            }, 'required': ['intent', 'target'], 'additionalProperties': False},
        }},
        {'type': 'function', 'function': {
            'name': 'exit_worktree',
            'description': '将当前对话返回工作区主目录，保留工作树、分支和未提交修改，不改变主目录当前分支。不执行提交、合并或删除；不得与其它工具并行调用。',
            'parameters': {'type': 'object', 'properties': {'intent': intent},
                           'required': ['intent'], 'additionalProperties': False},
        }},
    ]
