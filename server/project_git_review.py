"""Launch a normal, isolated conversation for a fixed Git comparison."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import threading

from flask import session

from core.tool_loading import snapshot_overrides_from_prefs
from modules.aux_model_resolver import resolve_aux_model_profile
from modules.personalization_manager import load_personalization_config
from modules.project_git.reviews import read_reviews, record_review, review_prompt
from server.context.data import get_workspace_data
from server.runtime import RuntimeContext, TaskParams, principal_from_session_snapshot, runtime_service
from utils.conversation_manager import ConversationManager
from server.utils_common import debug_log

_LAUNCH_LOCK = threading.RLock()


def launch_review(username: str, workspace_id: str, repo_id: str, repo: Path,
                  compare: dict, identity) -> dict:
    data = get_workspace_data(username, workspace_id, identity=identity)
    with _LAUNCH_LOCK:
        if runtime_service.list_runs(username, workspace_id, status="active"):
            raise RuntimeError("该工作区有任务正在运行，请结束后再发起评审。")
        # Validate storage before accepting a task, so a broken review index cannot
        # turn a successful task launch into a misleading failure response.
        read_reviews(data.workspace.data_dir, workspace_id, repo_id)
        prefs = load_personalization_config(data.workspace.data_dir)
        model_key = session.get("model_key") or prefs.get("default_model")
        profile = resolve_aux_model_profile(model_key) if model_key else None
        if not profile:
            raise ValueError("当前模型不可用，请先在对话中选择可用模型。")
        if profile.get("api_protocol") in {"anthropic_messages", "google"}:
            raise ValueError("当前模型协议尚不支持，请选择支持的模型后发起评审。")
        run_mode = session.get("run_mode") or prefs.get("default_run_mode") or "thinking"
        if run_mode not in {"fast", "thinking"}:
            run_mode = "thinking"
        manager = ConversationManager(base_dir=str(data.workspace.data_dir),
                                      project_path=str(data.workspace.project_path))
        metadata = {**snapshot_overrides_from_prefs(prefs), "work_mode": "execute",
                    "permission_mode": "readonly", "execution_mode": "sandbox",
                    "multi_agent_mode": False, "skip_auto_title_generation": True,
                    "git_review": {"repo_id": repo_id, "repo_path": str(repo), **compare}}
        conversation_id = manager.create_conversation(
            project_path=str(data.workspace.project_path), model_key=model_key,
            run_mode=run_mode, thinking_mode=run_mode == "thinking", metadata_overrides=metadata)
        label = compare["commit"]["subject"] if compare["kind"] == "commit" else compare["head_ref"]
        title = f"Review · {repo.name} · {compare['head_sha'][:8]} · {label}"[:120]
        manager.update_conversation_title(conversation_id, title)
        principal = principal_from_session_snapshot(dict(session), workspace_id, username=username)
        principal = replace(principal, host_workspace_id=workspace_id if identity.host_mode else principal.host_workspace_id)
        ctx = RuntimeContext(principal=principal, params=TaskParams(
            conversation_id=conversation_id, message=review_prompt(repo, compare),
            model_key=model_key, run_mode=run_mode, thinking_mode=run_mode == "thinking",
            message_source="git_review"))
        try:
            record = runtime_service.create_task(ctx)
        except Exception:
            # The conversation stays available for retry; never hide a launch error.
            manager.update_conversation_metadata(conversation_id, {"git_review_launch_failed": True})
            raise
        row = {"conversation_id": conversation_id, "task_id": record.task_id,
               "workspace_id": workspace_id, "kind": compare["kind"], "title": title,
               "base_sha": compare["base_sha"], "head_sha": compare["head_sha"],
               "created_at": datetime.now().isoformat()}
        warning = None
        try:
            record_review(data.workspace.data_dir, workspace_id, repo_id, row)
        except Exception as exc:
            # Work is already running. Return its identity even if the secondary
            # association index failed, rather than inviting a duplicate launch.
            warning = "评审已启动，但关联记录保存失败，可在对话列表查看。"
            debug_log(f"[ProjectGit] review index write failed: {exc}")
        return {**row, "warning": warning}


def list_reviews(username: str, workspace_id: str, repo_id: str, identity) -> list[dict]:
    data = get_workspace_data(username, workspace_id, identity=identity)
    rows = read_reviews(data.workspace.data_dir, workspace_id, repo_id)
    result = []
    for row in rows:
        conversation = data.manager_for(row["conversation_id"]).read_conversation(row["conversation_id"])
        if conversation is None:
            continue
        task = runtime_service.get_task(username, row["task_id"])
        status = task.status if task and task.workspace_id == workspace_id else "saved"
        title = (conversation.get("metadata") or {}).get("title") or row.get("title") or ""
        result.append({**row, "status": status, "title": title})
    return result
