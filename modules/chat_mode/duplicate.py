"""Copy a complete idle Chat tree without copying its background operations."""
from copy import deepcopy
import hashlib

from modules.chat_mode.tree import MessageTree, TreeConflict


def duplicate(storage, conversation_id, operation_id):
    with storage.manager._io_lock:
        source = storage.read(conversation_id)
        if source["metadata"]["chat"].get("active_turn_id"):
            raise TreeConflict("Copy the conversation after its current reply finishes")
        key = "copy_" + hashlib.sha256(
            f"{conversation_id}:{operation_id}".encode("utf-8")
        ).hexdigest()
        metadata = source["metadata"]
        new_id = storage.create_for_operation(
            key, model_key=metadata.get("model_key"),
            thinking_mode=metadata.get("thinking_mode", False),
            reasoning_effort=metadata.get("reasoning_effort"),
            network_enabled=metadata.get("network_enabled", True),
        )

        def populate(document):
            if document["metadata"]["chat"].get("copied_from") == conversation_id:
                return
            document["messages"] = deepcopy(source["messages"])
            document["metadata"] = deepcopy(metadata)
            state = document["metadata"]["chat"]
            state.update(creation_operation_id=key, copied_from=conversation_id,
                         commands={}, handoffs={})
            document["metadata"]["pending_network_notices"] = []
            document["title"] = source.get("title", "Chat") + " (copy)"
            MessageTree(document).validate()

        storage.manager.mutate_conversation(new_id, populate)
        return new_id
