"""Select an executor without silently substituting controller resources."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import ExecutionBackend


def get_execution_backend(owner: Any) -> ExecutionBackend:
    """Return the explicit backend or lazily wrap existing local resources.

    Laziness keeps lightweight queries and old isolated test owners from
    constructing processes or execution resources merely by importing modules.
    Once a backend is bound it is authoritative, including when it raises.
    """
    if owner is None:
        raise ValueError("An execution resource owner is required")
    backend = getattr(owner, "execution_backend", None)
    if backend is None:
        from .local import LocalExecutionBackend
        backend = LocalExecutionBackend(owner)
        owner.execution_backend = backend
    return backend


def is_local_backend(owner: Any) -> bool:
    """Recognize the trusted local adapter, not a peer-advertised capability.

    Unbound legacy owners use the local adapter on first access. A remote or
    fake backend cannot grant host access by declaring ``is_local=True``.
    """
    from .local import LocalExecutionBackend
    backend = getattr(owner, "execution_backend", None)
    return backend is None or (
        type(backend) is LocalExecutionBackend and backend.owner is owner
    )
