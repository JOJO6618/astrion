"""回归测试：对话模型持久化与 /new 页面默认模型行为。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.context import _apply_workspace_personalization_preferences


class FakeSession:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, value):
        self._data[key] = value


class TestApplyWorkspacePersonalizationPreferences(unittest.TestCase):
    def _make_terminal(self, model_key="default-model"):
        terminal = MagicMock()
        terminal.model_key = model_key
        terminal._workspace_default_model_applied = False

        def _apply_personalization_preferences(config, apply_default_model=True):
            if apply_default_model:
                default_model = (config or {}).get("default_model")
                if default_model:
                    terminal.model_key = default_model
        terminal.apply_personalization_preferences = MagicMock(side_effect=_apply_personalization_preferences)
        return terminal

    def _make_workspace(self):
        workspace = MagicMock()
        workspace.data_dir = tempfile.mkdtemp()
        return workspace

    @patch("server.context.load_personalization_config")
    @patch("server.context.has_request_context", return_value=True)
    def test_session_model_restored(self, _hrc, mock_load_config):
        """session 中保存了模型时，应恢复到该模型。"""
        mock_load_config.return_value = {"default_model": "default-model"}
        terminal = self._make_terminal(model_key="old-model")
        terminal.set_model = MagicMock(side_effect=lambda mk: setattr(terminal, "model_key", mk))
        workspace = self._make_workspace()
        session = FakeSession()
        session["model_key"] = "session-model"

        with patch("server.context.session", session):
            _apply_workspace_personalization_preferences(terminal, workspace)

        terminal.set_model.assert_called_once_with("session-model")
        self.assertEqual(session.get("model_key"), "session-model")

    @patch("server.context.load_personalization_config")
    @patch("server.context.has_request_context", return_value=True)
    def test_default_model_applied_for_fresh_session(self, _hrc, mock_load_config):
        """没有 session 模型时，应应用默认模型（且仅一次）。"""
        mock_load_config.return_value = {"default_model": "default-model"}
        terminal = self._make_terminal(model_key="kimi-k2.6")
        workspace = self._make_workspace()
        session = FakeSession()

        with patch("server.context.session", session):
            _apply_workspace_personalization_preferences(terminal, workspace)

        terminal.set_model.assert_not_called()
        self.assertEqual(terminal.model_key, "default-model")
        self.assertEqual(session.get("model_key"), "default-model")
        self.assertTrue(terminal._workspace_default_model_applied)


class TestLoadConversationRestoreModel(unittest.TestCase):
    @patch("core.web_terminal.logger")
    def test_restore_model_true_restores_saved_model(self, _mock_logger):
        """显式加载对话时应恢复对话保存的模型。"""
        from core.web_terminal import WebTerminal

        terminal = MagicMock(spec=WebTerminal)
        terminal.model_key = "default-model"
        terminal.thinking_mode = False
        terminal.run_mode = "fast"
        terminal.multi_agent_mode = False
        terminal.context_manager = MagicMock()
        terminal.context_manager.load_conversation_by_id.return_value = True

        cm = MagicMock()
        cm.load_conversation.return_value = {
            "metadata": {
                "thinking_mode": False,
                "model_key": "saved-model",
                "multi_agent_mode": False,
            }
        }
        terminal.context_manager._get_conversation_manager_for_id.return_value = cm

        terminal.set_model = MagicMock(side_effect=lambda mk: setattr(terminal, "model_key", mk))
        terminal.set_permission_mode = MagicMock()
        terminal.set_execution_mode = MagicMock()
        terminal.set_network_permission = MagicMock()
        terminal.api_client = MagicMock()
        terminal.current_session_id = 0

        # 调用实际方法
        result = WebTerminal.load_conversation(terminal, "conv_test_001", restore_model=True)

        terminal.set_model.assert_called_once_with("saved-model")
        self.assertEqual(terminal.model_key, "saved-model")
        self.assertTrue(result.get("success"))

    @patch("core.web_terminal.logger")
    def test_restore_model_false_keeps_current_model(self, _mock_logger):
        """程序启动自动恢复最近对话时不应恢复模型，避免 /new 页面显示旧模型。"""
        from core.web_terminal import WebTerminal

        terminal = MagicMock(spec=WebTerminal)
        terminal.model_key = "default-model"
        terminal.thinking_mode = False
        terminal.run_mode = "fast"
        terminal.multi_agent_mode = False
        terminal.context_manager = MagicMock()
        terminal.context_manager.load_conversation_by_id.return_value = True

        cm = MagicMock()
        cm.load_conversation.return_value = {
            "metadata": {
                "thinking_mode": False,
                "model_key": "saved-model",
                "multi_agent_mode": False,
            }
        }
        terminal.context_manager._get_conversation_manager_for_id.return_value = cm

        terminal.set_model = MagicMock(side_effect=lambda mk: setattr(terminal, "model_key", mk))
        terminal.set_permission_mode = MagicMock()
        terminal.set_execution_mode = MagicMock()
        terminal.set_network_permission = MagicMock()
        terminal.api_client = MagicMock()
        terminal.current_session_id = 0

        result = WebTerminal.load_conversation(terminal, "conv_test_001", restore_model=False)

        terminal.set_model.assert_not_called()
        self.assertEqual(terminal.model_key, "default-model")
        self.assertTrue(result.get("success"))


if __name__ == "__main__":
    unittest.main()
