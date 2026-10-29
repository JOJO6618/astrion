"""Linear context port backed by the authoritative Chat tree.

The existing model/tool loop sees the same ContextManager message interface.
Only this port understands branch ownership; it never saves a linear projection.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from utils.context_manager import ContextManager
from utils.responses_reasoning import same_responses_provider
from modules.chat_mode.storage import ChatStorage
from modules.chat_mode.tree import MessageTree, TreeConflict, identifier


class ChatContext(ContextManager):
    def __init__(self, storage: ChatStorage, conversation_id: str, turn_id: str):
        super().__init__(None, str(storage.root), conversation_kind="chat")
        self.storage = storage
        self.conversation_manager = storage.manager
        self.current_conversation_id = conversation_id
        self.turn_id = turn_id
        self._prepared_input_pending = True
        self._baseline = {}
        self.refresh()

    def _get_conversation_manager_for_id(self, conversation_id):
        if conversation_id != self.current_conversation_id:
            raise ValueError("Chat context cannot switch conversation identity")
        return self.conversation_manager

    def refresh(self):
        document = self.storage.read(self.current_conversation_id)
        tree = MessageTree(document)
        turn = tree.turn(self.turn_id)
        self.conversation_history = tree.path(turn["tip_id"] or turn["parent_id"], latest=False)
        self.conversation_metadata = document["metadata"]
        self.has_images = bool(document["metadata"].get("has_images"))
        self.has_videos = bool(document["metadata"].get("has_videos"))
        self._baseline = {m["message_id"]: deepcopy(m) for m in self.conversation_history}

    def add_conversation(self, role, content, **kwargs):
        if self._prepared_input_pending and role == "user":
            # Acceptance already committed the new user or selected the original
            # user for regeneration. The shared loop must not append it twice.
            self._prepared_input_pending = False
            parent = MessageTree(self.storage.read(self.current_conversation_id)).turn(self.turn_id)["parent_id"]
            message = next(m for m in self.conversation_history if m["message_id"] == parent)
            message.setdefault("metadata", {}).update(kwargs.get("metadata") or {})
            self.auto_save_conversation(force=True)
            return message
        return super().add_conversation(role, content, **kwargs)

    def auto_save_conversation(self, force=False):
        if not self.auto_save_enabled:
            return
        for message in self.conversation_history:
            mid = message["message_id"]
            previous = self._baseline.get(mid)
            if previous is None:
                stored = self.storage.append(self.current_conversation_id, self.turn_id, message)
                message.setdefault("metadata", {}).update(stored.get("metadata") or {})
                self._baseline[mid] = deepcopy(message)
                continue
            # Work timer/speed are loop-owned metadata. An older in-memory body
            # must never overwrite a concurrent user edit of this or an ancestor.
            metadata = message.get("metadata", {})
            old_metadata = previous.get("metadata", {})
            changes = {key: deepcopy(metadata[key]) for key in ("work_timer", "token_speed")
                       if key in metadata and metadata.get(key) != old_metadata.get(key)}
            if changes:
                def update(document):
                    node = MessageTree(document).node(mid)
                    node.setdefault("metadata", {}).update(changes)
                self.conversation_manager.mutate_conversation(self.current_conversation_id, update)
                self._baseline[mid] = deepcopy(message)

    def save_current_conversation(self):
        self.auto_save_conversation(force=True)
        return True

    def consume_network_notices(self):
        def apply(document):
            tree = MessageTree(document)
            if tree.state["active_turn_id"] != self.turn_id:
                raise TreeConflict("Chat turn no longer owns the context")
            pending = document["metadata"].get("pending_network_notices", [])
            for notice in pending:
                state = "开启" if notice["enabled"] else "关闭"
                tree.append_to_turn(self.turn_id, {
                    "message_id": identifier(), "role": "user",
                    "content": f"[Chat 联网设置更新] 联网已{state}。请按当前联网状态继续。",
                    "timestamp": datetime.now().isoformat(),
                    "metadata": {"visibility": "hidden", "hidden": True,
                                 "starts_work": False, "message_source": "network",
                                 "network_revision": notice["revision"]},
                })
            document["metadata"]["pending_network_notices"] = []
        self.conversation_manager.mutate_conversation(self.current_conversation_id, apply)
        self.refresh()

    def on_tool_call_finished(self, tool_name, **kwargs):
        # Chat summaries operate on a branch at the next complete request
        # boundary, not on workspace tool files or shallow version checkpoints.
        return 0

    def is_compression_in_progress(self):
        return bool(self.conversation_metadata.get("compression_in_progress"))

    def _current_model_key(self) -> str:
        """当前请求的模型 key（Chat 运行时可中途换同协议模型）。

        优先用运行时（Chat 页能直接在对话内切模型），回退到对话元数据，
        用于 Responses 加密 reasoning 的「同上游连接」判定。
        """
        key = getattr(getattr(self, "main_terminal", None), "model_key", None)
        if not key:
            key = (self.conversation_metadata or {}).get("model_key")
        return str(key or "")

    def build_api_history(self):
        result = []
        current_model_key = self._current_model_key()
        for stored in self.conversation_history:
            message = {"role": stored["role"], "content": stored.get("content", "")}
            if stored.get("media_refs") or stored.get("images") or stored.get("videos"):
                message["content"] = self._build_content_with_images(
                    stored.get("content", ""), stored.get("images") or [],
                    stored.get("videos") or [], media_refs=stored.get("media_refs") or [],
                )
            for key in ("tool_calls", "tool_call_id", "name", "reasoning_content"):
                if key in stored:
                    message[key] = deepcopy(stored[key])
            # Chat 历史与 Agent 同规：只有「同上游连接」才回插 Responses 加密
            # reasoning items（Chat 允许同协议换账号/换连接，加密块不可跨账号回放）。
            metadata = stored.get("metadata") or {}
            items = metadata.get("responses_reasoning_items")
            if items and same_responses_provider(
                str(metadata.get("model_key") or ""), current_model_key
            ):
                message["responses_reasoning_items"] = deepcopy(items)
            result.append(message)
        return result
