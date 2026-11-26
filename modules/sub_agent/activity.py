"""Paged reader for append-only sub-agent ``progress.jsonl`` files.

The activity endpoint is polled while a child agent runs. Reading and parsing
the whole file on every poll makes a long-running child progressively slower,
and the first open must not transfer the entire history in one response.

The file is append-only JSONL, so byte offsets are stable cursors:

* ``offset``  forward polling: return complete lines appended after the cursor.
* ``before``  older page: return the page immediately before that byte offset.
* no cursor  initial page: return the newest page (tail), never the whole file.

Only newline-terminated lines are consumed. A line still being written at EOF
is reported through ``partial_tail`` and is never consumed, so the client can
wait for the next poll instead of spinning on an empty page.

All reads are bounded by the file size captured in one ``stat`` call. Bytes
appended after that snapshot belong to the next poll.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional

_BLOCK_SIZE = 64 * 1024
_PARTIAL_SCAN_LIMIT = 256 * 1024


@dataclass(frozen=True)
class ActivityChunk:
    entries: List[Dict[str, Any]]
    start_offset: int
    next_offset: int
    file_size: int
    reset: bool
    truncated: bool
    has_more_before: bool
    has_more_after: bool
    partial_tail: bool


def _empty(offset: int = 0, *, reset: bool = False) -> ActivityChunk:
    return ActivityChunk(
        entries=[], start_offset=offset, next_offset=offset, file_size=0,
        reset=reset, truncated=False, has_more_before=False,
        has_more_after=False, partial_tail=False,
    )


def _parse_lines(raw: bytes) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _valid_offset(path: Path, size: int, offset: int) -> bool:
    """A cursor must be inside the file and immediately follow a newline."""
    if offset < 0 or offset > size:
        return False
    if offset == 0:
        return True
    with path.open("rb") as handle:
        handle.seek(offset - 1)
        return handle.read(1) == b"\n"


def _has_newline_after(handle, start: int, size: int) -> bool:
    """Whether a complete line exists after ``start`` within the snapshot.

    A long unterminated line is reported as partial instead of scanning the
    whole file; the next poll will finish it.
    """
    if start >= size:
        return False
    handle.seek(start)
    remaining = size - start
    scanned = 0
    while remaining > 0 and scanned < _PARTIAL_SCAN_LIMIT:
        block = handle.read(min(_BLOCK_SIZE, remaining, _PARTIAL_SCAN_LIMIT - scanned))
        if not block:
            break
        if b"\n" in block:
            return True
        scanned += len(block)
        remaining -= len(block)
    return False


def _read_forward(path: Path, start: int, limit: int, size: int) -> ActivityChunk:
    """Read at most ``limit`` complete lines beginning at ``start``.

    Reading stops at the ``size`` snapshot even if the writer keeps appending,
    so the returned ``next_offset`` never points past data this page classified.
    """
    data = bytearray()
    newlines = 0
    remaining = max(0, size - start)
    with path.open("rb") as handle:
        handle.seek(start)
        while newlines < limit and remaining > 0:
            block = handle.read(min(_BLOCK_SIZE, remaining))
            if not block:
                break
            data += block
            remaining -= len(block)
            newlines += block.count(b"\n")

        if newlines > limit:
            cut = -1
            for _ in range(limit):
                cut = data.find(b"\n", cut + 1)
            complete = bytes(data[: cut + 1])
            next_offset = start + len(complete)
            # The extra newline in the block guarantees another complete line,
            # possibly followed by a partial tail. Drain now; do not scan it.
            has_more_after = True
            partial_tail = False
        else:
            last_newline = data.rfind(b"\n")
            complete = bytes(data[: last_newline + 1]) if last_newline >= 0 else b""
            next_offset = start + len(complete)
            # This covers the exact-``limit`` case where the next complete line
            # sits in a block we did not read yet: without it, a drain loop
            # would stop and a completed activity could miss its tail.
            more_complete = _has_newline_after(handle, next_offset, size)
            has_more_after = more_complete
            partial_tail = next_offset < size and not more_complete

    return ActivityChunk(
        entries=_parse_lines(complete),
        start_offset=start,
        next_offset=next_offset,
        file_size=size,
        reset=False,
        truncated=False,
        has_more_before=False,
        has_more_after=has_more_after,
        partial_tail=partial_tail,
    )


def _read_backward(
    path: Path, end: int, limit: int, size: int, *, is_tail: bool = False,
) -> ActivityChunk:
    """Read the page of at most ``limit`` complete lines ending at ``end``."""
    if end <= 0:
        return ActivityChunk([], 0, 0, size, False, False, False, False, False)

    data = bytearray()
    position = end
    newlines = 0
    with path.open("rb") as handle:
        while position > 0 and newlines < limit + 1:
            start = max(0, position - _BLOCK_SIZE)
            handle.seek(start)
            block = handle.read(position - start)
            data[:0] = block
            position = start
            newlines += block.count(b"\n")

    last_newline = data.rfind(b"\n")
    if last_newline < 0:
        # The file contains no complete line yet. Cursor 0 lets the next poll
        # read the line once its newline arrives; ``end`` would be an invalid
        # cursor because it does not follow a newline.
        return ActivityChunk(
            [], 0, 0, size, False, False, False, False,
            # A tail poll with bytes but no newline is an unfinished record.
            # ``end == size`` here, so comparing end to size would be false.
            is_tail and size > 0,
        )

    complete_end = position + last_newline
    body = bytes(data[:last_newline])
    lines = body.split(b"\n")
    selected = lines[-limit:] if limit > 0 else lines
    selected_bytes = b"\n".join(selected)
    page_start = complete_end - len(selected_bytes)
    entries = _parse_lines(selected_bytes)
    next_offset = complete_end + 1
    return ActivityChunk(
        entries=entries,
        start_offset=page_start,
        # Forward polling continues after the newest complete line in this page.
        next_offset=next_offset,
        file_size=size,
        reset=False,
        truncated=False,
        has_more_before=page_start > 0,
        has_more_after=False,
        partial_tail=is_tail and next_offset < size,
    )


def read_activity_chunk(
    path: Optional[Path],
    *,
    offset: Optional[int] = None,
    before: Optional[int] = None,
    limit: Optional[int] = None,
) -> ActivityChunk:
    """Read one activity page.

    Callers must pass at most one of ``offset``/``before``. ``limit`` is the
    page size; the endpoint always supplies a bounded value.

    ``offset=0`` is a valid explicit request for the first forward page; only
    an omitted offset means "newest page".
    """
    if offset is not None and before is not None:
        raise ValueError("offset and before are mutually exclusive")
    page_size = max(1, int(limit or 1))
    if path is None:
        return _empty(0, reset=offset is not None or before is not None)
    path = Path(path)

    try:
        size = path.stat().st_size
    except OSError:
        return _empty(0, reset=offset is not None or before is not None)

    if before is not None:
        if before == 0:
            # Older-than-beginning is a valid empty page, not a stale cursor.
            return ActivityChunk([], 0, 0, size, False, False, False, False, False)
        if before < 0 or before > size or not _valid_offset(path, size, before):
            # The page anchor is stale or not on a line boundary: restart from
            # the newest page instead of returning a torn line.
            return replace(
                _read_backward(path, size, page_size, size, is_tail=True), reset=True,
            )
        return _read_backward(path, before, page_size, size)

    if offset is not None:
        if offset < 0 or offset > size or not _valid_offset(path, size, offset):
            return replace(
                _read_backward(path, size, page_size, size, is_tail=True), reset=True,
            )
        return _read_forward(path, offset, page_size, size)

    # No cursor: initial page is the tail, not the full history.
    return _read_backward(path, size, page_size, size, is_tail=True)
