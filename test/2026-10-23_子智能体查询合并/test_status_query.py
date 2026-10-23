"""Unified parent/child queries and model/runtime identity boundaries."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import tool_loading
from core.tool_config import TOOL_CATEGORIES
from modules.sub_agent.communication import SubAgentCommunicationMixin
from modules.sub_agent.definitions import build_master_tools, build_sub_tools
from modules.sub_agent.manager import SubAgentManager
from modules.sub_agent.named import PUBLIC_TOOLS, handle_named_tool, public_agent
from server.context.sub_agent_views import stored_overview


class StatusQueryTests(unittest.TestCase):
    def setUp(self):
        self.tasks = {
            "first": self.record("Research", "parent", "running"),
            "second": self.record("Reviewer", "parent", "idle"),
            "third": self.record("Retired", "parent", "terminated"),
            "other": self.record("Research", "other-parent", "idle"),
        }
        self.manager = SimpleNamespace(tasks=self.tasks, reconcile_task_states=Mock(),
                                       _build_stats_summary=Mock())
        self.terminal = SimpleNamespace(sub_agent_manager=self.manager,
                                       context_manager=SimpleNamespace(current_conversation_id="parent"))

    @staticmethod
    def record(name, conversation_id, status):
        return dict(name=name, task_id="internal-" + conversation_id + "-" + name,
                    conversation_id=conversation_id, status=status, summary="Task " + name,
                    working_dir="/project/child", created_at=1, updated_at=2,
                    model_key="fake", intelligence="same", access_level="working_dir_write",
                    round_id=2, end_reason="completed" if status == "idle" else None,
                    final_result="Previous answer", stats_file="/nonexistent/status-query/stats.json")

    def query(self, arguments):
        return asyncio.run(handle_named_tool(self.terminal, "get_sub_agent_status", arguments))

    def assert_no_internal_ids(self, result):
        for row in result["results"]:
            self.assertNotIn("task_id", row)
            self.assertNotIn("conversation_id", row)

    def test_all_query_includes_idle_and_terminated_only_from_current_parent(self):
        result = self.query({})
        self.assertTrue(result["success"])
        self.assertEqual([row["name"] for row in result["results"]], ["Research", "Reviewer", "Retired"])
        self.assertEqual([row["status"] for row in result["results"]], ["running", "idle", "terminated"])
        self.assertTrue(all(row["found"] for row in result["results"]))
        self.assert_no_internal_ids(result)
        self.manager.reconcile_task_states.assert_called_once_with(conversation_id="parent")

    def test_named_query_retains_order_casefold_missing_result_and_details(self):
        result = self.query({"names": ["reviewer", "Missing", "RESEARCH"]})
        self.assertEqual([row["found"] for row in result["results"]], [True, False, True])
        self.assertEqual(result["results"][0]["final_result"], "Previous answer")
        self.assertEqual(result["results"][0]["round_id"], 2)
        self.assertEqual(result["results"][1]["name"], "Missing")
        self.assertIn("不存在", result["results"][1]["error"])
        self.assert_no_internal_ids(result)

    def test_explicit_empty_or_invalid_names_rejected(self):
        for value in ([], None, "Research", {}):
            with self.subTest(value=value):
                result = self.query({"names": value})
                self.assertFalse(result["success"])
                self.assertIn("names", result["error"])

    def test_empty_parent_returns_successful_empty_results(self):
        self.terminal.context_manager.current_conversation_id = "empty-parent"
        self.assertEqual(self.query({}), {"success": True, "results": []})

    def test_child_uses_identical_query_schema_scope_and_results(self):
        child = SimpleNamespace(manager=self.manager, multi_agent_state=object(),
                                task_record={"conversation_id": "parent"})
        for arguments in ({}, {"names": ["Reviewer", "Missing"]}, {"names": []}, {"names": None}):
            result = asyncio.run(SubAgentCommunicationMixin._execute_multi_agent_tool(
                child, "get_sub_agent_status", arguments))
            self.assertEqual(result, self.query(arguments))
            if result["success"]:
                self.assert_no_internal_ids(result)

    def test_runtime_and_stored_views_preserve_both_ids_without_leaking_into_public_fields(self):
        overview = SubAgentManager.get_overview(self.manager, "parent")
        self.assertEqual(len(overview), 3)
        self.assertEqual(overview[0]["task_id"], self.tasks["first"]["task_id"])
        self.assertEqual(overview[0]["conversation_id"], "parent")
        with patch("server.context.sub_agent_views.persisted_sub_agents", return_value={"tasks": self.tasks}):
            stored = stored_overview(SimpleNamespace(), "parent")
        self.assertEqual([item["task_id"] for item in stored], [item["task_id"] for item in overview])
        self.assertTrue(all(item["conversation_id"] == "parent" for item in stored))
        for task in self.tasks.values():
            self.assertNotIn("task_id", public_agent(task))
            self.assertNotIn("conversation_id", public_agent(task))
            self.assertIn("task_id", task)

    def test_single_query_definition_and_registry(self):
        master = {item["function"]["name"]: item["function"] for item in build_master_tools()}
        child = {item["function"]["name"]: item["function"] for item in build_sub_tools()}
        self.assertEqual(master["get_sub_agent_status"], child["get_sub_agent_status"])
        self.assertNotIn("names", master["get_sub_agent_status"]["parameters"]["required"])
        self.assertEqual(master["get_sub_agent_status"]["parameters"]["properties"]["names"]["minItems"], 1)
        for names in (master, child, PUBLIC_TOOLS, tool_loading.deferrable_tool_names(),
                      TOOL_CATEGORIES["sub_agent"].tools):
            self.assertIn("get_sub_agent_status", names)
            self.assertNotIn("list_active_sub_agents", names)


if __name__ == "__main__":
    unittest.main()
