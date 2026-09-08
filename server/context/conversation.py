"""对话加载保障与对话级覆盖（custom prompt / personalization）应用。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from server.context._flask_bridge import has_request_context, session_set

from core.web_terminal import WebTerminal
from modules.i18n import tr
from server.utils_common import debug_log
from utils.host_workspace_debug import write_host_workspace_debug


def ensure_conversation_loaded(
    terminal: WebTerminal,
    conversation_id: Optional[str],
    workspace=None,
    update_session: bool = True,
):
    """确保对话加载到 terminal。

    update_session=False（任务线程等非请求上下文路径）时跳过 session 回写；
    回写本是「刷新用户会话」的适配层语义，执行链路不应产生该副作用。
    """
    created_new = False
    if not conversation_id:
        result = terminal.create_new_conversation()
        if not result.get("success"):
            raise RuntimeError(result.get("message", tr("context.create_conversation_failed")))
        conversation_id = result["conversation_id"]
        if update_session and has_request_context():
            session_set('run_mode', terminal.run_mode)
            session_set('thinking_mode', terminal.thinking_mode)
        created_new = True
    else:
        conversation_id = conversation_id if conversation_id.startswith('conv_') else f"conv_{conversation_id}"
        current_id = terminal.context_manager.current_conversation_id
        if current_id != conversation_id:
            load_result = terminal.load_conversation(conversation_id)
            if not load_result.get("success"):
                raise RuntimeError(load_result.get("message", tr("context.load_conversation_failed")))
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.after_load",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                terminal_project_path=str(getattr(terminal, "project_path", "")),
                context_project_path=str(getattr(getattr(terminal, "context_manager", None), "project_path", "")),
                metadata_project_path=(
                    getattr(getattr(terminal, "context_manager", None), "conversation_metadata", {}) or {}
                ).get("project_path"),
            )
            try:
                conv_data = terminal.context_manager._get_conversation_manager_for_id(conversation_id).load_conversation(conversation_id) or {}
                meta = conv_data.get("metadata", {}) or {}
                run_mode_meta = meta.get("run_mode")
                if run_mode_meta:
                    terminal.set_run_mode(run_mode_meta)
                elif meta.get("thinking_mode"):
                    terminal.set_run_mode("thinking")
                else:
                    terminal.set_run_mode("fast")
                try:
                    terminal.set_reasoning_effort(meta.get("reasoning_effort"))
                except (ValueError, AttributeError):
                    pass
                if update_session and has_request_context():
                    session_set('run_mode', terminal.run_mode)
                    session_set('thinking_mode', terminal.thinking_mode)
                    session_set('model_key', getattr(terminal, "model_key", None))
            except Exception:
                pass
    if workspace is not None:
        try:
            workspace_project_path = str(Path(workspace.project_path).expanduser().resolve())
            terminal.update_project_path(workspace_project_path)
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.reapply_workspace_path",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                workspace_project_path=workspace_project_path,
                terminal_project_path=str(getattr(terminal, "project_path", "")),
                context_project_path=str(getattr(getattr(terminal, "context_manager", None), "project_path", "")),
            )
        except Exception as exc:
            write_host_workspace_debug(
                "context.ensure_conversation_loaded.reapply_workspace_path_failed",
                terminal_id=id(terminal),
                conversation_id=conversation_id,
                error=str(exc),
            )
    # 应用对话级自定义 prompt / personalization（仅 API）。
    # 注意：ensure_conversation_loaded 在 WebSocket/后台任务等多处复用，有些调用点拿不到 workspace；
    #       因此这里允许 workspace 为空（仅跳过 override，不影响正常对话加载）。
    if workspace is not None:
        try:
            apply_conversation_overrides(terminal, workspace, conversation_id)
        except Exception as exc:
            debug_log(f"[apply_overrides] 失败: {exc}")
    return conversation_id, created_new


def apply_conversation_overrides(terminal: WebTerminal, workspace, conversation_id: Optional[str]):
    """根据对话元数据应用自定义 prompt / personalization（仅 API 用途）。"""
    if not conversation_id:
        return
    conv_path = Path(workspace.data_dir) / "conversations" / f"{conversation_id}.json"
    if not conv_path.exists():
        return
    try:
        data = json.loads(conv_path.read_text(encoding="utf-8"))
        meta = data.get("metadata") or {}
        prompt_name = meta.get("custom_prompt_name")
        personalization_name = meta.get("personalization_name")
        # 安全：元数据中的名称必须过资源名校验，防存储型路径穿越
        import re as _re
        def _safe_name(v):
            v = (v or "").strip()
            return v if _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v) else None
        prompt_name = _safe_name(prompt_name)
        personalization_name = _safe_name(personalization_name)
        # prompt override
        if prompt_name:
            prompt_path = Path(workspace.data_dir) / "prompts" / f"{prompt_name}.txt"
            if prompt_path.exists():
                terminal.context_manager.custom_system_prompt = prompt_path.read_text(encoding="utf-8")
            else:
                terminal.context_manager.custom_system_prompt = None
        else:
            terminal.context_manager.custom_system_prompt = None
        # personalization override
        if personalization_name:
            pers_path = Path(workspace.data_dir) / "personalization" / f"{personalization_name}.json"
            if pers_path.exists():
                try:
                    terminal.context_manager.custom_personalization_config = json.loads(pers_path.read_text(encoding="utf-8"))
                except Exception:
                    terminal.context_manager.custom_personalization_config = None
            else:
                terminal.context_manager.custom_personalization_config = None
        else:
            terminal.context_manager.custom_personalization_config = None

        # 应用个性化偏好（含禁用工具分类）到当前终端；
        # 对话加载链路不应用默认 模型/模式/推理强度（以对话 meta 为权威）
        try:
            terminal.apply_personalization_preferences(
                terminal.context_manager.custom_personalization_config,
                apply_default_modes=False,
            )
        except Exception as exc:
            debug_log(f"[apply_overrides] 应用个性化失败: {exc}")
    except Exception as exc:
        debug_log(f"[apply_overrides] 读取对话元数据失败: {exc}")
