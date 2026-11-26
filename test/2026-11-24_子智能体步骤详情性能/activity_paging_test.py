"""Boundary regression for the append-only sub-agent activity pager.

Run from the worktree root:
    PYTHONDONTWRITEBYTECODE=1 python3 -B test/2026-11-24_子智能体步骤详情性能/activity_paging_test.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "modules" / "sub_agent" / "activity.py"
_spec = importlib.util.spec_from_file_location("activity_paging_mod", _MODULE_PATH)
activity = importlib.util.module_from_spec(_spec)
sys.modules["activity_paging_mod"] = activity
_spec.loader.exec_module(activity)
read_activity_chunk = activity.read_activity_chunk


def _jsonl(start: int, count: int) -> bytes:
    return b"".join(
        json.dumps({"n": index}).encode() + b"\n"
        for index in range(start, start + count)
    )


class ActivityPagingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "progress.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_and_half_record_keep_cursor_zero(self):
        empty = read_activity_chunk(self.path, limit=200)
        self.assertEqual(empty.entries, [])
        self.assertEqual(empty.next_offset, 0)
        self.assertFalse(empty.partial_tail)

        self.path.write_bytes(b'{"n":1')
        half = read_activity_chunk(self.path, limit=200)
        self.assertEqual(half.entries, [])
        self.assertEqual(half.next_offset, 0)
        self.assertTrue(half.partial_tail)
        self.assertFalse(half.truncated)

    def test_next_offset_at_size_is_not_partial(self):
        self.path.write_bytes(b'{"n":1}\n')
        chunk = read_activity_chunk(self.path, limit=200)
        self.assertEqual([e["n"] for e in chunk.entries], [1])
        self.assertEqual(chunk.next_offset, self.path.stat().st_size)
        self.assertFalse(chunk.partial_tail)
        self.assertFalse(chunk.has_more_after)

    def test_exact_page_limit_with_cross_block_following_line(self):
        line1 = json.dumps({"s": "a" * 32758}, separators=(",", ":")).encode() + b"\n"
        line2 = json.dumps({"s": "b" * 32760}, separators=(",", ":")).encode() + b"\n"
        first = line1 + line2
        self.assertEqual(len(first), 65536)
        self.assertEqual(first.count(b"\n"), 2)
        self.path.write_bytes(first + b'{"s":"c"}\n')

        page = read_activity_chunk(self.path, offset=0, limit=2)
        self.assertEqual(page.next_offset, 65536)
        self.assertTrue(page.has_more_after)
        self.assertFalse(page.partial_tail)

        follow = read_activity_chunk(self.path, offset=page.next_offset, limit=2)
        self.assertEqual(follow.entries, [{"s": "c"}])
        self.assertFalse(follow.has_more_after)
        self.assertFalse(follow.partial_tail)

    def test_exact_page_limit_with_only_half_record_is_partial_not_drainable(self):
        self.path.write_bytes(b'{"a":1}\npartial')
        page = read_activity_chunk(self.path, offset=0, limit=1)
        self.assertFalse(page.has_more_after)
        self.assertTrue(page.partial_tail)
        self.assertEqual(page.next_offset, len(b'{"a":1}\n'))

    def test_empty_log_then_more_than_one_page_is_drained_without_loss(self):
        self.path.write_bytes(b"")
        self.path.write_bytes(_jsonl(1, 1000))
        first = read_activity_chunk(self.path, offset=0, limit=200)
        self.assertEqual(len(first.entries), 200)
        self.assertTrue(first.has_more_after)
        seen = [e["n"] for e in first.entries]
        cursor = first.next_offset
        for _ in range(20):
            page = read_activity_chunk(self.path, offset=cursor, limit=200)
            seen.extend(e["n"] for e in page.entries)
            cursor = page.next_offset
            if not page.has_more_after:
                break
        self.assertEqual(seen, list(range(1, 1001)))

    def test_tail_and_older_page_continuity(self):
        self.path.write_bytes(_jsonl(0, 1000))
        tail = read_activity_chunk(self.path, limit=200)
        self.assertEqual([e["n"] for e in tail.entries], list(range(800, 1000)))
        self.assertTrue(tail.has_more_before)
        self.assertFalse(tail.partial_tail)

        older = read_activity_chunk(self.path, before=tail.start_offset, limit=200)
        self.assertEqual([e["n"] for e in older.entries], list(range(600, 800)))
        self.assertEqual(older.next_offset, tail.start_offset)
        self.assertTrue(older.has_more_before)

        zero = read_activity_chunk(self.path, before=0, limit=200)
        self.assertEqual(zero.entries, [])
        self.assertFalse(zero.reset)
        self.assertEqual(zero.next_offset, 0)

    def test_invalid_cursor_resets_to_tail_and_skips_malformed(self):
        self.path.write_bytes(b'bad\n{}\n[2]\n{}\n')
        page = read_activity_chunk(self.path, offset=2, limit=3)
        self.assertTrue(page.reset)
        self.assertEqual(page.entries, [{}, {}])
        self.assertEqual(page.start_offset, 4)
        self.assertFalse(page.truncated)


if __name__ == "__main__":
    unittest.main()
