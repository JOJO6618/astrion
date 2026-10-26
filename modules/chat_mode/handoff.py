"""Persistent suggestion decisions; deadlines never depend on a browser timer."""
from copy import deepcopy
from html import unescape
import re
from threading import Lock, Timer
import time
from uuid import uuid4

from modules.chat_mode.tree import MessageTree

_TAG = re.compile(r'<suggest_agent\b[^>]*\/>', re.I)
_REASON = re.compile(r'\breason\s*=\s*([\"\'])(.*?)\1', re.I | re.S)
_LOCK = Lock()
_TIMERS = {}


def register(storage, conversation_id, principal, *, schedule=True):
    def mutate(document):
        tree = MessageTree(document)
        path = tree.path()
        message = next((item for item in reversed(path) if item["role"] == "assistant"), None)
        if not message or not isinstance(message.get("content"), str):
            return None
        match = _TAG.search(message["content"])
        if not match:
            return None
        entries = document["metadata"]["chat"].setdefault("handoffs", {})
        key = message["message_id"]
        if key not in entries:
            reason = _REASON.search(match.group())
            entries[key] = {"id": f"handoff_{uuid4().hex}", "message_id": key,
                            "status": "pending", "reason": unescape(reason.group(2))[:500] if reason else "",
                            "deadline": time.time() + 30, "leaf_id": tree.state["latest_leaf_id"]}
        return deepcopy(entries[key])
    _, state = storage.manager.mutate_conversation(conversation_id, mutate)
    if state and schedule:
        schedule_deadline(storage, conversation_id, principal, state)
    return state


def read(storage, conversation_id, message_id):
    document = storage.read(conversation_id)
    state = document["metadata"]["chat"].get("handoffs", {}).get(message_id)
    if state is None:
        raise FileNotFoundError("Agent suggestion unavailable")
    return deepcopy(state)


def update(storage, conversation_id, message_id, callback):
    def mutate(document):
        state = document["metadata"]["chat"].get("handoffs", {}).get(message_id)
        if state is None:
            raise FileNotFoundError("Agent suggestion unavailable")
        callback(state, document)
        return deepcopy(state)
    return storage.manager.mutate_conversation(conversation_id, mutate)[1]


def schedule_deadline(storage, conversation_id, principal, state):
    if state["status"] != "pending":
        return
    key = (str(storage.root.resolve()), conversation_id, state["id"])
    with _LOCK:
        if key in _TIMERS:
            return
        timer = Timer(max(0, state["deadline"] - time.time()), expire,
                      args=(storage, conversation_id, principal, state["message_id"], key))
        timer.daemon = True
        _TIMERS[key] = timer
        timer.start()


def cancel_deadlines(storage, conversation_id):
    prefix = (str(storage.root.resolve()), conversation_id)
    with _LOCK:
        for key in list(_TIMERS):
            if key[:2] == prefix:
                _TIMERS.pop(key).cancel()


def expire(storage, conversation_id, principal, message_id, key):
    retry = False
    try:
        decline(storage, conversation_id, principal, message_id, expired=True)
    except FileNotFoundError:
        pass
    except (RuntimeError, ValueError, OSError):
        retry = True
    finally:
        with _LOCK:
            _TIMERS.pop(key, None)
    if retry:
        try:
            current = read(storage, conversation_id, message_id)
            if current["status"] == "pending":
                with _LOCK:
                    if key not in _TIMERS:
                        timer = Timer(3, expire, args=(storage, conversation_id, principal, message_id, key))
                        timer.daemon = True
                        _TIMERS[key] = timer
                        timer.start()
        except FileNotFoundError:
            pass


def decline(storage, conversation_id, principal, message_id, *, expired=False):
    from server.runtime import RuntimeContext, TaskParams, runtime_service
    from server.conversation_view.snapshots import VIEW_LOCK
    with VIEW_LOCK:
        current = read(storage, conversation_id, message_id)
        if current["status"] in {"declined", "completed", "preparing", "superseded"}:
            return current
        if expired and (current["status"] != "pending" or time.time() < current["deadline"]):
            return current
        document = storage.read(conversation_id)
        tree = MessageTree(document)
        if tree.state["latest_leaf_id"] != current["leaf_id"] or tree.state["active_turn_id"]:
            return update(storage, conversation_id, message_id,
                          lambda state, _: state.update(status="superseded"))
        # The task operation is idempotent. Keep the decision retryable if
        # admission fails instead of acknowledging a user message never sent.
        record = runtime_service.create_task(RuntimeContext(principal, TaskParams(
            conversation_id=conversation_id,
            message="不切换到 Agent，继续在 Chat 中处理当前任务",
            chat_parent_id=current["leaf_id"], chat_operation_id=f'{current["id"]}:decline',
        )))
        return update(storage, conversation_id, message_id,
                      lambda state, _: state.update(status="declined", task_id=record.task_id))


def select(storage, conversation_id, message_id):
    def apply(state, document):
        if state["status"] in {"selecting", "failed"}:
            return
        if state["status"] != "pending":
            raise RuntimeError("Suggestion has already been decided")
        if document["metadata"]["chat"]["latest_leaf_id"] != state["leaf_id"]:
            raise RuntimeError("Chat has moved to another branch")
        if time.time() >= state["deadline"]:
            raise RuntimeError("Suggestion has expired")
        state["status"] = "selecting"
    return update(storage, conversation_id, message_id, apply)
