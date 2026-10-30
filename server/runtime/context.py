"""运行时上下文模型（RuntimeContext 三层分离）。

契约见 docs/runtime_contract.md §4.1：

- ``TrustedPrincipal``：可信身份与资源范围。只能由适配层在完成认证后构造；
  禁止接受客户端或未来 Schedule payload 自报的 role/is_api_user。
- ``TaskParams``：本次任务参数（消息、媒体、模型/模式覆盖等）。
- ``InternalDirectives``：内部执行信息（门闸 token、通知回放等），
  仅内部调用方（通知链/工作流/派发器）使用，普通客户端不可提交。

默认值解析优先级：本次显式传参（TaskParams）> 对话元数据绑定 >
用户偏好快照（TrustedPrincipal.preferred_*）> 系统默认。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TrustedPrincipal:
    """可信身份与资源范围（适配层认证后构造）。

    preferred_* 是「用户偏好快照」：资源装配（get_user_resources）新建 terminal 时
    的默认值来源；与 TaskParams 中的「本次覆盖」字段语义不同，不要混用。
    """

    username: str
    workspace_id: Optional[str]
    role: str = "user"
    is_api_user: bool = False
    host_mode: bool = False
    host_workspace_id: Optional[str] = None
    preferred_model_key: Optional[str] = None
    preferred_run_mode: Optional[str] = None
    preferred_thinking_mode: Optional[bool] = None
    conversation_kind: str = "agent"

    def validate(self) -> None:
        if not str(self.username or "").strip():
            raise ValueError("runtime_context: username 不能为空")
        if self.conversation_kind not in {"agent", "chat"}:
            raise ValueError("Invalid conversation kind")
        if self.conversation_kind == "chat":
            if self.workspace_id is not None:
                raise ValueError("Chat has no workspace identity")
        elif not str(self.workspace_id or "").strip():
            raise ValueError("runtime_context: workspace_id 不能为空")


@dataclass(frozen=True)
class TaskParams:
    """本次任务参数（优先级最高的覆盖层）。"""

    message: str = ""
    images: List[Any] = field(default_factory=list)
    videos: List[Any] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    conversation_id: Optional[str] = None
    model_key: Optional[str] = None
    run_mode: Optional[str] = None
    thinking_mode: Optional[bool] = None
    max_iterations: Optional[int] = None
    goal_mode: bool = False
    goal_action: Optional[str] = None
    goal_options: Optional[Dict[str, Any]] = None
    skill_context_messages: List[Dict[str, str]] = field(default_factory=list)
    message_source: Optional[str] = None
    queued_message_id: Optional[str] = None
    task_type: str = "chat"
    chat_operation_id: Optional[str] = None
    chat_parent_id: Optional[str] = None
    chat_regenerate_turn_id: Optional[str] = None
    chat_edit_message_id: Optional[str] = None
    chat_presentation_id: Optional[str] = None
    chat_settings: Dict[str, Any] = field(default_factory=dict)
    # 审批/提问等待超时透传（秒）。None = 保持既有默认语义（3600s）不变；
    # 超时后的语义（拒绝当前动作继续 vs 结束任务）属阶段三产品决策，
    # 本阶段仅建立透传机制，适配层暂不向 Web 客户端暴露该字段。
    approval_timeout_seconds: Optional[int] = None

    def validate(self) -> None:
        if self.chat_presentation_id is not None and (
                not isinstance(self.chat_presentation_id, str) or not 1 <= len(self.chat_presentation_id) <= 128):
            raise ValueError("Invalid Chat presentation identity")
        if not isinstance(self.chat_settings, dict) or set(self.chat_settings) - {"reasoning_effort", "network_enabled", "chatgpt_fast_mode"}:
            raise ValueError("Invalid Chat settings")
        if "chatgpt_fast_mode" in self.chat_settings:
            from utils.api_client.codex.service_tier import validate_fast_mode
            validate_fast_mode(self.chat_settings["chatgpt_fast_mode"])
        effort = self.chat_settings.get("reasoning_effort")
        if effort is not None and (not isinstance(effort, str) or effort not in {"minimal", "low", "medium", "high", "xhigh"}):
            raise ValueError("Invalid Chat reasoning effort")
        if "network_enabled" in self.chat_settings and not isinstance(self.chat_settings["network_enabled"], bool):
            raise ValueError("Chat network setting must be a boolean")
        if self.goal_action not in {None, "start", "continue"}:
            raise ValueError("Invalid goal action")
        from modules.goal_policy import validate_limits
        validate_limits(self.goal_options)
        if self.run_mode is not None:
            normalized = str(self.run_mode).lower()
            if normalized not in {"fast", "thinking", "deep"}:
                raise ValueError("runtime_context: run_mode 只支持 fast/thinking/deep")


@dataclass(frozen=True)
class InternalDirectives:
    """内部执行信息。仅内部调用方使用；HTTP 适配层禁止从请求体构造本结构。"""

    # 派发方预占的主任务门闸 token，随任务移交由任务线程认领
    main_task_gate_token: Optional[str] = None
    # 通知类任务：把 user 消息写入任务事件流，保证轮询客户端/刷新后可见
    auto_user_message_event: bool = False
    auto_user_message_payload: Optional[Dict[str, Any]] = None
    # 本批通知池中除触发消息外的前置通知，随任务事件流回放
    preceding_user_notices: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class RuntimeContext:
    """一轮 Run 的完整显式上下文：可信身份 + 任务参数 + 内部指令。"""

    principal: TrustedPrincipal
    params: TaskParams
    directives: InternalDirectives = field(default_factory=InternalDirectives)

    def validate(self) -> None:
        self.principal.validate()
        self.params.validate()

    @classmethod
    def from_terminal(
        cls,
        terminal: Any,
        workspace: Any,
        username: str,
        params: TaskParams,
        directives: Optional[InternalDirectives] = None,
    ) -> "RuntimeContext":
        """从对话级 terminal/工作区构造（通知链、多智能体派发等内部调用方）。

        host_mode 取 workspace.username == "host"；偏好快照取 terminal 当前值。
        """
        workspace_id = getattr(workspace, "workspace_id", None) or "default"
        host_mode = bool(getattr(workspace, "username", None) == "host")
        role = getattr(terminal, "user_role", None) or "user"
        return cls(
            principal=TrustedPrincipal(
                username=username,
                workspace_id=workspace_id,
                role=role,
                is_api_user=(role == "api"),
                host_mode=host_mode,
                host_workspace_id=workspace_id if host_mode else None,
                preferred_model_key=getattr(terminal, "model_key", None),
                preferred_run_mode=getattr(terminal, "run_mode", None),
                preferred_thinking_mode=getattr(terminal, "thinking_mode", None),
            ),
            params=params,
            directives=directives or InternalDirectives(),
        )



def principal_from_session_snapshot(
    session_snapshot: Dict[str, Any],
    workspace_id: str,
    username: Optional[str] = None,
) -> TrustedPrincipal:
    """适配层工具：从 Flask session（或其 dict 快照）构造可信 principal。

    调用方必须在完成认证后调用；``session_snapshot`` 由适配层显式传入
    （本模块不 import flask，保持服务层可测试、无隐式上下文依赖）。
    """
    snap = session_snapshot or {}
    resolved_workspace = str(workspace_id or snap.get("workspace_id") or "default")
    host_mode = bool(snap.get("host_mode"))
    return TrustedPrincipal(
        username=str(username or snap.get("username") or ""),
        workspace_id=resolved_workspace,
        role=str(snap.get("role") or "user"),
        is_api_user=bool(snap.get("is_api_user")),
        host_mode=host_mode,
        host_workspace_id=str(snap.get("host_workspace_id") or (resolved_workspace if host_mode else "") or "") or None,
        preferred_model_key=snap.get("model_key"),
        preferred_run_mode=snap.get("run_mode"),
        preferred_thinking_mode=snap.get("thinking_mode"),
    )
