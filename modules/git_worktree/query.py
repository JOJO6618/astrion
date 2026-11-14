"""Read views without allocating execution runtimes or changing workspace focus."""
from pathlib import Path
import re

from modules.project_git.runner import text
from . import current_state, enabled


def directory_for(workspace, metadata):
    root = Path(workspace.project_path).resolve()
    if not enabled(metadata):
        return root
    state = current_state(metadata)
    directory = Path(state.get('directory') or root)
    if not directory.is_absolute() or directory.resolve() != directory or not directory.is_dir():
        raise ValueError('工作树执行目录已失效，请先返回主目录或选择有效工作树。')
    directory.relative_to(root)
    return directory


def _display_path(raw, root):
    # Git stores execution-device paths. Querying its admin records does not need
    # a container/runtime; translate only known mounts, never arbitrary paths.
    from config import TERMINAL_SANDBOX_MODE, TERMINAL_SANDBOX_MOUNT_PATH
    mount = str(TERMINAL_SANDBOX_MOUNT_PATH or '/workspace').rstrip('/')
    if TERMINAL_SANDBOX_MODE == 'docker' and (raw == mount or raw.startswith(mount + '/')):
        return root / raw[len(mount) + 1:] if raw != mount else root
    if root.drive:
        match = re.fullmatch(r'/mnt/([A-Za-z])(?:/(.*))?', raw)
        if match:
            return Path(match[1].upper() + ':/' + (match[2] or ''))
    return Path(raw)


def query_args(directory, args):
    """Translate a managed linked tree's Git admin path for host-side reads."""
    directory = Path(directory)
    if directory.parent.name != 'worktrees' or directory.parent.parent.name != '.astrion':
        return list(args)
    root = directory.parent.parent.parent
    gitfile = directory / '.git'
    if gitfile.is_symlink():
        raise ValueError('工作树 Git 目录不能被重定向。')
    if not gitfile.is_file():
        return list(args)
    pointer = gitfile.read_text(encoding='utf-8').strip()
    if not pointer.startswith('gitdir: '):
        raise ValueError('工作树 Git 目录记录无效。')
    raw = pointer[len('gitdir: '):]
    mapped = _display_path(raw, root)
    if mapped == Path(raw):
        return list(args)
    mapped.relative_to(root / '.git' / 'worktrees')
    if mapped.resolve() != mapped or not mapped.is_dir():
        raise ValueError('工作树 Git 管理目录已失效。')
    return ['--git-dir=' + str(mapped), '--work-tree=' + str(directory), *args]


def view(workspace, metadata):
    root = Path(workspace.project_path).resolve()
    top = _display_path(text(root, ['rev-parse', '--show-toplevel']).strip(), root).resolve()
    if top != root:
        raise ValueError('当前工作区必须是 Git 仓库根目录；不会使用父目录仓库的分支。')
    unavailable = ''
    try:
        directory = directory_for(workspace, metadata)
    except (ValueError, OSError) as exc:
        unavailable = str(exc)
        # Display the missing binding, not the main checkout as a fallback.
        directory = Path(current_state(metadata).get('directory') or root)
    trees = []
    raw = text(root, ['worktree', 'list', '--porcelain', '-z'])
    managed_root = root / '.astrion' / 'worktrees'
    for record in raw.split('\0\0'):
        fields = dict(line.split(' ', 1) if ' ' in line else (line, True)
                      for line in record.split('\0') if line)
        if 'worktree' not in fields:
            continue
        path = _display_path(fields['worktree'], root)
        if path != root and not path.is_relative_to(managed_root):
            continue
        trees.append({'branch': str(fields.get('branch') or '').removeprefix('refs/heads/'),
                      'directory': str(path), 'is_main': path == root,
                      'unavailable': not path.is_dir() or ('prunable' in fields and path == Path(fields['worktree'])),
                      'current': path == directory})
    refs = text(root, ['for-each-ref', '--format=%(refname:short)', 'refs/heads', 'refs/tags']).splitlines()
    branch = next((tree['branch'] for tree in trees if tree['current']), '')
    if not branch:
        branch = current_state(metadata).get('branch') or ''
    return {'enabled': enabled(metadata), 'directory': str(directory), 'branch': branch,
            'active': directory != root, 'worktrees': trees, 'refs': refs,
            'unavailable': unavailable}
