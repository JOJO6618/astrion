"""工作流运行时 REST API（区别于 workflow_page.py 的编辑器 CRUD）。

- `POST /api/workflow/activate`   ：slash 菜单激活。仅智能体空闲（主任务门闸未被持有）
  可用；激活即快照定义，随后以一条 user 提示消息派发一轮工作（门闸 token 随任务移交）。
- `POST /api/workflow/deactivate` ：用户主动退出。柔性原则——只摘牌 + 通知，绝不掐断
  正在运行的智能体：忙时通知入池（由工具循环末尾 inline 消费），闲时直接派发一轮任务。
- `GET  /api/workflow/status`     ：当前对话工作流进度快照（前端刷新/恢复用）。
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

from server.auth_helpers import api_login_required
from server.context import make_terminal_callback, with_terminal

workflow_runtime_bp = Blueprint("workflow_runtime", __name__)


def _broadcast_progress(data_dir, conversation_id: str, username: str) -> None:
    """向该用户房间广播一次工作流进度快照（前端按 conversation_id 过滤）。"""
    try:
        from modules.workflow_state_manager import WorkflowStateManager
        from server.workflow_flow import emit_workflow_progress

        wsm = WorkflowStateManager(data_dir, conversation_id)
        emit_workflow_progress(
            wsm=wsm,
            sender=make_terminal_callback(username),
            conversation_id=conversation_id,
        )
    except Exception:
        pass


@workflow_runtime_bp.route("/api/workflow/activate", methods=["POST"])
@api_login_required
@with_terminal
def api_activate_workflow(terminal, workspace, username):
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    conversation_id = str(data.get("conversation_id") or "").strip() or None
    if not name:
        return jsonify({"error": "缺少工作流名称"}), 400

    from server.main_task_gate import release_main_task_gate, try_acquire_main_task_gate
    from server.workflow_flow import activate_workflow

    created_new = False
    if not conversation_id:
        # 空对话激活（定稿语义：激活条件是「智能体空闲」而非「对话非空」）：
        # 自动创建对话，对齐 tasks/api.py 未带 conversation_id 时的补建先例。
        cm = getattr(getattr(terminal, "context_manager", None), "conversation_manager", None)
        if cm is None:
            return jsonify({"error": "对话管理器不可用"}), 500
        _run_mode = str(getattr(terminal, "run_mode", "") or "fast")
        if _run_mode not in {"fast", "thinking", "deep"}:
            _run_mode = "fast"
        _thinking = getattr(terminal, "thinking_mode", None)
        _thinking = bool(_thinking) if _thinking is not None else (_run_mode != "fast")
        _svc_cid_before = getattr(cm, "current_conversation_id", None)
        # 完整对齐 /api/conversations 正常创建路径的模式继承（否则任务 terminal 恢复时
        # work_mode 回退个性化默认 plan、reasoning_effort 丢失，导致 plan 抑制执行 + 思考退化）：
        # work_mode/权限/执行环境沿用 terminal 当前值；effort 优先级 terminal 当前档 > 个性化默认。
        try:
            from modules.personalization_manager import load_personalization_config as _load_prefs
            _prefs = _load_prefs(workspace.data_dir) or {}
        except Exception:
            _prefs = {}
        _work_mode = "plan"
        try:
            _work_mode = str(terminal.get_work_mode() or "plan") if hasattr(terminal, "get_work_mode") else "plan"
        except Exception:
            pass
        if _work_mode not in ("plan", "ask", "execute"):
            _work_mode = "plan"
        _permission_mode = getattr(terminal, "get_permission_mode", lambda: "unrestricted")()
        if _permission_mode not in ("readonly", "approval", "auto_approval", "unrestricted"):
            _permission_mode = "unrestricted"
        # plan 档不变量：权限必须只读，同时记录进入前权限供离开 plan 恢复
        _pre_plan_permission = None
        if _work_mode == "plan":
            if _permission_mode != "readonly":
                _pre_plan_permission = _permission_mode
            _permission_mode = "readonly"
        _reasoning_effort = getattr(terminal, "reasoning_effort", None)
        if not (isinstance(_reasoning_effort, str) and _reasoning_effort.strip()):
            _reasoning_effort = (_prefs.get("default_reasoning_effort") or None)
        _meta_overrides = {
            "work_mode": _work_mode,
            "permission_mode": _permission_mode,
            "execution_mode": getattr(terminal, "get_execution_mode", lambda: "sandbox")(),
            "pre_plan_permission_mode": _pre_plan_permission,
            "reasoning_effort": _reasoning_effort,
        }
        try:
            conversation_id = cm.create_conversation(
                project_path=str(getattr(workspace, "project_path", "") or "."),
                run_mode=_run_mode,
                thinking_mode=_thinking,
                model_key=getattr(terminal, "model_key", None),
                metadata_overrides=_meta_overrides,
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"创建对话失败：{exc}"}), 500
        created_new = True
        # 恢复服务 terminal 的对话指针，避免污染共享服务 terminal 的上下文状态
        try:
            cm.current_conversation_id = _svc_cid_before
        except Exception:
            pass

    # 仅智能体空闲（主任务门闸未被持有）可激活；新建对话不可能有并发任务，跳过预占。
    gate_token = None
    if not created_new:
        gate_token = try_acquire_main_task_gate(terminal)
        if gate_token is None:
            return jsonify({"error": "智能体正在工作中，工作流仅可在空闲时激活。"}), 409

    try:
        # 激活提示消息将追加到历史末尾，阶段消息游标取当前长度 + 1。
        # 新建对话：激活消息是第 1 条，游标固定为 1（服务 terminal 的 history 长度不可信）。
        if created_new:
            msg_index = 1
        else:
            try:
                msg_index = len(getattr(terminal.context_manager, "conversation_history", []) or []) + 1
            except Exception:
                msg_index = 0
        result = activate_workflow(
            data_dir=workspace.data_dir,
            conversation_id=conversation_id,
            name=name,
            msg_index=msg_index,
        )
        if not result.get("success"):
            release_main_task_gate(terminal, gate_token)
            return jsonify({"error": result.get("error")}), 400

        # 幂等：同工作流已激活 → 只广播进度，不重复派发
        if result.get("already"):
            if gate_token:
                release_main_task_gate(terminal, gate_token)
            _broadcast_progress(workspace.data_dir, conversation_id, username)
            return jsonify({
                "success": True,
                "already": True,
                "conversation_id": conversation_id,
                "snapshot": result["manager"].progress_snapshot(),
            })

        activation_text = str(result.get("text") or "")
        prompt = (
            "工作流已激活，请立即开始按流程执行。"
            "完成当前步骤后调用 report_workflow_stage(summary) 汇报。\n\n"
            f"{activation_text}"
        )

        from .tasks import task_manager

        workspace_id = getattr(workspace, "workspace_id", None) or "default"
        session_data = {
            "username": username,
            "message_source": "workflow",
            # 门闸 token 随任务移交，由任务线程认领（见 process_message_task）
            "main_task_gate_token": gate_token,
            # 让任务事件流携带该 user 消息，保证轮询客户端/刷新后可见
            "auto_user_message_event": True,
            "auto_user_message_payload": {
                "message_source": "workflow",
                "workflow_activate": True,
                "visibility": "chat",
                "starts_work": True,
                "timestamp": datetime.now().isoformat(),
            },
        }
        try:
            rec = task_manager.create_chat_task(
                username,
                workspace_id,
                prompt,
                [],
                conversation_id,
                message_source="workflow",
                session_data=session_data,
            )
        except RuntimeError as exc:
            release_main_task_gate(terminal, gate_token)
            return jsonify({"error": str(exc)}), 409

        _broadcast_progress(workspace.data_dir, conversation_id, username)
        return jsonify({
            "success": True,
            "task_id": rec.task_id,
            "conversation_id": conversation_id,
            # 快照随响应返回：前端立即写入 store，不等任务事件流的首个进度事件
            "snapshot": result["manager"].progress_snapshot(),
        })
    except Exception as exc:  # noqa: BLE001
        release_main_task_gate(terminal, gate_token)
        return jsonify({"error": f"激活工作流失败：{exc}"}), 500


@workflow_runtime_bp.route("/api/workflow/deactivate", methods=["POST"])
@api_login_required
@with_terminal
def api_deactivate_workflow(terminal, workspace, username):
    data = request.get_json(silent=True) or {}
    conversation_id = str(data.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "缺少 conversation_id"}), 400

    from server.main_task_gate import release_main_task_gate, try_acquire_main_task_gate
    from server.workflow_flow import deactivate_workflow_by_user

    result = deactivate_workflow_by_user(
        data_dir=workspace.data_dir,
        conversation_id=conversation_id,
    )
    if not result.get("success"):
        return jsonify({"error": result.get("error")}), 400

    _broadcast_progress(workspace.data_dir, conversation_id, username)

    # 柔性通知已入池：闲时直接取出并派发一轮任务；忙时留池，
    # 由运行中的工具循环末尾 process_workflow_updates inline 消费。
    dispatched = False
    gate_token = try_acquire_main_task_gate(terminal)
    if gate_token is not None:
        wsm = None
        notices = None
        restored = False
        try:
            from modules.workflow_state_manager import WorkflowStateManager

            wsm = WorkflowStateManager(workspace.data_dir, conversation_id)
            notices = wsm.poll_notices()
            notice_text = "\n\n".join(
                str(n.get("message") or "").strip() for n in notices if str(n.get("message") or "").strip()
            )
            if notice_text:
                from .tasks import task_manager

                workspace_id = getattr(workspace, "workspace_id", None) or "default"
                session_data = {
                    "username": username,
                    "message_source": "workflow",
                    "main_task_gate_token": gate_token,
                    "auto_user_message_event": True,
                    "auto_user_message_payload": {
                        "message_source": "workflow",
                        "workflow_notice": True,
                        "visibility": "chat",
                        "starts_work": True,
                        "timestamp": datetime.now().isoformat(),
                    },
                }
                task_manager.create_chat_task(
                    username,
                    workspace_id,
                    notice_text,
                    [],
                    conversation_id,
                    message_source="workflow",
                    session_data=session_data,
                )
                dispatched = True
        except Exception:  # noqa: BLE001
            # 任何失败：通知放回池（等轮询器/工具循环消费），不静默丢失
            if wsm is not None and notices and not restored:
                try:
                    wsm.restore_notices(notices)
                    restored = True
                except Exception:  # noqa: BLE001
                    pass
        finally:
            if not dispatched:
                release_main_task_gate(terminal, gate_token)

    return jsonify({
        "success": True,
        "workflow_name": result.get("workflow_name"),
        "dispatched": dispatched,
        # 摘牌后快照（active=False），前端据此关闭窗口
        "snapshot": {"active": False},
    })


@workflow_runtime_bp.route("/api/workflow/status", methods=["GET"])
@api_login_required
@with_terminal
def api_workflow_status(terminal, workspace, username):
    conversation_id = str(request.args.get("conversation_id") or "").strip()
    if not conversation_id:
        return jsonify({"error": "缺少 conversation_id"}), 400
    try:
        from modules.workflow_state_manager import WorkflowStateManager

        wsm = WorkflowStateManager(workspace.data_dir, conversation_id)
        return jsonify({"success": True, "snapshot": wsm.progress_snapshot()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"读取工作流状态失败：{exc}"}), 500
