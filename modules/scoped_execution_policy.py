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


def scoped_work_path(project_path, working_dir=None, scope=None):
    scope = scope if scope is not None else current_execution_scope()
    root = fixed_workspace_root(scope) if scope else Path(project_path).resolve()
    try:
        target = (root / working_dir).resolve() if working_dir else root
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScopedExecutionError(f"Working directory is outside or inaccessible in the fixed workspace: {exc}") from exc
    return target


def scoped_write_access(requested=True, scope=None):
    scope = scope if scope is not None else current_execution_scope()
    # A child's access level is fixed; parent readonly switches cannot downgrade it.
    return True if scope and scope.is_sub_agent else bool(requested)


def require_scoped_docker_support(scope=None):
    scope = scope if scope is not None else current_execution_scope()
    if scope is not None and scope.access_level == "full_access":
        raise ScopedExecutionError("Docker 工具箱不支持宿主机完全访问。")
    # workspace_write requires the mandatory scoped launcher. sandbox_write
    # retains the existing container boundary; host fallback is forbidden.


def same_scope_authority(left, right):
    if left is None or right is None:
        return left is right
    return (
        left.executor_kind, left.actor_id, Path(left.workspace_root), left.access_level, left.conversation_id
    ) == (
        right.executor_kind, right.actor_id, Path(right.workspace_root), right.access_level, right.conversation_id
    )
