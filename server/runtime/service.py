"""RuntimeService：公共任务受理与控制入口（契约 docs/runtime_contract.md §4.2）。

定位：Web（HTTP 适配层）、CLI、未来的定时触发器等调用方共用的任务入口。
本服务只做「受理裁决 + 显式上下文转发 + 控制委托」，不持有任务状态——
任务记录、事件流、门闸、保存保护仍由既有 TaskManager / main_task_gate /
conversation_manager 承载（契约 §2 状态责任表不变）。

兼容期说明：create_task 内部把 RuntimeContext 转换为既有
session_data 快照传入 create_chat_task；任务线程已改为显式 RuntimeIdentity
驱动资源装配（test_request_context 桥已拆除），session_data 快照仍承载
门闸 token、事件回放等内部指令的跨线程传递。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from modules.i18n import tr
from server.runtime.context import RuntimeContext, TrustedPrincipal


class RuntimeService:
    """公共任务入口。无状态：全部状态委托给 task_manager 单例。"""

    # ---- 受理 ----

    def create_task(self, ctx: RuntimeContext):
        """受理一轮 Run：校验显式上下文 → 互斥裁决（create_chat_task 内）→ 登记 → 起执行。

        抛错契约（适配层负责映射 HTTP 状态码）：
        - ValueError：上下文/参数非法（400）
        - RuntimeError：同对话已有运行中 chat 任务等业务冲突（409）
        """
        ctx.validate()
        # 延迟导入避免循环：server.tasks 的 blueprint 链不依赖本包
        from server.tasks import task_manager

        params = ctx.params
        conversation_id = params.conversation_id
        if not conversation_id and str(params.task_type or "chat").strip().lower() == "chat":
            # 对话级隔离兜底（自 server/tasks/api.py 下沉）：chat 任务必须落在
            # 对话级 terminal 上运行。补建对话文件是装配职责，收在服务层单点，
            # Web/CLI/定时触发器等调用方无需各自实现「先建会话再发任务」。
            conversation_id = self._ensure_conversation_for_chat(ctx)
        return task_manager.create_chat_task(
            ctx.principal.username,
            ctx.principal.workspace_id,
            params.message,
            list(params.images or []),
            conversation_id,
            videos=list(params.videos or []),
            model_key=params.model_key,
            thinking_mode=params.thinking_mode,
            run_mode=params.run_mode,
            max_iterations=params.max_iterations,
            session_data=ctx.to_session_data(),
            message_source=params.message_source,
            goal_mode=params.goal_mode,
            skill_context_messages=list(params.skill_context_messages or []),
            files=list(params.files or []),
            task_type=params.task_type,
        )

    @staticmethod
    def _ensure_conversation_for_chat(ctx: RuntimeContext) -> Optional[str]:
        """chat 任务未携带 conversation_id 时补建对话文件。

        失败时返回 None（容错语义与原适配层兜底一致：任务线程内
        ensure_conversation_loaded 仍有最终兜底，但会失去对话级隔离，
        仅作为极端降级路径存在）。
        """
        try:
            from server.context import RuntimeIdentity, get_user_resources
            from server.utils_common import debug_log

            p = ctx.principal
            params = ctx.params
            identity = RuntimeIdentity(
                host_mode=p.host_mode,
                host_workspace_id=p.host_workspace_id,
                is_api_user=p.is_api_user,
                role=p.role,
                preferred_model_key=params.model_key or p.preferred_model_key,
                preferred_run_mode=params.run_mode or p.preferred_run_mode,
                preferred_thinking_mode=(
                    params.thinking_mode if params.thinking_mode is not None else p.preferred_thinking_mode
                ),
            )
            terminal, workspace = get_user_resources(
                p.username, workspace_id=p.workspace_id, update_session=False, identity=identity
            )
            cm = getattr(getattr(terminal, "context_manager", None), "conversation_manager", None)
            if cm is None or workspace is None:
                return None
            run_mode = params.run_mode or p.preferred_run_mode or "fast"
            if run_mode not in {"fast", "thinking", "deep"}:
                run_mode = "fast"
            thinking_mode = params.thinking_mode
            if thinking_mode is None:
                thinking_mode = p.preferred_thinking_mode
            thinking_mode = bool(thinking_mode) if thinking_mode is not None else (run_mode != "fast")
            conversation_id = cm.create_conversation(
                project_path=str(getattr(workspace, "project_path", "") or "."),
                run_mode=run_mode,
                thinking_mode=thinking_mode,
                model_key=params.model_key or p.preferred_model_key,
            )
            debug_log(f"[RuntimeService] 未携带 conversation_id，已补建对话: {conversation_id}")
            return conversation_id
        except Exception as exc:
            try:
                from server.utils_common import debug_log

                debug_log(f"[RuntimeService] 补建对话失败（继续按无 cid 处理）: {exc}")
            except Exception:
                pass
            return None

    # ---- 控制 ----

    def cancel_task(self, username: str, task_id: str) -> bool:
        """停止主 Run（不触碰后台工作者；后台任务有独立控制入口）。"""
        from server.tasks import task_manager

        return task_manager.cancel_task(username, task_id)

    def enqueue_runtime_guidance(
        self, username: str, task_id: str, message: str, source: Optional[str] = None
    ) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.enqueue_runtime_guidance(username, task_id, message, source=source)

    def enqueue_runtime_pending_message(
        self, username: str, task_id: str, message: str, files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.enqueue_runtime_pending_message(username, task_id, message, files=files)

    def remove_runtime_pending_message(self, username: str, task_id: str, message_id: str) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.remove_runtime_pending_message(username, task_id, message_id)

    def promote_runtime_pending_to_guidance(self, username: str, task_id: str, message_id: str) -> Dict[str, Any]:
        from server.tasks import task_manager

        return task_manager.promote_runtime_pending_to_guidance(username, task_id, message_id)

    # ---- 审批/提问（公共入口；三类 manager 的薄路由，裁决语义由 manager 保证）----

    _APPROVAL_KINDS = ("tool", "plan", "question")

    def list_pending_approvals(
        self, username: str, conversation_id: Optional[str] = None, *, kind: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """待决审批/提问合并查询（approval.list）。kind=None 返回三类全量（按键分桶）。"""
        from server.state import plan_approval_manager, tool_approval_manager, user_question_manager

        kinds = (kind,) if kind else self._APPROVAL_KINDS
        if any(k not in self._APPROVAL_KINDS for k in kinds):
            raise ValueError(f"runtime_context: 未知审批类型 {kind}")
        result: Dict[str, List[Dict[str, Any]]] = {}
        if "tool" in kinds:
            result["tool"] = tool_approval_manager.list_pending(username, conversation_id)
        if "plan" in kinds:
            result["plan"] = plan_approval_manager.list_pending(username, conversation_id)
        if "question" in kinds:
            result["question"] = user_question_manager.list_pending(username, conversation_id)
        return result

    def resolve_approval(
        self,
        kind: str,
        *,
        username: str,
        item_id: str,
        decision: Optional[str] = None,
        approved: Optional[bool] = None,
        comment: Optional[str] = None,
        selected_option_id: Optional[str] = None,
        text: Optional[str] = None,
        dismissed: bool = False,
    ) -> Dict[str, Any]:
        """统一裁决入口（approval.resolve）。kind 路由：
        - tool: decision ∈ {"approved","rejected"}（锁内单次裁决，重复回答返回现状）
        - plan: approved bool + 可选 comment
        - question: selected_option_id / text / dismissed 三选一语义
        错误语义与 manager 一致：越权 PermissionError、不存在 KeyError、参数非法 ValueError。
        """
        from server.state import plan_approval_manager, tool_approval_manager, user_question_manager

        if kind == "tool":
            if decision is None:
                raise ValueError("runtime_context: tool 审批需 decision")
            return tool_approval_manager.decide(item_id, username, str(decision), reason=comment)
        if kind == "plan":
            if approved is None:
                raise ValueError("runtime_context: plan 审批需 approved")
            return plan_approval_manager.answer(
                approval_id=item_id, username=username, approved=bool(approved), comment=comment
            )
        if kind == "question":
            return user_question_manager.answer(
                question_id=item_id, username=username,
                selected_option_id=selected_option_id, text=text, dismissed=dismissed,
            )
        raise ValueError(f"runtime_context: 未知审批类型 {kind}")

    # ---- 会话查询（公共入口；CLI/定时器等非 Web 调用方不依赖 Web 路由）----

    def list_sessions(
        self,
        username: str,
        workspace_id: str,
        principal: Optional[TrustedPrincipal] = None,
        limit: int = 50,
        offset: int = 0,
        multi_agent_mode: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """会话列表查询。principal 省略时按 username/workspace_id 构造最小身份快照。

        注意：返回结构由 conversation 管理链路定义（items/total 等），本服务只做
        资源装配与转发，不重排字段。
        """
        terminal, _workspace = self._resources_for_query(username, workspace_id, principal)
        cm = getattr(terminal, "context_manager", None)
        if cm is None:
            raise RuntimeError(tr("tasks.system_not_initialized"))
        return cm.get_conversation_list(limit=limit, offset=offset, multi_agent_mode=multi_agent_mode)

    def get_session_history(
        self,
        username: str,
        workspace_id: str,
        conversation_id: str,
        principal: Optional[TrustedPrincipal] = None,
    ) -> Optional[Dict[str, Any]]:
        """会话历史读取（磁盘权威快照）。返回 None 表示不存在。"""
        if not str(conversation_id or "").strip():
            raise ValueError("runtime_context: conversation_id 不能为空")
        terminal, _workspace = self._resources_for_query(username, workspace_id, principal)
        cm = getattr(terminal, "context_manager", None)
        if cm is None:
            raise RuntimeError(tr("tasks.system_not_initialized"))
        manager = cm._get_conversation_manager_for_id(conversation_id)
        return manager.load_conversation(conversation_id)

    @staticmethod
    def _resources_for_query(username: str, workspace_id: str, principal: Optional[TrustedPrincipal]):
        """查询类调用的资源装配（工作区级 terminal 即可，不加载会话到内存）。"""
        from server.context import RuntimeIdentity, get_user_resources

        if principal is None:
            principal = TrustedPrincipal(username=username, workspace_id=workspace_id)
        if principal.username != username:
            # 纵深防御：principal 是适配层认证后的可信身份，不得与查询目标身份不符
            raise PermissionError("runtime_context: principal 与查询目标用户不一致")
        if str(principal.workspace_id or "") != str(workspace_id or ""):
            # 资源范围一致性：principal 声明的 workspace 即授权范围，
            # 跨工作区查询必须重新认证构造新 principal，不得仅传第二份参数
            raise PermissionError("runtime_context: principal 与查询目标工作区不一致")
        identity = RuntimeIdentity(
            host_mode=principal.host_mode,
            host_workspace_id=principal.host_workspace_id,
            is_api_user=principal.is_api_user,
            role=principal.role,
            preferred_model_key=principal.preferred_model_key,
            preferred_run_mode=principal.preferred_run_mode,
            preferred_thinking_mode=principal.preferred_thinking_mode,
        )
        terminal, workspace = get_user_resources(
            username, workspace_id=workspace_id, update_session=False, identity=identity
        )
        if terminal is None:
            raise RuntimeError(tr("tasks.system_not_initialized"))
        return terminal, workspace

    # ---- 观察（内部接口；后台调用方不必为观察任务发 HTTP 请求）----

    def list_runs(
        self,
        username: str,
        workspace_id: Optional[str] = None,
        *,
        conversation_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Run 发现查询（run.list）：按工作区/会话/状态筛选，created_at 倒序。

        客户端 B 不知 task_id 时经本接口发现活动 Run（审核 F1）。载荷与 Web
        /api/tasks 同构（复用 _task_public_payload），保证多端字段一致。
        status 过滤语义同 Web 路由："active" = pending/running/cancel_requested，
        或逗号分隔状态集；None 不过滤。归属约束：仅返回 username 自己的 Run。
        """
        from server.tasks import task_manager
        from server.tasks.models import task_public_payload

        recs = task_manager.list_tasks(username, workspace_id)
        if conversation_id:
            target = str(conversation_id)
            recs = [r for r in recs if str(getattr(r, "conversation_id", None) or "") in {target, target[5:] if target.startswith("conv_") else f"conv_{target}"}]
        if status:
            normalized = str(status).strip().lower()
            if normalized == "active":
                active = {"pending", "running", "cancel_requested"}
                recs = [r for r in recs if r.status in active]
            else:
                wanted = {part.strip() for part in normalized.split(",") if part.strip()}
                recs = [r for r in recs if r.status in wanted]
        recs = sorted(recs, key=lambda x: x.created_at, reverse=True)
        # 载荷与 Web /api/tasks 完全同构（核心层唯一序列化实现，无新旧双轨）
        return [task_public_payload(r) for r in recs]

    def get_runtime_pending_messages(self, username: str, task_id: str):
        """追问队列查询（run.queue 的查询面）。"""
        from server.tasks import task_manager

        return task_manager.get_runtime_pending_messages(username, task_id)

    def get_task(self, username: str, task_id: str):
        from server.tasks import task_manager

        return task_manager.get_task(username, task_id)

    def get_task_events(
        self, username: str, task_id: str, offset: int
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[int], Optional[str], Optional[Dict[str, Any]]]:
        """按 offset 增量读取任务事件流（idx/offset 协议，与 REST 轮询同一语义）。

        返回 (events, next_offset, error, meta)。error 非空表示任务不存在或无权访问。
        meta 携带缺口检测水位：``window_start`` = 事件窗口当前最小 idx
        （协议 docs/runtime_protocol.md §5.2）；客户端 offset < window_start
        即事件已被裁剪，须走重新同步（会话快照对账）而非续传。
        """
        from server.tasks import task_manager

        rec = task_manager.get_task(username, task_id)
        if not rec:
            return None, None, tr("tasks.task_not_found"), None
        offset = max(0, int(offset or 0))
        events = task_manager.get_events_since(rec, offset)
        next_offset = events[-1]["idx"] + 1 if events else offset
        meta = {"window_start": task_manager.get_event_window_start(rec)}
        return events, next_offset, None, meta


# 进程级单例（无状态，可安全共享）
runtime_service = RuntimeService()
