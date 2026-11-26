"""Use Python's production ConversationManager against an isolated test workspace."""

from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.conversation_manager import ConversationManager


def chat_fixture(action, root, conversation_id, expected):
    from modules.chat_mode.storage import ChatStorage
    from modules.chat_mode.tree import MessageTree

    storage = ChatStorage(Path(root) / "chat")
    if action == "chat_seed":
        manager = storage.manager
        manager._save_conversation_file(conversation_id, expected)
        manager._update_index(conversation_id, expected)
        left = storage.begin(conversation_id, "python-begin-left", message={
            "role": "user", "content": "original question", "message_id": "msg_root",
            "metadata": {"python_extension": [1, {"keep": True}]},
        })["value"]
        storage.append(conversation_id, left["id"], {
            "role": "assistant", "content": "first answer", "message_id": "msg_left",
            "metadata": {"responses_reasoning_items": [
                {"type": "reasoning", "encrypted_content": "old-chat-opaque"},
            ]},
        })
        storage.finish(conversation_id, left["id"])
        right = storage.begin(conversation_id, "python-begin-right",
                              regenerate_turn_id=left["id"])["value"]
        storage.append(conversation_id, right["id"], {
            "role": "assistant", "content": "other answer", "message_id": "msg_right",
            "media_refs": [{"source_path": r"C:\media\old.png", "path": "asset/old.png"}],
        })
        storage.finish(conversation_id, right["id"])
        return
    if action != "chat_check_and_append":
        raise ValueError(f"Unknown synthetic Chat operation {action}")

    loaded = storage.read(conversation_id)
    old = expected["original"]
    assert loaded["id"] == old["id"] == conversation_id
    assert loaded["messages"][:len(old["messages"])] == old["messages"]
    assert loaded["metadata"]["chat"]["turns"][:2] == old["metadata"]["chat"]["turns"]
    assert loaded["metadata"]["chat"]["commands"] == old["metadata"]["chat"]["commands"]
    assert loaded["metadata"]["chat"]["future"] == old["metadata"]["chat"]["future"]
    assert loaded["metadata"]["frozen_system_prompt"] == old["metadata"]["frozen_system_prompt"]
    assert loaded["unknown_root"] == old["unknown_root"]
    tree = MessageTree(loaded)
    assert tree.state["revision"] == expected["revision"]
    assert tree.state["active_turn_id"] is None
    assert tree.siblings("msg_left") == ["msg_left", "msg_right", "msg_rust_regenerated"]
    for leaf, identities in expected["paths"].items():
        assert [message["message_id"] for message in tree.path(leaf, latest=False)] == identities
    assert tree.state["latest_leaf_id"] == "msg_rust_reply"
    assert loaded["metadata"]["total_messages"] == len(loaded["messages"])
    for message in loaded["messages"][len(old["messages"]):]:
        datetime.fromisoformat(message["timestamp"])

    continued = storage.begin(conversation_id, "python-after-rust", parent_id="msg_rust_reply",
                              message={"role":"user", "content":"Python continued", "message_id":"msg_python_again"},
                              expected_revision=expected["revision"])["value"]
    storage.finish(conversation_id, continued["id"], "stopped")
    assert storage.read(conversation_id)["metadata"]["chat"]["latest_leaf_id"] == "msg_python_again"


def main():
    action, data_dir, workspace, conversation_id = sys.argv[1:]
    expected = json.load(sys.stdin)
    if action.startswith("chat_"):
        chat_fixture(action, data_dir, conversation_id, expected)
        return

    manager = ConversationManager(base_dir=data_dir, project_path=workspace, read_only=True)
    path = manager._get_conversation_file_path(conversation_id)
    if action == "seed":
        manager._save_conversation_file(conversation_id, expected)
        manager._update_index(conversation_id, expected)
        return
    if action != "check_and_append":
        raise ValueError(f"Unknown synthetic legacy operation {action}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = manager.read_conversation(conversation_id)
    original = expected["original"]
    count = len(original["messages"])
    assert raw["id"] == loaded["id"] == original["id"]
    assert raw["messages"][:count] == loaded["messages"][:count] == original["messages"]
    assert [message["content"] for message in loaded["messages"][count:]] == expected["new_contents"]
    assert raw["metadata"]["frozen_system_prompt"] == original["metadata"]["frozen_system_prompt"]
    assert raw["metadata"]["frozen_custom_system_prompt"] == original["metadata"]["frozen_custom_system_prompt"]
    assert raw["metadata"]["custom"] == original["metadata"]["custom"]
    assert raw["metadata"]["total_messages"] == len(loaded["messages"])
    assert raw["metadata"]["total_tools"] == loaded["metadata"]["total_tools"] == 1
    assert raw["todo_list"] == original["todo_list"]
    assert raw["unknown_root"] == original["unknown_root"]
    assert loaded["token_statistics"]["unknown_field"] is True
    assert loaded["token_statistics"]["total_tokens"] == 20
    identities = [message["message_id"] for message in loaded["messages"]]
    assert len(identities) == len(set(identities))
    for message in loaded["messages"][count:]:
        assert message["message_id"].startswith("msg_")
        datetime.fromisoformat(message["timestamp"])
    datetime.fromisoformat(raw["updated_at"])

    assert manager.save_conversation(
        conversation_id,
        loaded["messages"] + [expected["python_message"]],
        todo_list=loaded["todo_list"],
    )
    assert manager.read_conversation(conversation_id)["messages"][-1] == expected["python_message"]


if __name__ == "__main__":
    main()
