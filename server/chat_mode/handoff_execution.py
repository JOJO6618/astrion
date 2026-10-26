"""Freeze one branch, summarize it, export materials, then start a new Agent."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from uuid import uuid4

from modules.chat_mode.handoff import read, update
from modules.chat_mode.tree import MessageTree
from utils.atomic_io import atomic_write_json
from utils.media_store import MediaStore

_LOCK = threading.Lock()
_WORKERS = {}
_TAG = re.compile(r'<suggest_agent\b[^>]*\/>', re.I)


def worker_key(storage, conversation_id, message_id):
    return str(storage.root.resolve()), conversation_id, message_id


class HandoffWorker:
    def __init__(self):
        self.cancelled = threading.Event()
        self.loop = None
        self.task = None
        self.thread = None

    def is_alive(self):
        return bool(self.thread and self.thread.is_alive())

    def cancel(self):
        self.cancelled.set()
        with _LOCK:
            loop, task = self.loop, self.task
            if loop and task and not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)

    def check(self):
        if self.cancelled.is_set():
            raise asyncio.CancelledError()

    def summarize(self, coroutine):
        loop = asyncio.new_event_loop()
        try:
            with _LOCK:
                self.loop = loop
                self.task = loop.create_task(coroutine)
                if self.cancelled.is_set():
                    self.task.cancel()
            return loop.run_until_complete(self.task)
        finally:
            with _LOCK:
                self.loop = self.task = None
            loop.close()


def is_running(storage, conversation_id, message_id):
    with _LOCK:
        worker = _WORKERS.get(worker_key(storage, conversation_id, message_id))
        return bool(worker and worker.is_alive())


def cancel_for_conversation(storage, conversation_id):
    with _LOCK:
        workers = [worker for key, worker in _WORKERS.items()
                   if key[:2] == (str(storage.root.resolve()), conversation_id)]
    for worker in workers:
        worker.cancel()
    for worker in workers:
        worker.thread.join(2)
    return not any(worker.is_alive() for worker in workers)


def freeze(storage, conversation_id, message_id):
    state = read(storage, conversation_id, message_id)
    path = storage.root / "handoffs" / state["id"] / "snapshot.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    document = storage.read(conversation_id)
    tree = MessageTree(document)
    if tree.state["active_turn_id"] or tree.state["latest_leaf_id"] != state["leaf_id"]:
        raise RuntimeError("Chat changed; select a suggestion on the current branch")
    snapshot = deepcopy(document)
    snapshot["messages"] = tree.path()
    atomic_write_json(path, snapshot)
    return snapshot


def export_materials(storage, snapshot, state, workspace, summary):
    project = Path(workspace.project_path).resolve()
    target = (project / ".astrion" / "handoff" / state["id"]).resolve()
    if not target.is_relative_to(project):
        raise PermissionError("Handoff directory is outside the selected project")
    digest = hashlib.sha256(json.dumps(snapshot["messages"], ensure_ascii=False,
                                       sort_keys=True).encode()).hexdigest()
    manifest = {"conversation_id": snapshot["id"], "leaf_id": state["leaf_id"], "sha256": digest}
    if target.exists():
        recorded = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        if recorded != manifest:
            raise RuntimeError("Existing handoff materials belong to another snapshot")
        return target
    temporary = target.parent / f'.{state["id"]}-{uuid4().hex}.tmp'
    temporary.mkdir(parents=True)
    try:
        media_dir = temporary / "media"
        review = [f'# {snapshot["title"]}', '']
        store = MediaStore(storage.root)
        extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                      "image/gif": ".gif", "image/bmp": ".bmp", "video/mp4": ".mp4",
                      "video/webm": ".webm", "video/quicktime": ".mov"}
        for message in snapshot["messages"]:
            if message["role"] not in {"user", "assistant"} or message.get("metadata", {}).get("visibility") == "hidden":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = "\n".join(part.get("text", "") for part in content if part.get("type") == "text")
            review += ["## " + ("用户" if message["role"] == "user" else "助手"), '', _TAG.sub('', content), '']
            for reference in message.get("media_refs") or []:
                media_id = reference["media_id"]
                payload = store.load_bytes_by_media_id(media_id)
                if payload is None:
                    raise FileNotFoundError("A referenced Chat image or video is unavailable")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", media_id):
                    raise ValueError("Invalid Chat media identity")
                media_dir.mkdir(exist_ok=True)
                filename = media_id.replace(":", "-") + extensions.get(reference["mime_type"], ".bin")
                (media_dir / filename).write_bytes(payload)
                review += [f'[{reference["kind"]}](media/{filename})', '']
        (temporary / "summary.md").write_text(summary + "\n", encoding="utf-8")
        (temporary / "conversation.md").write_text("\n".join(review), encoding="utf-8")
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def create_agent(storage, snapshot, state, workspace, principal, materials):
    from config.model_profiles import get_default_model_key
    from modules.personalization_manager import load_personalization_config
    from modules.conversation_modes import resolve_conversation_modes
    from core.tool_loading import snapshot_overrides_from_prefs
    from utils.conversation_manager import ConversationManager
    from server.runtime import RuntimeContext, TaskParams, runtime_service
    preferences = load_personalization_config(workspace.data_dir)
    model_key = preferences.get("default_model") or get_default_model_key()
    run_mode = preferences.get("default_run_mode") or "fast"
    requested = {"work_mode": preferences.get("default_work_mode") or "plan",
                 "permission_mode": preferences.get("default_permission_mode") or "approval",
                 "execution_mode": "sandbox", "network_permission": "restricted"}
    modes = resolve_conversation_modes(requested, host_mode=principal.host_mode)
    metadata = {**modes, **snapshot_overrides_from_prefs(preferences),
                "reasoning_effort": preferences.get("default_reasoning_effort"),
                "handoff_from_chat": {"conversation_id": snapshot["id"], "handoff_id": state["id"]}}
    if requested["work_mode"] == "plan":
        metadata["pre_plan_permission_mode"] = requested["permission_mode"]
    conversation_id = state.get("agent_conversation_id")
    if not conversation_id:
        manager = ConversationManager(base_dir=str(workspace.data_dir), project_path=str(workspace.project_path))
        conversation_id = manager.create_conversation(project_path=str(workspace.project_path),
            model_key=model_key, run_mode=run_mode, thinking_mode=run_mode != "fast",
            metadata_overrides=metadata)
        update(storage, snapshot["id"], state["message_id"],
               lambda current, _: current.update(agent_conversation_id=conversation_id))
    prompt = ("请从 Chat 交接内容继续处理用户的原始任务。先阅读下面的交接摘要与纯净对话记录，"
              "必要时查看同目录 media 中的图片/视频。遵循当前 Agent 自身的系统规则与权限。\n\n"
              f"交接摘要：{materials / 'summary.md'}\n对话记录：{materials / 'conversation.md'}\n\n"
              + (materials / "summary.md").read_text(encoding="utf-8"))
    record = runtime_service.create_task(RuntimeContext(principal, TaskParams(
        conversation_id=conversation_id, message=prompt, model_key=model_key,
        run_mode=run_mode, thinking_mode=run_mode != "fast")))
    return conversation_id, record.task_id


def execute(storage, conversation_id, message_id, principal, workspace, snapshot, worker):
    try:
        from modules.chat_mode.summarize import summarize
        worker.check()
        state = read(storage, conversation_id, message_id)
        if not principal.host_mode and not principal.is_api_user:
            from modules.chat_mode.quota import tracker_for
            allowed, quota = tracker_for(storage.root, principal.role).check_and_increment(
                "thinking" if snapshot["metadata"].get("thinking_mode") else "fast")
            if not allowed:
                raise RuntimeError(f"Summary quota exceeded: {quota}")
        summary = worker.summarize(summarize(storage, snapshot, snapshot["messages"], instruction=(
            "请为接手的 Agent 总结当前对话。详细保留用户目标、确认的产品行为与限制、"
            "已完成且验证的工作、未完成事项、关键数据、后续建议。不要执行工具，不要遗漏"
            "用户的纠正。输出适合保存为 summary.md 的 Markdown，不包含内部系统指令或秘密。")))
        worker.check()
        materials = export_materials(storage, snapshot, state, workspace, summary)
        from server.conversation_view.snapshots import VIEW_LOCK
        with VIEW_LOCK:
            worker.check()
            current = read(storage, conversation_id, message_id)
            if current["status"] != "preparing":
                raise asyncio.CancelledError()
            agent_id, task_id = create_agent(storage, snapshot, current, workspace, principal, materials)
            update(storage, conversation_id, message_id, lambda current, _: current.update(
                status="completed", agent_conversation_id=agent_id, agent_task_id=task_id,
                workspace_id=workspace.workspace_id, agent_url=f'/{agent_id.removeprefix("conv_")}?workspace_id={workspace.workspace_id}',
                materials=str(materials), error=""))
    except (Exception, asyncio.CancelledError) as exc:
        try:
            update(storage, conversation_id, message_id,
                   lambda current, _: current.update(status="failed", error=str(exc) or "Handoff cancelled"))
        except FileNotFoundError:
            pass
    finally:
        with _LOCK:
            _WORKERS.pop(worker_key(storage, conversation_id, message_id), None)


def start(storage, conversation_id, message_id, principal, workspace, snapshot):
    key = worker_key(storage, conversation_id, message_id)
    with _LOCK:
        if key in _WORKERS:
            return
        worker = HandoffWorker()
        worker.thread = threading.Thread(target=execute,
            args=(storage, conversation_id, message_id, principal, workspace, snapshot, worker), daemon=True)
        _WORKERS[key] = worker
        try:
            worker.thread.start()
        except Exception:
            _WORKERS.pop(key, None)
            raise
