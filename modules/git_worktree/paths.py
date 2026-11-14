"""Disambiguate execution-relative inputs and workspace-relative backend results."""
from pathlib import Path


def result_path(owner, path):
    if not path:
        return path
    workspace = Path(owner.project_path)
    from modules.execution_scope import current_execution_scope
    scope = current_execution_scope()
    if scope and scope.is_sub_agent:
        from modules.scoped_execution_policy import fixed_execution_root
        base = fixed_execution_root(scope)
    else:
        base = Path(getattr(owner, 'execution_root', None) or workspace)
    if base == workspace:
        return path
    # Execution filesystem results are relative to the identity workspace. Return
    # an absolute path to the model so reusing it never prepends the worktree twice.
    return str(workspace / path)
