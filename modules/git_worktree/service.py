"""Git operations via the bound execution backend; no commit/merge/cleanup."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import stat
import threading

from modules.execution_plane import get_execution_backend, is_local_backend
from . import current_state, enabled, execution_root
from .binding import persist_binding
from .commands import git_command
from .grants import bind_extra_roots

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

    # ------------------------------------------------------------------
    # 授权判定
    # ------------------------------------------------------------------
    def inside_workspace(self, path: Path) -> bool:
        try:
            Path(path).relative_to(self.workspace)
            return True
        except ValueError:
            return False

    def writable_roots(self) -> list[Path]:
        """沙箱可写授权根（部署环境变量 + 用户级/工作区级授权）。

        只用于工作区外工作树的准入判定，不写回策略文件。
        """
        try:
            from modules.host_sandbox_policy import get_macos_writable_paths
            raw = get_macos_writable_paths(str(self.workspace))
        except Exception:  # noqa: BLE001 - 读取失败按无授权处理（fail-closed）
            return []
        roots = []
        for item in raw:
            try:
                roots.append(Path(str(item)).expanduser().resolve())
            except (OSError, RuntimeError, ValueError):
                continue
        return roots

    def authorized(self, path: Path) -> bool:
        """工作区内恒可用；工作区外必须落在可写授权根内。"""
        target = Path(path)
        if self.inside_workspace(target):
            return True
        for root in self.writable_roots():
            try:
                target.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def direct_mode(self) -> bool:
        getter = getattr(self.owner, 'get_execution_mode', None)
        try:
            return (getter() if callable(getter) else None) == 'direct'
        except Exception:  # noqa: BLE001 - 取不到执行环境按沙箱处理
            return False

    def validate_directory(self, path: Path, *, managed=True) -> Path:
        info = self.backend.files.inspect_path(str(path))
        if not info.get('success'):
            raise WorktreeError(str(info.get('error') or '无法验证工作树目录。'))
        reported = Path(info.get('path') or path)
        canonical = reported.resolve() if reported.is_absolute() else (self.workspace / reported).resolve()
        if managed:
            try:
                canonical.relative_to(self.root)
            except ValueError as exc:
                raise WorktreeError('只能在工作区 .astrion/worktrees/ 下创建工作树。') from exc
        if info.get('is_symlink'):
            raise WorktreeError('工作树目录不能是符号链接。')
        return canonical

    def validate_target_path(self, raw: str) -> Path:
        """校验 enter 传入的目录：绝对路径、非符号链接、存在且是目录。

        用户手动切换可以先于任何授权发生，所以这里不能用 `FileManager.inspect_path`：
        它会按砂箱可读边界拒绝工作区外目录，这里必须只做本机文件系统事实校验
        （工作树本来就只支持 Local 后端）。
        """
        if not isinstance(raw, str) or not raw.strip():
            raise WorktreeError('directory 必须是非空绝对路径。')
        candidate = Path(raw.strip()).expanduser()
        if not candidate.is_absolute():
            raise WorktreeError('directory 必须是绝对路径。')
        try:
            info = os.lstat(candidate)
        except OSError as exc:
            raise WorktreeError(f'目标工作树目录无法访问：{exc}') from exc
        if stat.S_ISLNK(info.st_mode):
            raise WorktreeError('工作树目录不能是符号链接。')
        if not stat.S_ISDIR(info.st_mode):
            raise WorktreeError('目标路径不是文件夹。')
        return candidate.resolve()

    async def common_dir(self, cwd: Path) -> Path:
        """仓库公共 Git 目录（linked worktree 与主仓库相同），用于核对归属。"""
        raw = (await self.git(['rev-parse', '--git-common-dir'], cwd=cwd)).strip()
        path = Path(raw)
        return path.resolve() if path.is_absolute() else (Path(cwd) / path).resolve()

    async def inspect_worktree(self, directory: Path) -> dict:
        """按 Git 事实确认它是本仓库的工作树，并返回当前分支与 HEAD。"""
        shown = str(directory)
        with bind_extra_roots([directory]):
            try:
                theirs = await self.common_dir(directory)
            except WorktreeError as exc:
                raise WorktreeError(f'该目录（{shown}）不是 Git 工作树：{exc}') from exc
            mine = await self.common_dir(self.workspace)
            if theirs != mine:
                raise WorktreeError(f'该目录（{shown}）属于其它 Git 仓库，不是当前工作区的工作树。')
            if not (directory / '.git').is_file():
                raise WorktreeError(
                    f'该目录（{shown}）是当前仓库的主工作目录，请用 exit_worktree 返回；enter 只接受工作树。')
            branch = (await self.git(['branch', '--show-current'], cwd=directory)).strip()
            head = (await self.git(['rev-parse', 'HEAD'], cwd=directory)).strip()
        return {'branch': branch, 'head': head}

    async def describe(self, raw: str) -> dict:
        """只读描述一个候选目录（供前端在提交前提示）。"""
        try:
            directory = self.validate_target_path(raw)
            info = await self.inspect_worktree(directory)
        except WorktreeError as exc:
            return {'valid': False, 'error': str(exc)}
        return {'valid': True, 'directory': str(directory), 'branch': info['branch'],
                'head': info['head'], 'inside_workspace': self.inside_workspace(directory),
                'authorized': self.authorized(directory)}

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
            branch = str(fields.get('branch') or '').removeprefix('refs/heads/')
            trees.append({'directory': str(directory), 'branch': branch,
                          'head': fields.get('HEAD'), 'is_main': directory == self.workspace,
                          'managed': directory.is_relative_to(self.root),
                          'inside_workspace': self.inside_workspace(directory),
                          'detached': 'detached' in fields,
                          'authorized': self.authorized(directory)})
        return {'success': True, 'enabled': True, 'directory': execution_root(self.owner),
                'state': current_state(self.owner.context_manager.conversation_metadata), 'worktrees': trees}

    # ------------------------------------------------------------------
    # 绑定切换
    # ------------------------------------------------------------------
    def busy_reasons(self, root: Path) -> list[str]:
        """退出工作树前的忙碌检查：子智能体、后台命令、持久终端。"""
        reasons = []
        cm = getattr(self.owner, 'context_manager', None)
        cid = getattr(cm, 'current_conversation_id', None)
        children = getattr(self.owner, 'sub_agent_manager', None)
        if children is not None:
            lock = getattr(children, '_state_lock', None)
            if lock is not None:
                with lock:
                    active = [rec for rec in getattr(children, 'tasks', {}).values()
                              if rec.get('conversation_id') == cid and rec.get('status') == 'running']
            else:
                active = [rec for rec in getattr(children, 'tasks', {}).values()
                          if rec.get('conversation_id') == cid and rec.get('status') == 'running']
            if active:
                reasons.append(f'还有 {len(active)} 个子智能体在运行')
        background = getattr(self.owner, 'background_command_manager', None)
        if background is not None:
            try:
                records = background.list_records(conversation_id=cid)
            except Exception:  # noqa: BLE001 - 列表失败不阻塞退出判定
                records = []
            if any(rec.get('status') == 'running' for rec in records):
                reasons.append('还有后台命令在运行')
        terminals = getattr(self.owner, 'terminal_manager', None)
        if terminals is not None:
            try:
                sessions = terminals.get_terminal_list()
            except Exception:  # noqa: BLE001
                sessions = []
            for session in sessions:
                if not session.get('is_running'):
                    continue
                try:
                    Path(str(session.get('working_dir'))).resolve().relative_to(root)
                except (ValueError, OSError, RuntimeError):
                    continue
                reasons.append(f"终端 {session.get('name')} 仍在当前执行目录运行")
                break
        return reasons

    async def change(self, target: dict | None, *, exit=False, notify_user=False,
                     persist=True, origin='model') -> dict:
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
                # 当前执行根可能在工作区外；检查残留工作前先放行它，否则读取会被拦。
                current_root = Path(execution_root(self.owner)).resolve()
                with bind_extra_roots([current_root]):
                    reasons = self.busy_reasons(current_root)
                if reasons:
                    raise WorktreeError('当前执行目录仍有进行中的工作，暂不能退出：' + '；'.join(reasons) + '。')
                directory = self.workspace
                branch = (await self.git(['branch', '--show-current'], cwd=directory)).strip()
                state = {'directory': str(directory), 'branch': branch, 'active': False}
            elif target['action'] == 'create':
                state = await self.create(target)
            else:
                state = await self.enter(target, origin=origin)
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

    async def enter(self, target: dict, *, origin='model') -> dict:
        """进入已注册工作树；键为 branch_name 或 directory（互斥）。"""
        branch_name = target.get('branch_name')
        raw_directory = target.get('directory')
        if raw_directory is not None:
            directory = self.validate_target_path(raw_directory)
            info = await self.inspect_worktree(directory)
            tree = {'directory': str(directory), 'branch': info['branch'], 'head': info['head']}
        else:
            trees = (await self.list())['worktrees']
            matches = [item for item in trees if item['branch'] == branch_name]
            if len(matches) != 1:
                raise WorktreeError('找不到唯一对应的已注册工作树；不会自动创建或切换主目录分支。')
            tree = matches[0]
            directory = Path(tree['directory'])
            if tree['is_main']:
                raise WorktreeError('目标就是工作区主目录，请改用 exit_worktree。')
            # 外部工作树：校验分支等 git 命令的 cwd 在工作区外，需要临时放行该根。
            with bind_extra_roots([directory]):
                if not directory.is_dir():
                    raise WorktreeError('目标工作树目录已失效，请选择其它工作树或返回主目录。')
                current = (await self.git(['branch', '--show-current'], cwd=directory)).strip()
                if current != branch_name:
                    raise WorktreeError('目标工作树分支已变化，请重新选择。')
        branch = tree['branch'] or ''
        inside = self.inside_workspace(directory)
        if not inside and origin != 'user' and not self.direct_mode() and not self.authorized(directory):
            raise WorktreeError(
                '该工作树位于工作区外，且不在沙箱路径授权（可读写）范围内；'
                '请在「路径授权」中放行该目录，或改由用户手动切换。')
        state = {'directory': str(directory), 'branch': branch,
                 'head': tree.get('head'), 'active': True}
        if not inside:
            # 对话级临时放行：写入对话绑定，重启恢复后可重建；退出绑定即失效，
            # 不写任何用户级策略文件。`added` 表示它原本不在授权范围内。
            state['grant'] = {'path': str(directory), 'added': not self.authorized(directory)}
        return state

    @staticmethod
    def validate_target(target):
        if not isinstance(target, dict) or target.get('action') not in {'create', 'enter'}:
            raise WorktreeError('必须明确选择 create 或 enter。')
        action = target['action']
        if action == 'enter':
            keys = set(target) - {'action'}
            if keys not in ({'branch_name'}, {'directory'}):
                raise WorktreeError('enter 只能提供 branch_name 或 directory 其中一个。')
            value = target.get('branch_name') if 'branch_name' in target else target.get('directory')
            if not isinstance(value, str) or not value.strip():
                raise WorktreeError('branch_name 或 directory 不能为空。')
            if 'branch_name' in target and value.startswith('-'):
                raise WorktreeError('必须提供合法的分支名。')
            if 'directory' in target and not Path(value.strip()).is_absolute():
                raise WorktreeError('directory 必须是绝对路径。')
            return
        expected = {'action', 'branch_name', 'start_ref', 'include_uncommitted'}
        if set(target) != expected:
            raise WorktreeError('工作树参数缺失或包含不适用字段。')
        branch = target['branch_name']
        if not isinstance(branch, str) or not branch.strip() or branch.startswith('-'):
            raise WorktreeError('必须提供合法的新分支名。')
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
            raise WorktreeError('分支已存在，请使用其它名称，或选择进入该分支已有的工作树。')
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
