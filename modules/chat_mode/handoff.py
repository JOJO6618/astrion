"""Persistent decisions; only a live first presentation may auto-continue."""
from copy import deepcopy
from threading import Lock, Timer
import time
from uuid import uuid4

from modules.chat_mode.tree import MessageTree
from modules.chat_mode.handoff_records import ACTIONABLE, TERMINAL, pause, resolve, sync

_LOCK = Lock()
_TIMERS = {}
_LEASES = {}
_RUNTIME_ID = uuid4().hex


def register(storage, conversation_id, principal, *, schedule=True, turn_id=None, presentation_id=None):
    def mutate(document):
        tree = MessageTree(document)
        fresh = {m["message_id"] for m in tree.path() if m["role"] == "assistant"
                 and (turn_id is None or m.get("metadata", {}).get("chat", {}).get("turn_id") == turn_id)}
        owner = presentation_id if presentation_id == tree.state.get("handoff_presentation_id") else None
        entries = sync(document, fresh_ids=fresh, presentation_id=owner)
        states = [s for s in entries.values() if s["message_id"] in fresh and s.get("present")]
        for state in states:
            state.setdefault("runtime_id", _RUNTIME_ID)
        return deepcopy(states)
    _, states = storage.manager.mutate_conversation(conversation_id, mutate)
    # A browser must observe the fresh label before any timer is armed.
    return states[-1] if states else None


def read(storage, conversation_id, identity):
    document = storage.read(conversation_id)
    return deepcopy(resolve(document["metadata"]["chat"].get("handoffs", {}), identity))


def update(storage, conversation_id, identity, callback):
    def mutate(document):
        state = resolve(document["metadata"]["chat"].get("handoffs", {}), identity)
        callback(state, document)
        return deepcopy(state)
    return storage.manager.mutate_conversation(conversation_id, mutate)[1]


def presentation(storage, conversation_id, identity, *, release=False):
    if not isinstance(identity, str) or not 1 <= len(identity) <= 128:
        raise ValueError("Invalid Chat presentation identity")
    def mutate(document):
        chat = document["metadata"]["chat"]
        if release and chat.get("handoff_presentation_id") != identity:
            return False
        for state in sync(document).values():
            pause(state)
        chat["handoff_presentation_id"] = None if release else identity
        return True
    _, changed = storage.manager.mutate_conversation(conversation_id, mutate)
    if changed:
        cancel_deadlines(storage, conversation_id)


def timer_key(storage, conversation_id, state):
    return str(storage.root.resolve()), conversation_id, state["id"]


def observe(storage, conversation_id, principal, identity, presentation_id, *, suspend=False):
    current = read(storage, conversation_id, identity)
    key = timer_key(storage, conversation_id, current)
    with _LOCK:
        lease = _LEASES.get(key)
    last_seen = lease[0] if lease else None
    owner = storage.read(conversation_id)["metadata"]["chat"].get("handoff_presentation_id")
    if (current.get("countdown_enabled") and current.get("presentation_id") == presentation_id
            and (suspend or current.get("runtime_id") != _RUNTIME_ID or owner != presentation_id
                 or (last_seen is not None and time.monotonic() - last_seen > 3)
                 or (last_seen is None and time.time() >= (current.get("deadline") or 0)))):
        current = update(storage, conversation_id, identity, lambda state, _: pause(state))
    if (current["status"] == "pending" and current.get("countdown_enabled")
            and current.get("presentation_id") == presentation_id):
        with _LOCK:
            _LEASES[key] = (time.monotonic(), time.time())
        schedule_deadline(storage, conversation_id, principal, current)
    return current


def schedule_deadline(storage, conversation_id, principal, state):
    if state["status"] != "pending" or not state.get("countdown_enabled") or not state.get("deadline"):
        return
    key = timer_key(storage, conversation_id, state)
    with _LOCK:
        if key in _TIMERS:
            return
        timer = Timer(max(0, state["deadline"] - time.time()), expire,
                      args=(storage, conversation_id, principal, state["id"], key))
        timer.daemon = True
        _TIMERS[key] = timer
        timer.start()


def cancel_deadlines(storage, conversation_id):
    prefix = (str(storage.root.resolve()), conversation_id)
    with _LOCK:
        for key in list(_TIMERS):
            if key[:2] == prefix:
                _TIMERS.pop(key).cancel()
        for key in list(_LEASES):
            if key[:2] == prefix:
                _LEASES.pop(key, None)


def expire(storage, conversation_id, principal, identity, key):
    retry = False
    try:
        decline(storage, conversation_id, principal, identity, expired=True)
    except FileNotFoundError:
        pass
    except (RuntimeError, ValueError, OSError):
        retry = True
    finally:
        with _LOCK:
            _TIMERS.pop(key, None)
    if retry:
        try:
            current = read(storage, conversation_id, identity)
            if current["status"] == "pending" and current.get("countdown_enabled"):
                with _LOCK:
                    timer = Timer(3, expire, args=(storage, conversation_id, principal, identity, key))
                    timer.daemon = True
                    _TIMERS[key] = timer
                    timer.start()
        except FileNotFoundError:
            pass


def decline(storage, conversation_id, principal, identity, *, expired=False):
    from server.runtime import RuntimeContext, TaskParams, runtime_service
    from server.conversation_view.snapshots import VIEW_LOCK
    with VIEW_LOCK:
        current = read(storage, conversation_id, identity)
        if current["status"] in TERMINAL | {"preparing"}:
            return current
        if expired:
            if (current["status"] != "pending" or not current.get("countdown_enabled")
                    or time.time() < (current.get("deadline") or float("inf"))):
                return current
            with _LOCK:
                last_seen, observed_at = _LEASES.get(timer_key(storage, conversation_id, current), (0, 0))
            chat = storage.read(conversation_id)["metadata"]["chat"]
            if (current.get("runtime_id") != _RUNTIME_ID or time.monotonic() - last_seen > 3
                    or chat.get("handoff_presentation_id") != current.get("presentation_id")):
                return update(storage, conversation_id, identity, lambda state, _: pause(state))
            # A heartbeat before the deadline cannot authorize an action after
            # the page disappears. A live post-deadline observation re-arms the
            # zero-delay timer and performs the same atomic decision.
            if observed_at < current["deadline"]:
                return current
        document = storage.read(conversation_id)
        tree = MessageTree(document)
        if tree.state["latest_leaf_id"] != current["leaf_id"] or tree.state["active_turn_id"]:
            return update(storage, conversation_id, identity,
                          lambda state, _: state.update(status="superseded", countdown_enabled=False,
                                                        deadline=None, decided_at=time.time()))
        record = runtime_service.create_task(RuntimeContext(principal, TaskParams(
            conversation_id=conversation_id,
            message="不切换到 Agent，继续在 Chat 中处理当前任务",
            chat_parent_id=current["leaf_id"], chat_operation_id=f'{current["id"]}:decline',
        )))
        return update(storage, conversation_id, identity,
                      lambda state, _: state.update(status="declined", task_id=record.task_id,
                                                    decided_at=time.time(), countdown_enabled=False,
                                                    deadline=None,
                                                    decision_source="timeout" if expired else "button"))


def select(storage, conversation_id, identity):
    def apply(state, document):
        if state["status"] not in ACTIONABLE or state.get("present") is False:
            raise RuntimeError("Suggestion has already been decided or removed")
        chat = document["metadata"]["chat"]
        if chat["latest_leaf_id"] != state["leaf_id"] or chat["active_turn_id"]:
            raise RuntimeError("Chat has moved to another branch")
        if any(s["status"] == "preparing" and s["id"] != state["id"]
               for s in chat.get("handoffs", {}).values()):
            raise RuntimeError("Another suggestion is already switching to Agent")
        state["status"] = "selecting"
        pause(state)
    return update(storage, conversation_id, identity, apply)
