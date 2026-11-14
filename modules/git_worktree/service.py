"""Git operations via the bound execution backend; no commit/merge/cleanup."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import threading

from modules.execution_plane import get_execution_backend, is_local_backend
from . import current_state, enabled, execution_root
from .binding import persist_binding
from .commands import git_command

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class WorktreeError(ValueError):
    pass


class Worktrees:
    def __init__(self, owner):
        self.owner = owner
        self.backend = get_execution_backend(owner)
        if not is_local_backend(owner):
            raise WorktreeError('当前执行后端尚未适配 Git 工作树路径；拒绝使用控制端本机目录。')
        self.workspace = Path(owner.project_path).resolve()
        self.root = self.workspace / '.astrion' / 'worktrees'
        self.created_directory = None
        self.cleanup_warnings = []
        self.windows_direct = os.name == 'nt' and self.backend.execution_mode == 'direct'

    def command_path(self, path: str | Path) -> str:
        path = Path(path)
        session = getattr(self.owner, 'container_session', None)
        if getattr(session, 'mode', None) == 'docker':
            from config import TERMINAL_SANDBOX_MOUNT_PATH
            return str(getattr(session, 'mount_path', None) or TERMINAL_SANDBOX_MOUNT_PATH or '/workspace').rstrip('/') + '/' + path.relative_to(self.workspace).as_posix()
        if os.name == 'nt' and self.backend.execution_mode != 'direct':
            from modules.host_sandbox_runner import _win_path_to_wsl
            return _win_path_to_wsl(path)
        return str(path)

    async def git(self, args: list[str], *, cwd=None, write=False, output_file=None) -> str:
        command = git_command(args, windows_direct=self.windows_direct,
                              stdout_file=self.command_path(output_file) if output_file else None)
        result = await self.backend.run_command(
            command, timeout=120, sandbox_write_access=write,
            working_dir=str(cwd or execution_root(self.owner)),
        )
        if result.get('return_code') != 0 or not result.get('success'):
            raise WorktreeError(str(result.get('error') or result.get('output') or 'Git 操作失败'))
        if output_file:
            return ''
        if 'stdout' not in result or result.get('stdout_truncated'):
            raise WorktreeError('Git 标准输出缺失或被截断，拒绝解析不完整状态。')
        return str(result['stdout'])

    def host_path(self, raw: str) -> Path:
        session = getattr(self.owner, 'container_session', None)
        if getattr(session, 'mode', None) == 'docker':
            from config import TERMINAL_SANDBOX_MOUNT_PATH
            mount = str(getattr(session, 'mount_path', None) or TERMINAL_SANDBOX_MOUNT_PATH or '/workspace').rstrip('/')
            if raw == mount:
                return self.workspace
            if not raw.startswith(mount + '/'):
                raise WorktreeError('Git 工作树不属于当前工作区。')
            return self.workspace / raw[len(mount) + 1:]
        if os.name == 'nt' and self.backend.execution_mode != 'direct':
            match = re.fullmatch(r'/mnt/([A-Za-z])(?:/(.*))?', raw)
            if not match:
                raise WorktreeError('无法解析 WSL 工作树路径。')
            raw = match[1].upper() + ':/' + (match[2] or '')
        return Path(raw).resolve()

    def validate_directory(self, path: Path, *, managed=True) -> Path:
        info = self.backend.files.inspect_path(str(path))
        if not info.get('success'):
            raise WorktreeError(str(info.get('error') or '无法验证工作树目录。'))
        reported = Path(info.get('path') or path)
        canonical = (self.workspace / reported).resolve()
        try:
            canonical.relative_to(self.root if managed else self.workspace)
        except ValueError as exc:
            raise WorktreeError('只能进入当前工作区 .astrion/worktrees/ 下的工作树。') from exc
        if info.get('is_symlink'):
            raise WorktreeError('工作树目录不能是符号链接。')
        return canonical

    def ensure_folder(self, path):
        info = self.backend.files.inspect_path(str(path))
        if not info.get('success'):
            raise WorktreeError(str(info.get('error') or '目录不可访问。'))
        if info.get('exists'):
            if not info.get('is_dir') or info.get('is_symlink'):
                raise WorktreeError('目录不是安全的普通文件夹。')
            return
        result = self.backend.files.create_folder(str(path))
        if not result.get('success'):
            raise WorktreeError(str(result.get('error') or '无法创建目录。'))

    async def list(self) -> dict:
        raw = await self.git(['worktree', 'list', '--porcelain', '-z'], cwd=self.workspace)
        trees = []
        for record in raw.split('\0\0'):
            fields = dict(line.split(' ', 1) if ' ' in line else (line, True)
                          for line in record.split('\0') if line)
            if 'worktree' not in fields:
                continue
            directory = self.host_path(fields['worktree'])
            try:
                directory.relative_to(self.workspace)
            except ValueError:
                continue
            branch = str(fields.get('branch') or '').removeprefix('refs/heads/')
            trees.append({'directory': str(directory), 'branch': branch,
                          'head': fields.get('HEAD'), 'is_main': directory == self.workspace,
                          'managed': directory.is_relative_to(self.root)})
        return {'success': True, 'enabled': True, 'directory': execution_root(self.owner),
                'state': current_state(self.owner.context_manager.conversation_metadata), 'worktrees': trees}

    async def change(self, target: dict | None, *, exit=False, notify_user=False, persist=True) -> dict:
        meta = self.owner.context_manager.conversation_metadata or {}
        if not enabled(meta):
            raise WorktreeError('当前对话未启用 Git worktree。')
        if not exit:
            self.validate_target(target)
        key = str(self.workspace)
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(key, threading.Lock())
        while not lock.acquire(blocking=False):
            await asyncio.sleep(0.025)
        try:
            # An execution workspace must be the repository root; never guess a nested repository.
            top = self.host_path((await self.git(['rev-parse', '--show-toplevel'], cwd=self.workspace)).strip())
            if top != self.workspace:
                raise WorktreeError('当前工作区必须是 Git 仓库根目录。')
            if exit:
                directory = self.workspace
                branch = (await self.git(['branch', '--show-current'], cwd=directory)).strip()
                state = {'directory': str(directory), 'branch': branch, 'active': False}
            elif target['action'] == 'create':
                state = await self.create(target)
            else:
                info = await self.list()
                matches = [tree for tree in info['worktrees'] if tree['branch'] == target['branch_name'] and tree['managed']]
                if len(matches) != 1:
                    raise WorktreeError('找不到唯一对应的托管工作树；不会自动创建或切换主目录分支。')
                tree = matches[0]
                self.validate_directory(Path(tree['directory']))
                if not Path(tree['directory']).is_dir():
                    raise WorktreeError('目标工作树目录已失效，请选择其它工作树或返回主目录。')
                if (await self.git(['branch', '--show-current'], cwd=tree['directory'])).strip() != target['branch_name']:
                    raise WorktreeError('目标工作树分支已变化，请重新选择。')
                state = {key: tree[key] for key in ('directory', 'branch', 'head')}
                state['active'] = True
            if persist:
                state = persist_binding(self.owner, state, notify_user=notify_user)
            return {'success': True, 'state': state, 'warnings': self.cleanup_warnings,
                    'message': '执行目录已切换；工作树和修改已保留，未执行提交或合并。'}
        except (OSError, ValueError, RuntimeError) as exc:
            result = {'success': False, 'error': str(exc), 'warnings': self.cleanup_warnings}
            if self.created_directory:
                result['retained_directory'] = self.created_directory
                result['retained_branch'] = target.get('branch_name') if target else None
                result['recovery'] = '工作树已保留；可用 enter_worktree 的 enter 操作进入该分支检查或处理冲突。'
            return result
        finally:
            lock.release()

    @staticmethod
    def validate_target(target):
        if not isinstance(target, dict) or target.get('action') not in {'create', 'enter'}:
            raise WorktreeError('必须明确选择 create 或 enter。')
        action = target['action']
        expected = {'action', 'branch_name'} | ({'start_ref', 'include_uncommitted'} if action == 'create' else set())
        if set(target) != expected:
            raise WorktreeError('工作树参数缺失或包含不适用字段。')
        branch = target['branch_name']
        if not isinstance(branch, str) or not branch.strip() or branch.startswith('-'):
            raise WorktreeError('必须提供合法的新分支名。')
        if action == 'create':
            if not isinstance(target['start_ref'], str) or not target['start_ref'].strip() or target['start_ref'].startswith('-'):
                raise WorktreeError('必须明确提供起始分支或版本。')
            if type(target['include_uncommitted']) is not bool:
                raise WorktreeError('include_uncommitted 必须明确提供 true 或 false。')

    async def create(self, target):
        mode = getattr(self.owner, 'get_permission_mode', lambda: 'readonly')()
        work_mode = getattr(self.owner, 'get_work_mode', lambda: 'plan')()
        if mode == 'readonly' or work_mode == 'plan':
            raise WorktreeError('只读或计划模式不能创建工作树。')
        branch = target['branch_name']
        await self.git(['check-ref-format', '--branch', branch])
        branches = (await self.git(['for-each-ref', '--count=1', '--format=%(refname)', 'refs/heads/' + branch], cwd=self.workspace)).splitlines()
        if 'refs/heads/' + branch in branches:
            raise WorktreeError('分支已存在，请使用其它名称，或选择进入该分支已有的托管工作树。')
        source = execution_root(self.owner)
        head = (await self.git(['rev-parse', '--verify', '--end-of-options', target['start_ref'] + '^{commit}'], cwd=source)).strip()
        leaf = re.sub(r'[^A-Za-z0-9._-]+', '-', branch).strip('.-')[:64] or 'worktree'
        leaf += '-' + hashlib.sha256(branch.encode()).hexdigest()[:8]
        directory = self.validate_directory(self.root / leaf)
        self.ensure_folder(self.root)
        # Repository-local excludes avoid changing the user's tracked .gitignore.
        exclude_raw = (await self.git(['rev-parse', '--git-path', 'info/exclude'], cwd=self.workspace)).strip()
        exclude = self.host_path(exclude_raw) if Path(exclude_raw).is_absolute() else self.workspace / exclude_raw
        info = self.backend.files.inspect_path(str(exclude))
        if not info.get('success'):
            raise WorktreeError('无法验证 Git 忽略文件。')
        old = self.backend.files.read_binary(str(exclude)) if info.get('exists') else b''
        rule = b'/.astrion/worktrees/'
        if rule not in old.splitlines():
            result = self.backend.files.write_binary(str(exclude), old + b'\n' + rule + b'\n')
            if not result.get('success'):
                raise WorktreeError(str(result.get('error') or '无法配置工作树忽略规则。'))
        from .carry import Carry
        carry = Carry(self, source) if target['include_uncommitted'] else None
        try:
            if carry:
                await carry.prepare()
            await self.git(['worktree', 'add', '-b', branch, self.command_path(directory), head], cwd=self.workspace, write=True)
            self.created_directory = str(directory)
            if carry:
                await carry.apply(directory)
        finally:
            if carry:
                self.cleanup_warnings.extend(carry.cleanup())
        return {'directory': str(directory), 'branch': branch, 'head': head,
                'active': True, 'include_uncommitted': target['include_uncommitted']}
