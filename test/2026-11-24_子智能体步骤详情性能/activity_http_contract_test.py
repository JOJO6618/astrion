"""HTTP contract test: Python activity endpoint vs Rust JSON contract.

Runs the real Flask view (including auth + with_terminal decorators) against a
temporary directory fixture. No server process, no frontend, no real data.

Run with the project venv that has Flask:
    /Users/jojo/Desktop/agents/正在修复中/agents/.venv/bin/python -B \
        test/2026-11-24_子智能体步骤详情性能/activity_http_contract_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from flask import Flask
    import server.conversation as conversation
except ImportError as exc:  # system python lacks Flask; the project venv has it
    Flask = None
    conversation = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


PAYLOAD_KEYS = {
    "task_id",
    "status",
    "entries",
    "start_offset",
    "next_offset",
    "file_size",
    "reset",
    "truncated",
    "has_more_before",
    "has_more_after",
    "partial_tail",
}

INT_KEYS = {"start_offset", "next_offset", "file_size"}
BOOL_KEYS = {
    "reset",
    "truncated",
    "has_more_before",
    "has_more_after",
    "partial_tail",
}


class _Manager:
    def __init__(self, task):
        self.tasks = {"sub_a": task}

    def _load_state(self):
        return None


class _Terminal:
    def __init__(self, task):
        self.sub_agent_manager = _Manager(task)


@unittest.skipIf(Flask is None, f"Flask unavailable: {_IMPORT_ERROR}")
class ActivityHttpContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.progress = Path(self._tmp.name) / "progress.jsonl"
        self.task = {
            "task_id": "sub_a",
            "status": "idle",
            "progress_file": str(self.progress),
            "task_root": self._tmp.name,
        }
        self.terminal = _Terminal(self.task)

        self.app = Flask(__name__)
        self.app.secret_key = "activity-contract-test"
        self.app.add_url_rule(
            "/api/sub_agents/<task_id>/activity",
            endpoint="activity",
            view_func=conversation.get_sub_agent_activity,
        )
        self._stack = ExitStack()
        self._stack.enter_context(
            patch("server.auth_helpers.is_logged_in", return_value=True)
        )
        self._stack.enter_context(
            patch(
                "server.context.decorators.get_user_resources",
                return_value=(self.terminal, SimpleNamespace(workspace_id="ws_a")),
            )
        )
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["username"] = "host"
            session["role"] = "admin"

    def tearDown(self):
        self._stack.close()
        self._tmp.cleanup()

    def _get(self, query: str):
        return self.client.get("/api/sub_agents/sub_a/activity" + query)

    def _assert_page(self, page, expected):
        self.assertEqual(set(page), PAYLOAD_KEYS)
        self.assertEqual(page, expected)
        for key in INT_KEYS:
            self.assertIs(type(page[key]), int, key)
        for key in BOOL_KEYS:
            self.assertIs(type(page[key]), bool, key)
        self.assertIsInstance(page["entries"], list)

    def test_tail_matches_rust_json_contract_exactly(self):
        # Rust sub_agents_contract.rs fixture: three complete lines + half line.
        self.progress.write_bytes(
            b'{"id":1}\n{"id":2}\n{"id":3}\n{"partial":'
        )
        response = self._get("?limit=1")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(set(body), {"success", "data"})
        self.assertIs(body["success"], True)
        self._assert_page(
            body["data"],
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 3}],
                "start_offset": 18,
                "next_offset": 27,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": True,
                "has_more_after": False,
                "partial_tail": True,
            },
        )

    def test_before_and_forward_pages_match_contract(self):
        self.progress.write_bytes(
            b'{"id":1}\n{"id":2}\n{"id":3}\n{"partial":'
        )
        tail = self._get("?limit=1").get_json()["data"]
        self._assert_page(
            tail,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 3}],
                "start_offset": 18,
                "next_offset": 27,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": True,
                "has_more_after": False,
                "partial_tail": True,
            },
        )

        previous = self._get("?before=18&limit=1").get_json()["data"]
        self._assert_page(
            previous,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 2}],
                "start_offset": 9,
                "next_offset": 18,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": True,
                "has_more_after": False,
                "partial_tail": False,
            },
        )
        self.assertEqual(previous["next_offset"], tail["start_offset"])

        forward = self._get("?offset=0&limit=2").get_json()["data"]
        self._assert_page(
            forward,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 1}, {"id": 2}],
                "start_offset": 0,
                "next_offset": 18,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": False,
                "has_more_after": True,
                "partial_tail": False,
            },
        )
        self.assertEqual(forward["next_offset"], previous["next_offset"])

        after = self._get("?offset=18&limit=2").get_json()["data"]
        self._assert_page(
            after,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 3}],
                "start_offset": 18,
                "next_offset": 27,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": False,
                "has_more_after": False,
                "partial_tail": True,
            },
        )

        zero = self._get("?before=0&limit=1").get_json()["data"]
        self._assert_page(
            zero,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [],
                "start_offset": 0,
                "next_offset": 0,
                "file_size": 38,
                "reset": False,
                "truncated": False,
                "has_more_before": False,
                "has_more_after": False,
                "partial_tail": False,
            },
        )

    def test_reset_validation_and_missing_file_match_contract(self):
        self.progress.write_bytes(b'{"id":1}\n{"id":2}\n{"id":3}\n')
        reset = self._get("?offset=999&limit=1").get_json()["data"]
        self._assert_page(
            reset,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [{"id": 3}],
                "start_offset": 18,
                "next_offset": 27,
                "file_size": 27,
                "reset": True,
                "truncated": False,
                "has_more_before": True,
                "has_more_after": False,
                "partial_tail": False,
            },
        )
        for query in ("?offset=-1", "?before=-1", "?before=4&offset=0"):
            self.assertEqual(self._get(query).status_code, 400, query)

        self.progress.unlink()
        missing = self._get("?offset=0").get_json()["data"]
        self._assert_page(
            missing,
            {
                "task_id": "sub_a",
                "status": "idle",
                "entries": [],
                "start_offset": 0,
                "next_offset": 0,
                "file_size": 0,
                "reset": True,
                "truncated": False,
                "has_more_before": False,
                "has_more_after": False,
                "partial_tail": False,
            },
        )

    def test_cursor_limit_default_and_clamp(self):
        self.progress.write_bytes(
            b"".join(
                json.dumps({"n": index}).encode() + b"\n"
                for index in range(1000)
            )
        )
        default_page = self._get("?offset=0").get_json()["data"]
        self.assertEqual(len(default_page["entries"]), 200)
        self.assertTrue(default_page["has_more_after"])
        self.assertFalse(default_page["partial_tail"])

        clamped = self._get("?offset=0&limit=10000").get_json()["data"]
        self.assertEqual(len(clamped["entries"]), 500)
        self.assertTrue(clamped["has_more_after"])

        tail = self._get("?limit=10000").get_json()["data"]
        self.assertEqual(len(tail["entries"]), 1000)
        self.assertFalse(tail["has_more_after"])
        self.assertFalse(tail["partial_tail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
