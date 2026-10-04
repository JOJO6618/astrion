"""Offline regressions: no running backend, real conversations or model requests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server.runtime.session_modes import session_mode_overrides
from server.chat_flow_task_support import inject_runtime_user_message
from server.tasks.models import TaskManager
from server.runtime.service import RuntimeService
from utils.context_manager.media_mixin import MediaMixin
from utils.media_store import MediaStore


class QuickRuntimeTests(unittest.TestCase):
    def test_explicit_modes_override_plan_defaults(self):
        terminal = SimpleNamespace(
            get_work_mode=lambda: "plan", get_permission_mode=lambda: "readonly",
            get_execution_mode=lambda: "direct",
        )
        modes = session_mode_overrides(terminal, SimpleNamespace(host_mode=True),
                                       "execute", "unrestricted", "sandbox")
        self.assertEqual(modes, {"work_mode": "execute", "permission_mode": "unrestricted",
                                 "execution_mode": "sandbox"})

    def test_restricted_and_plan_modes_lock_sandbox(self):
        terminal = Mock()
        principal = SimpleNamespace(host_mode=True)
        self.assertEqual(session_mode_overrides(terminal, principal, "plan", "unrestricted", "direct"),
                         {"work_mode": "plan", "permission_mode": "readonly", "execution_mode": "sandbox"})
        self.assertEqual(session_mode_overrides(terminal, principal, "execute", "approval", "direct")["execution_mode"], "sandbox")
        with self.assertRaises(PermissionError):
            session_mode_overrides(terminal, SimpleNamespace(host_mode=False), "execute", "unrestricted", "direct")
        with self.assertRaises(ValueError):
            session_mode_overrides(terminal, principal, "invalid", "unrestricted", "sandbox")

    def test_guidance_queue_keeps_media_and_does_not_enqueue_pending(self):
        manager = TaskManager()
        image = {"data_url": "data:image/png;base64,AA==", "kind": "image"}
        record = SimpleNamespace(username="test", task_id="t1", status="running",
                                 runtime_guidance_queue=[], runtime_pending_queue=[], updated_at=0)
        manager._tasks["t1"] = record
        with patch("server.tasks.models.save_queue"):
            result = manager.enqueue_runtime_guidance("test", "t1", "", images=[image])
            self.assertTrue(result["success"])
            self.assertEqual(record.runtime_pending_queue, [])
            entries = manager.consume_runtime_guidance_for_injection("test", "t1")
        self.assertEqual(entries[0]["images"], [image])
        self.assertTrue(entries[0]["text"])
        self.assertEqual(record.runtime_guidance_queue, [])

    def test_image_guidance_persists_and_injects_multimodal_content(self):
        image = {"data_url": "data:image/png;base64,AA==", "kind": "image"}
        refs = [{"kind": "image", "media_id": "stored-image"}]
        content = [{"type": "text", "text": "guidance"},
                   {"type": "image_url", "image_url": {"url": image["data_url"]}}]
        context = Mock()
        context.add_conversation.return_value = {"media_refs": refs, "images": [image]}
        context._build_content_with_images.return_value = content
        terminal = SimpleNamespace(context_manager=context)
        messages = [{"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
                    {"role": "tool", "tool_call_id": "a"}, {"role": "tool", "tool_call_id": "b"}]
        sender = Mock()
        inject_runtime_user_message(web_terminal=terminal, messages=messages, text="Look at this",
                                    source="guidance", inline=True, after_tool_call_id="a",
                                    images=[image], sender=sender)
        self.assertEqual(messages[-1], {"role": "user", "content": content})
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(context.add_conversation.call_args.kwargs["images"], [image])
        self.assertEqual(context._build_content_with_images.call_args.kwargs["media_refs"], refs)
        self.assertEqual(sender.call_args.args[1]["images"], [image])


    def test_real_data_url_roundtrip_becomes_model_image_block(self):
        payload = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        with tempfile.TemporaryDirectory() as directory:
            context = MediaMixin()
            context.media_store = MediaStore(Path(directory))
            context.project_path = directory
            images = [{"data_url": payload, "kind": "image"}]
            candidates = context._prepare_media_candidates(images=images)
            refs = [context._store_media_item_from_candidate(candidates[0])]
            self.assertTrue(refs[0]["media_id"])
            content = context._build_content_with_images("Look at this", images, media_refs=refs)
            self.assertEqual(content[0], {"type": "text", "text": "Look at this"})
            self.assertEqual(content[1], {"type": "image_url", "image_url": {"url": payload}})

    def test_runtime_session_persists_explicit_modes(self):
        service = RuntimeService()
        manager = Mock()
        manager.create_conversation.return_value = "test-session"
        terminal = SimpleNamespace(context_manager=SimpleNamespace(conversation_manager=manager))
        workspace = SimpleNamespace(project_path="/test", data_dir="/test")
        principal = SimpleNamespace(host_mode=True, preferred_run_mode=None,
                                    preferred_thinking_mode=None, preferred_model_key=None)
        with patch.object(service, "_resources_for_query", return_value=(terminal, workspace)), \
                patch("modules.personalization_manager.load_personalization_config", return_value={}):
            service.create_session("test", "w1", principal, work_mode="execute",
                                   permission_mode="unrestricted", execution_mode="sandbox")
        metadata = manager.create_conversation.call_args.kwargs["metadata_overrides"]
        self.assertEqual(metadata["work_mode"], "execute")
        self.assertEqual(metadata["permission_mode"], "unrestricted")
        self.assertEqual(metadata["execution_mode"], "sandbox")


if __name__ == "__main__":
    unittest.main()
