"""Small fail-closed helpers for trusted task-local execution authority."""
from pathlib import Path

from modules.execution_scope import current_execution_scope


class ScopedExecutionError(ValueError):
    pass


def fixed_workspace_root(scope):
    root = Path(scope.workspace_root)
    try:
        valid = root.is_absolute() and root.resolve() == root and root.is_dir()
    except (OSError, RuntimeError) as exc:
        raise ScopedExecutionError(f"Fixed workspace root is unavailable: {exc}") from exc
    if not valid:
        raise ScopedExecutionError("Fixed workspace root is missing or has been replaced by a symlink; operation refused")
    return root


def fixed_working_dir(scope):
    root = fixed_workspace_root(scope)
    raw = getattr(scope, "working_dir", None)
    if raw is None:
        if scope.access_level == "working_dir_write":
            raise ScopedExecutionError("Missing fixed child working directory")
        return root
    target = Path(raw)
    if not target.is_absolute() or target.resolve() != target or not target.is_dir():
        raise ScopedExecutionError("Fixed child working directory is unavailable or redirected")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ScopedExecutionError("Fixed child directory is outside its original workspace") from exc
    return target


def _allowed_external_roots() -> tuple:
    """当前调用上下文里额外放行的执行根（工作区外工作树）。

    只由已校验的对话工作树绑定 / 子智能体执行根写入，工具参数无法注入；
    没有绑定时恒为空。
    """
    try:
        from modules.git_worktree.grants import current_extra_roots
        return current_extra_roots()
    except Exception:  # noqa: BLE001 - 读取失败时按无额外根处理
        return ()


def _within(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def fixed_execution_root(scope):
    raw = getattr(scope, 'execution_root', None)
    if raw is None:
        return fixed_working_dir(scope)
    root = fixed_workspace_root(scope)
    target = Path(raw)
    if not target.is_absolute() or target.resolve() != target or not target.is_dir():
        raise ScopedExecutionError('Execution root is unavailable or redirected')
    if not _within(target, root):
        if not any(_within(target, granted) for granted in _allowed_external_roots()):
            raise ScopedExecutionError('Execution root is outside its fixed workspace')
    return target


def fixed_write_root(scope):
    return fixed_working_dir(scope) if scope.access_level == "working_dir_write" else fixed_workspace_root(scope)


def scoped_work_path(project_path, working_dir=None, scope=None):
    scope = scope if scope is not None else current_execution_scope()
    root = fixed_workspace_root(scope) if scope else Path(project_path).resolve()
    base = fixed_execution_root(scope) if scope and scope.is_sub_agent else root
    try:
        target = (base / working_dir).resolve() if working_dir else base
        if not _within(target, root):
            if not any(_within(target, granted) for granted in _allowed_external_roots()):
                raise ValueError('target is outside the fixed workspace')
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScopedExecutionError(f"Working directory is outside or inaccessible in the fixed workspace: {exc}") from exc
    return target


def scoped_write_access(requested=True, scope=None):
    scope = scope if scope is not None else current_execution_scope()
    # A child's access level is fixed; parent readonly switches cannot downgrade it.
    return True if scope and scope.is_sub_agent else bool(requested)


def same_scope_authority(left, right):
    if left is None or right is None:
        return left is right
    return (
        left.executor_kind, left.actor_id, Path(left.workspace_root), left.access_level, left.conversation_id, left.working_dir, getattr(left, 'execution_root', None)
    ) == (
        right.executor_kind, right.actor_id, Path(right.workspace_root), right.access_level, right.conversation_id, right.working_dir, getattr(right, 'execution_root', None)
    )
