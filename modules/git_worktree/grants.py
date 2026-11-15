"""Conversation-scoped external execution roots.

一个对话绑定到工作区外的工作树时，沙箱必须临时放行该目录；这个模块只承载
「当前工具调用上下文里额外放行的执行根」，不写任何用户级策略文件，因此退出
绑定（或进程重启后重新绑定）都不会留下持久授权。

信任边界：根路径只来自已校验的对话工作树绑定与子智能体记录，绝不来自工具参数。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence, Tuple


_extra_roots: ContextVar[Tuple[Path, ...]] = ContextVar('git_worktree_extra_roots', default=())


def normalize_roots(raw: Optional[Iterable[object]]) -> Tuple[Path, ...]:
    """把候选路径规范化成绝对、去重、稳定顺序的根列表。"""
    roots = []
    seen = set()
    for item in raw or ():
        try:
            path = Path(str(item)).expanduser()
        except (TypeError, ValueError, OSError):
            continue
        if not path.is_absolute():
            continue
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return tuple(roots)


def current_extra_roots() -> Tuple[Path, ...]:
    return _extra_roots.get()


@contextmanager
def bind_extra_roots(roots: Optional[Iterable[object]]) -> Iterator[Tuple[Path, ...]]:
    normalized = normalize_roots(roots)
    token = _extra_roots.set(normalized)
    try:
        yield normalized
    finally:
        _extra_roots.reset(token)


def roots_below(path: Optional[object], roots: Sequence[Path]) -> Tuple[Path, ...]:
    """返回 `path` 落在其中的根（用于判断某个目录是否在已放行范围内）。"""
    if path is None:
        return ()
    try:
        target = Path(str(path)).expanduser()
        if not target.is_absolute():
            return ()
        target = target.resolve()
    except (TypeError, ValueError, OSError):
        return ()
    matched = []
    for root in roots:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        matched.append(root)
    return tuple(matched)


def external_roots(execution_root: Optional[object], workspace: Optional[object]) -> Tuple[Path, ...]:
    """执行根在工作区之外时返回它，否则返回空。

    只用于「已经完成绑定校验」的执行目录：是否允许位于工作区外由
    `git_worktree.binding.checked_root` 与 `service.Worktrees` 判定，这里不重复授权。
    """
    if not execution_root:
        return ()
    try:
        target = Path(str(execution_root)).expanduser().resolve()
        root = Path(str(workspace)).expanduser().resolve() if workspace else None
    except (TypeError, ValueError, OSError):
        return ()
    if root is not None:
        try:
            target.relative_to(root)
        except ValueError:
            return (target,)
        return ()
    return (target,)
