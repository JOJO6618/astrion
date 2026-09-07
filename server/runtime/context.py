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
    workspace_id: str
    role: str = "user"
    is_api_user: bool = False
    host_mode: bool = False
    host_workspace_id: Optional[str] = None
    preferred_model_key: Optional[str] = None
    preferred_run_mode: Optional[str] = None
    preferred_thinking_mode: Optional[bool] = None

    def validate(self) -> None:
        if not str(self.username or "").strip():
            raise ValueError("runtime_context: username 不能为空")
        if not str(self.workspace_id or "").strip():
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
    skill_context_messages: List[Dict[str, str]] = field(default_factory=list)
    message_source: Optional[str] = None
    task_type: str = "chat"
    # 审批/提问等待超时透传（秒）。None = 保持既有默认语义（3600s）不变；
    # 超时后的语义（拒绝当前动作继续 vs 结束任务）属阶段三产品决策，
    # 本阶段仅建立透传机制，适配层暂不向 Web 客户端暴露该字段。
    approval_timeout_seconds: Optional[int] = None

    def validate(self) -> None:
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

        与既有 session_data 手工构造逐字段对齐：
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

    def to_session_data(self) -> Dict[str, Any]:
        """兼容转换：合并为现有 ``create_chat_task`` 的 session_data 快照 dict。

        快照在受理时固化、随任务线程传递：身份/偏好由 ``_run_chat_task`` 还原为
        ``RuntimeIdentity`` 驱动资源装配（无 Flask 隐式上下文）；门闸移交
        （main_task_gate_token）、事件注入（auto_user_message_*）、terminal 属性
        设置（message_source/goal_mode/skill_context_messages）语义不变。

        其中 run_mode/thinking_mode/model_key 取「用户偏好快照」层
        （principal.preferred_*），供资源装配新建 terminal 时恢复默认值；
        本次覆盖值（params.*）由 RuntimeService.create_task 走显式参数传递，
        不进入本快照。
        """
        p = self.principal
        session_data: Dict[str, Any] = {
            "username": p.username,
            "role": p.role,
            "is_api_user": p.is_api_user,
            "host_mode": p.host_mode,
            "host_workspace_id": p.host_workspace_id or (p.workspace_id if p.host_mode else None),
            "workspace_id": p.workspace_id,
            "run_mode": p.preferred_run_mode,
            "thinking_mode": p.preferred_thinking_mode,
            "model_key": p.preferred_model_key,
            "message_source": self.params.message_source,
            "goal_mode": bool(self.params.goal_mode),
            "skill_context_messages": list(self.params.skill_context_messages or []),
        }
        if self.params.approval_timeout_seconds is not None:
            session_data["approval_timeout_seconds"] = int(self.params.approval_timeout_seconds)
        d = self.directives
        if d.main_task_gate_token:
            session_data["main_task_gate_token"] = d.main_task_gate_token
        if d.auto_user_message_event:
            session_data["auto_user_message_event"] = True
        if d.auto_user_message_payload:
            session_data["auto_user_message_payload"] = dict(d.auto_user_message_payload)
        if d.preceding_user_notices:
            session_data["preceding_user_notices"] = list(d.preceding_user_notices)
        return session_data


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
