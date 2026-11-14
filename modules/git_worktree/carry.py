"""Copy staged/worktree changes through execution-side Git and filesystem resources."""
import base64
from pathlib import Path
import uuid

from .commands import process_command


class Carry:
    def __init__(self, service, source):
        self.service = service
        self.source = Path(source)
        self.scratch = []
        self.patches = []
        self.untracked = []

    def temporary(self, extension):
        path = self.service.root / ('.carry-' + uuid.uuid4().hex + extension)
        self.scratch.append(path)
        return path

    async def prepare(self):
        service = self.service
        for cached in (True, False):
            path = self.temporary('.patch')
            args = ['diff', '--no-ext-diff', '--no-textconv', '--binary', '--full-index',
                    '--output=' + service.command_path(path)]
            args += ['--cached', 'HEAD', '--'] if cached else ['--']
            await service.git(args, cwd=self.source, write=True)
            info = service.backend.files.inspect_path(str(path))
            if not info.get('success'):
                raise ValueError('无法读取携带修改的临时补丁。')
            self.patches.append(path if info.get('size') else None)
        listing = self.temporary('.paths')
        await service.git(['ls-files', '--others', '--exclude-standard', '-z'],
                          cwd=self.source, write=True, output_file=listing)
        raw = service.backend.files.read_binary(str(listing)).decode('utf-8', 'strict')
        for relative in raw.split('\0'):
            if not relative:
                continue
            rel = Path(relative)
            if rel.is_absolute() or '..' in rel.parts or relative.replace('\\', '/').startswith('.astrion/worktrees/'):
                raise ValueError('未跟踪文件路径无效。')
            info = service.backend.files.inspect_path(str(self.source / rel))
            if not info.get('success') or info.get('is_symlink') or not info.get('is_file'):
                raise ValueError(f'无法安全携带文件：{relative}')
            self.untracked.append(relative)

    async def apply(self, directory):
        service = self.service
        staged, unstaged = self.patches
        if staged:
            await service.git(['apply', '--3way', '--binary', service.command_path(staged)], cwd=directory, write=True)
        staged_tree = (await service.git(['write-tree'], cwd=directory, write=True)).strip()
        if unstaged:
            await service.git(['apply', '--3way', '--binary', service.command_path(unstaged)], cwd=directory, write=True)
            # --3way stages its result; restore exactly the staged layer we copied.
            await service.git(['read-tree', staged_tree], cwd=directory, write=True)
        for relative in self.untracked:
            source = self.source / relative
            dest = directory / relative
            info = service.backend.files.inspect_path(str(dest), access='write')
            if not info.get('success') or info.get('exists'):
                raise ValueError(f'携带文件与目标版本冲突：{relative}')
            service.ensure_folder(dest.parent)
            source_info = service.backend.files.inspect_path(str(source))
            if not source_info.get('success') or source_info.get('is_symlink') or not source_info.get('is_file'):
                raise ValueError(f'无法安全携带文件：{relative}')
            # Copy on the execution device: bounded memory and executable bits kept.
            if service.windows_direct:
                src = service.command_path(source).replace("'", "''")
                dst = service.command_path(dest).replace("'", "''")
                script = f"$ErrorActionPreference='Stop';[IO.File]::Copy('{src}','{dst}',$false)"
                encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
                command = 'powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ' + encoded
            else:
                paths = [service.command_path(source), service.command_path(dest)]
                command = (process_command(['cp', '-p', '-P', '-n', '--', *paths])
                           + ' && ' + process_command(['cmp', '-s', '--', *paths]))
            result = await service.backend.run_command(command, timeout=120, sandbox_write_access=True, working_dir=str(directory))
            copied = service.backend.files.inspect_path(str(dest))
            if not result.get('success') or not copied.get('is_file') or copied.get('is_symlink'):
                raise ValueError(str(result.get('error') or result.get('output') or f'无法携带文件：{relative}'))

    def cleanup(self):
        warnings = []
        for path in self.scratch:
            try:
                result = self.service.backend.files.delete_file(str(path))
                if not result.get('success'):
                    info = self.service.backend.files.inspect_path(str(path))
                    if info.get('exists') or not info.get('success'):
                        warnings.append(f'无法清理本次临时补丁：{path}')
            except Exception as exc:
                warnings.append(f'无法清理本次临时补丁 {path}：{exc}')
        return warnings
