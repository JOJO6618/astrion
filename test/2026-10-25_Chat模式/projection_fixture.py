"""Emit synthetic events through the production snapshot projector, without app/config IO."""
import importlib.util
import json
from pathlib import Path

path = Path(__file__).resolve().parents[2] / "server/conversation_view/projector.py"
spec = importlib.util.spec_from_file_location("chat_projection_fixture", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
projection = module.RuntimeProjection("task_fixture", "conv_fixture", "synthetic")
phases = {}
index = 0


def event(kind, data=None):
    global index
    projection.apply({"idx": index, "type": kind, "data": data or {}, "ts": 1000 + index})
    index += 1


def snapshot(name):
    phases[name] = projection.snapshot()


event("ai_message_start")
event("api_request_start")
snapshot("waiting")
event("thinking_start", {"response_message_id": "response"})
event("thinking_chunk", {"response_message_id": "response", "content": "Thinking received"})
snapshot("thinking")
event("thinking_end", {"response_message_id": "response", "full_content": "Thinking received"})
event("text_start", {"response_message_id": "response"})
event("text_chunk", {"response_message_id": "response", "content": "Visible reply"})
snapshot("text")
event("text_end", {"response_message_id": "response", "full_content": "Visible reply"})
event("tool_preparing", {"id": "call", "name": "web_search"})
snapshot("preparing")
event("tool_start", {"id": "execution", "preparing_id": "call", "name": "web_search", "arguments": {"query": "fixture", "intent": "Search intent"}})
snapshot("tool")
event("tool_update_action", {"id": "execution", "status": "completed", "result": {"success": True}})
event("api_request_start")
snapshot("next_request")
event("text_start", {"response_message_id": "response_final"})
event("text_chunk", {"response_message_id": "response_final", "content": "Final answer"})
event("text_end", {"response_message_id": "response_final", "full_content": "Final answer"})
event("task_complete")
snapshot("finished")
print(json.dumps(phases))
