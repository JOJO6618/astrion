"""工作区个性化偏好应用（模型/模式默认值恢复与 session 回写）。"""
from __future__ import annotations

from typing import Optional

from flask import session, has_request_context

from core.web_terminal import WebTerminal
from modules.personalization_manager import load_personalization_config
from modules.i18n import tr  # noqa: F401  # 保持与原模块一致的可用导入
from server.utils_common import debug_log


def _apply_workspace_personalization_preferences(
    terminal: WebTerminal,
    workspace,
    update_session: bool = True,
    session_model: Optional[str] = None,
    allow_session_io: bool = True,
) -> None:
    """Apply persisted workspace personalization after policy/workspace resolution.

    session_model：显式身份模式（任务线程）传入的偏好模型快照，优先于 session 读取；
    allow_session_io=False 时完全不读写 Flask session（无请求上下文场景）。
    """
    try:
        config = load_personalization_config(workspace.data_dir)
        resolved_session_model = None
        if session_model is not None:
            if isinstance(session_model, str) and session_model.strip():
                resolved_session_model = session_model.strip()
        elif allow_session_io and has_request_context():
            raw_session_model = session.get("model_key")
            if isinstance(raw_session_model, str) and raw_session_model.strip():
                resolved_session_model = raw_session_model.strip()

        # 对话级 terminal（_bound_conversation_id）的模型由绑定加载权威恢复
        # （对话文件 metadata.model_key），session 级模型是全局的最后选择，
        # 不能覆盖到某个具体对话的 terminal 上，否则重启后进入对话会被
        # session 里的其它模型回写（模型回变默认的 bug）。
        is_conversation_bound = bool(getattr(terminal, "_bound_conversation_id", None))

        # default_model 是“新会话初始偏好”，不能在每次 /api/status、任务创建、
        # 加载资源时覆盖用户已经在当前会话里手动切换的模型。
        if (
            resolved_session_model
            and not is_conversation_bound
            and resolved_session_model != getattr(terminal, "model_key", None)
        ):
            try:
                terminal.set_model(resolved_session_model)
            except Exception as exc:
                debug_log(f"[Personalization] 恢复会话模型失败: {resolved_session_model} ({exc})")

        apply_default_model = (
            not is_conversation_bound
            and not bool(resolved_session_model)
            and not bool(getattr(terminal, "_workspace_default_model_applied", False))
        )
        # 对话级 terminal 的 模型/思考模式/推理强度 以对话 meta 为权威，
        # 此函数在每次 /api/status、任务创建、加载资源时都会触发，
        # 不得在对话加载后反复用 prefs 默认值覆盖 meta 恢复值。
        # 工作区级 terminal（/new 页）同理：三项 modes 默认值仅首次应用一次，
        # 之后用户在 /new 手动调整的 模式/档位 必须稳定存活到创建对话时，
        # 不能被 status 轮询反复重置（prefs 更新走 settings 保存路径显式应用，
        # 新建空对话走 create_new_conversation 的 prefer_defaults 路径重置）。
        apply_default_modes = (
            not is_conversation_bound
            and not bool(getattr(terminal, "_workspace_default_modes_applied", False))
        )
        terminal.apply_personalization_preferences(
            config,
            apply_default_model=apply_default_model,
            apply_default_modes=apply_default_modes,
        )
        if apply_default_model:
            try:
                terminal._workspace_default_model_applied = True
            except Exception:
                pass
        if apply_default_modes:
            try:
                terminal._workspace_default_modes_applied = True
            except Exception:
                pass
        if allow_session_io and has_request_context() and update_session:
            session["run_mode"] = getattr(terminal, "run_mode", session.get("run_mode"))
            session["thinking_mode"] = getattr(terminal, "thinking_mode", session.get("thinking_mode"))
            session["model_key"] = getattr(terminal, "model_key", session.get("model_key"))
    except Exception as exc:
        debug_log(f"[Personalization] 应用工作区偏好失败: {exc}")
