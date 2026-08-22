from __future__ import annotations

import unittest
from types import SimpleNamespace


class ServerRefactorSmokeTest(unittest.TestCase):
    def test_chat_flow_imports_and_runner_binding(self):
        import server.chat_flow as chat_flow
        import server.chat_flow_runner as runner

        self.assertIs(chat_flow.handle_task_with_sender, runner.handle_task_with_sender)
        self.assertTrue(callable(chat_flow.start_chat_task))
        self.assertTrue(callable(chat_flow.run_chat_task_sync))

    def test_chat_flow_helper_functions(self):
        import server.chat_flow_helpers as helpers

        self.assertTrue(helpers.detect_tool_failure({"success": False}))
        self.assertFalse(helpers.detect_tool_failure({"success": True}))

    def test_runner_helpers_pure_functions(self):
        import server.chat_flow_runner_helpers as helpers

        self.assertEqual(helpers.extract_intent_from_partial('{"intent": "fix"}'), "fix")
        self.assertEqual(
            helpers.extract_intent_from_partial('{"intent": "\\u7b49\\u5f85\\u4e0b\\u8f7d\\u5b8c\\u6210"}'),
            "等待下载完成",
        )
        self.assertEqual(helpers.resolve_monitor_path({"file_path": "  a.txt  "}), "a.txt")
        self.assertEqual(helpers.resolve_monitor_memory([1, 2, 3], entry_limit=2), ["1", "2"])

    def test_conversation_stats_exports(self):
        import server.conversation as conversation
        import server.conversation_stats as stats

        self.assertTrue(callable(stats.build_admin_dashboard_snapshot))
        self.assertTrue(callable(conversation.compute_workspace_storage))
        self.assertTrue(callable(conversation.collect_upload_events))

    def test_custom_tools_refresh_disabled_after_dynamic_load(self):
        from core.main_terminal_parts.tools_definition import MainTerminalToolsDefinitionMixin

        class Registry:
            def reload(self):
                return [
                    {
                        "id": "demo_tool",
                        "description": "Demo tool",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ]

        class Terminal(MainTerminalToolsDefinitionMixin):
            custom_tools_enabled = True
            user_role = "admin"
            custom_tool_registry = Registry()

            def __init__(self):
                self.tool_categories_map = {
                    "custom": SimpleNamespace(
                        tools=[],
                        default_enabled=True,
                        silent_when_disabled=False,
                    )
                }
                self.tool_category_states = {"custom": False}
                self.admin_forced_category_states = {}
                self.disabled_tools = set()

            def _refresh_disabled_tools(self):
                disabled = set()
                for key, category in self.tool_categories_map.items():
                    if not self.tool_category_states.get(key, category.default_enabled):
                        disabled.update(category.tools)
                self.disabled_tools = disabled

        terminal = Terminal()
        custom_tools = terminal._build_custom_tools()

        self.assertEqual(custom_tools[0]["function"]["name"], "demo_tool")
        self.assertEqual(terminal.tool_categories_map["custom"].tools, ["demo_tool"])
        self.assertIn("demo_tool", terminal.disabled_tools)

    def test_context_applies_workspace_personalization_preferences(self):
        import server.context as context

        calls = []

        class Terminal:
            run_mode = "fast"
            thinking_mode = False
            model_key = None

            def apply_personalization_preferences(self, config, **kwargs):
                calls.append(config)

        workspace = SimpleNamespace(data_dir="/tmp/workspace-data")
        original_loader = context.load_personalization_config
        try:
            context.load_personalization_config = lambda data_dir: {
                "disabled_tool_categories": ["custom"]
            }
            context._apply_workspace_personalization_preferences(Terminal(), workspace)
        finally:
            context.load_personalization_config = original_loader

        self.assertEqual(calls, [{"disabled_tool_categories": ["custom"]}])


if __name__ == "__main__":
    unittest.main()
