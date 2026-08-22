"""工作流（Workflow）运行时编排（定稿：docs/workflow_feature_plan.md §4）。

职责集中在此，尽量减少对主循环/工具循环的侵入：
- 激活（工具与 REST 共用）：幂等规则 + 快照复制 + 激活上下文文本
- system 段构建（不冻结，每次 build_messages 现生成）
- report_workflow_stage / choose_workflow_branch 的推进矩阵（_arrive_at 递归消解）
- 审核调用（payload 构建 + 消息游标痕迹截取 + WorkflowReviewAgent）
- 柔性通知文本构造（用户退出 / maxRejects / max_stage_rounds）
- 进度事件广播（对齐 goal 的 sender → 轮询透传链路）

柔性原则：一切终态只「摘牌 + 通知」，绝不掐断智能体工作。
review 是瞬态节点：同步审核完直接走到下一站，current 只停 stage / branch / end。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from modules.workflow_manager import load_workflow, validate_structure, workflow_to_markdown
from modules.workflow_review_agent import WorkflowReviewAgent
from modules.workflow_state_manager import (
    REASON_COMPLETED,
    REASON_MAX_REJECTS,
    REASON_MODEL,
    REASON_USER,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_STOPPED,
    WorkflowStateManager,
)

_KIND_LABELS = {"stage": "阶段", "review": "审核", "branch": "分支", "start": "开始", "end": "结束"}

# 审核痕迹截取长度上限（防 payload 膨胀）
_STAGE_TRACE_MAX_CHARS = 3500


# ---------------------------------------------------------------- 基础


def get_active_manager(data_dir, conversation_id: Optional[str]) -> Optional[WorkflowStateManager]:
    """返回本对话处于 active 的工作流状态管理器；无则 None。"""
    if not conversation_id:
        return None
    try:
        wsm = WorkflowStateManager(data_dir, conversation_id)
    except ValueError:
        return None
    return wsm if wsm.is_active() else None


def workflow_is_active(data_dir, conversation_id: Optional[str]) -> bool:
    return get_active_manager(data_dir, conversation_id) is not None


def _history_len(web_terminal) -> int:
    try:
        return len(web_terminal.context_manager.conversation_history or [])
    except Exception:
        return 0


# ---------------------------------------------------------------- 激活


def activate_workflow(
    *,
    data_dir,
    conversation_id: str,
    name: str,
    msg_index: int,
) -> Dict[str, Any]:
    """激活入口（AI 工具与 REST 共用）。

    幂等规则：同工作流已激活 → 返回当前进度；不同工作流 → 拒绝（提示先退出）。
    成功返回 {"success": True, "text": 激活上下文文本, "manager": wsm, "already": bool}。
    """
    name = str(name or "").strip()
    if not name:
        return {"success": False, "error": "缺少工作流名称。"}
    existing = get_active_manager(data_dir, conversation_id)
    if existing is not None:
        current_name = str(existing.state.get("workflow_name") or "")
        if current_name == name:
            text = build_activation_text(wsm=existing, already=True)
            return {"success": True, "already": True, "text": text, "manager": existing}
        return {
            "success": False,
            "error": (
                f"当前对话已激活工作流「{current_name}」，同一时间只能激活一个工作流。"
                "请先调用 deactivate_workflow 退出，再激活新的。"
            ),
        }
    try:
        wf = load_workflow(name, data_dir)
    except FileNotFoundError:
        return {"success": False, "error": f"工作流不存在：{name}"}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    errors = validate_structure(wf)
    if errors:
        return {"success": False, "error": "工作流结构校验未通过：" + "；".join(errors)}
    wsm = WorkflowStateManager(data_dir, conversation_id)
    entry = None
    for node in wf.get("nodes") or []:
        if node.get("kind") == "start":
            entry = node.get("next")
            break
    entry_node = None
    for node in wf.get("nodes") or []:
        if node.get("id") == entry:
            entry_node = node
            break
    if not entry_node:
        return {"success": False, "error": "工作流缺少有效的入口节点（开始节点未连接）。"}
    wsm.activate(
        workflow_name=str(wf.get("name") or name),
        definition_markdown=workflow_to_markdown(wf),
        entry_node_id=str(entry_node["id"]),
        stage_start_msg_index=msg_index,
    )
    text = build_activation_text(wsm=wsm, already=False)
    return {"success": True, "already": False, "text": text, "manager": wsm}


def _ordered_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按流程拓扑顺序排列节点：从开始节点沿边 BFS，未连通的孤立节点排在最后（保持原相对顺序）。

    定义文件里的 nodes 数组顺序是编辑器保存顺序，不代表流程顺序（用户可能先拖了结束节点），
    直接遍历会让目录错乱（如「结束」排在阶段前面）。
    """
    valid = [n for n in nodes if isinstance(n, dict) and n.get("id")]
    by_id = {str(n.get("id")): n for n in valid}

    def _targets(node: Dict[str, Any]) -> List[str]:
        kind = node.get("kind")
        if kind in ("start", "stage"):
            nxt = node.get("next")
            return [str(nxt)] if nxt else []
        if kind == "review":
            out = []
            if node.get("next"):
                out.append(str(node["next"]))
            if node.get("rejectTo"):
                out.append(str(node["rejectTo"]))
            return out
        if kind == "branch":
            return [
                str(r.get("target"))
                for r in (node.get("next") or [])
                if isinstance(r, dict) and r.get("target")
            ]
        return []

    ordered: List[Dict[str, Any]] = []
    seen: set = set()
    queue = [n for n in valid if n.get("kind") == "start"]
    while queue:
        node = queue.pop(0)
        nid = str(node.get("id"))
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(node)
        for target in _targets(node):
            if target in by_id and target not in seen:
                queue.append(by_id[target])
    for n in valid:
        if str(n.get("id")) not in seen:
            ordered.append(n)
    return ordered


def build_activation_text(*, wsm: WorkflowStateManager, already: bool = False) -> str:
    """激活上下文：全景目录 + 当前节点详情（工具返回 / REST 激活消息共用）。"""
    definition = wsm.load_definition() or {}
    current = wsm.get_node(wsm.get_current_node_id()) or {}
    lines: List[str] = []
    if already:
        lines.append(f"【工作流已处于激活状态】{definition.get('name')}：{definition.get('description')}")
    else:
        lines.append(f"【工作流已激活】{definition.get('name')}：{definition.get('description')}")
    lines.append("")
    body = str(definition.get("body") or "").strip()
    if body:
        lines.append("【流程约定】")
        lines.append(body)
        lines.append("")
    lines.append("【节点目录】")
    for node in _ordered_nodes(definition.get("nodes") or []):
        if not isinstance(node, dict):
            continue
        kind = node.get("kind")
        label = _KIND_LABELS.get(kind, kind or "?")
        desc = ""
        if kind == "stage":
            desc = str(node.get("goal") or "")
        elif kind == "review":
            desc = f"把关：{node.get('prompt') or '阶段产出审核'}"
        elif kind == "branch":
            desc = "按条件选择路径"
        marker = " ← 当前" if node.get("id") == current.get("id") else ""
        lines.append(f"- [{label}] {node.get('name')}：{desc}{marker}")
    lines.append("")
    lines.append(_current_node_brief(wsm=wsm, current=current))
    return "\n".join(lines)


def _current_node_brief(*, wsm: WorkflowStateManager, current: Dict[str, Any]) -> str:
    """当前节点详情文本（激活/推进/状态查询共用）。"""
    kind = current.get("kind")
    name = current.get("name")
    if kind == "stage":
        lines = [f"【当前阶段】{name}", f"目标：{current.get('goal') or '（未填写）'}"]
        instructions = str(current.get("instructions") or "").strip()
        if instructions:
            lines.append(f"要求：{instructions}")
        lines.append("完成后调用 report_workflow_stage(summary) 汇报以推进流程。")
        return "\n".join(lines)
    if kind == "branch":
        routes = current.get("next") or []
        menu = "\n".join(
            f"- {r.get('target')}（{r.get('condition') or '无条件描述'}）" for r in routes if isinstance(r, dict)
        )
        return (
            f"【当前位于分支点】{name}\n请选择后续路径（调用 choose_workflow_branch(target_node_id)）：\n{menu}"
        )
    return f"【当前位置】{name}"


# ---------------------------------------------------------------- system 段（不冻结，每次现生成）

WORKFLOW_SYSTEM_PREFIX = "【工作流进行中】"


def refresh_workflow_system_segment(messages, *, data_dir, conversation_id: Optional[str]) -> None:
    """阶段推进/退出后同步刷新 messages 里的工作流 system 段。

    单任务内 messages 只在入口构建一次，阶段推进后后续迭代会看到滞后的当前位置；
    推进工具（report/choose）执行成功后由工具循环层调用本函数刷新。
    工作流已退出（新内容为空）时移除该段。
    """
    if not isinstance(messages, list):
        return
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        content = str(msg.get("content") or "")
        if not content.startswith(WORKFLOW_SYSTEM_PREFIX):
            continue
        try:
            new_content = build_workflow_system_prompt(data_dir=data_dir, conversation_id=conversation_id)
        except Exception:
            return
        if new_content:
            msg["content"] = new_content
        else:
            messages.pop(idx)
        return


def build_workflow_system_prompt(*, data_dir, conversation_id: Optional[str]) -> str:
    """工作流进行中的 system 上下文段。无激活工作流时返回空串（不注入）。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return ""
    definition = wsm.load_definition() or {}
    current = wsm.get_node(wsm.get_current_node_id()) or {}
    lines: List[str] = [
        f"【工作流进行中】{definition.get('name')}：{definition.get('description')}",
        "【节点目录】（完整定义见激活时的上下文；迷失时可调用 get_workflow_status 自查）",
    ]
    for node in definition.get("nodes") or []:
        if not isinstance(node, dict) or node.get("kind") in ("start", "end"):
            continue
        kind = node.get("kind")
        label = _KIND_LABELS.get(kind, kind or "?")
        if kind == "stage":
            desc = str(node.get("goal") or "")
        elif kind == "review":
            desc = f"把关：{node.get('prompt') or '阶段产出审核'}"
        else:
            desc = "按条件选择路径"
        marker = " ← 当前" if node.get("id") == current.get("id") else ""
        lines.append(f"- [{label}] {node.get('name')}：{desc}{marker}")
    lines.append("")
    lines.append(_current_node_brief(wsm=wsm, current=current))
    lines.append(
        "工作流只是辅助流程：期间可以正常与用户讨论其他内容；"
        "阶段完成必须显式调用 report_workflow_stage 汇报，不要在没有汇报的情况下宣称阶段完成。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 推进矩阵


async def handle_stage_report(
    *,
    web_terminal,
    data_dir,
    sender,
    conversation_id: str,
    summary: str,
) -> Dict[str, Any]:
    """report_workflow_stage 核心：当前必为 stage，按下一节点类型分派（定稿 §4.3）。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return {
            "success": False,
            "error": "当前对话没有激活的工作流（可能已被退出）。如需重新开始，请调用 activate_workflow。",
        }
    current = wsm.get_node(wsm.get_current_node_id())
    if not current:
        return {"success": False, "error": "工作流状态异常：当前节点不存在于定义快照中。可调用 get_workflow_status 自查。"}
    if current.get("kind") == "branch":
        return {
            "success": False,
            "error": f"当前停在分支点「{current.get('name')}」，请先调用 choose_workflow_branch(target_node_id) 选择路径。",
        }
    if current.get("kind") != "stage":
        return {"success": False, "error": f"当前不在执行阶段（位于「{current.get('name')}」），无法汇报阶段完成。"}
    nxt = wsm.get_node(current.get("next"))
    if not nxt:
        return {"success": False, "error": "流程定义异常：当前阶段没有有效的后续节点。"}
    stage_info = {
        "node_id": current.get("id"),
        "name": str(current.get("name") or ""),
        "summary": str(summary or "").strip(),
        "rounds": wsm.get_stage_rounds(),
    }
    text = await _arrive_at(
        node=nxt,
        wsm=wsm,
        web_terminal=web_terminal,
        sender=sender,
        conversation_id=conversation_id,
        stage_info=stage_info,
    )
    return {"success": True, "message": text}


async def handle_branch_choice(
    *,
    web_terminal,
    data_dir,
    sender,
    conversation_id: str,
    target_node_id: str,
) -> Dict[str, Any]:
    """choose_workflow_branch 核心：仅当前停在 branch 时可调，校验候选集后推进。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return {"success": False, "error": "当前对话没有激活的工作流。"}
    current = wsm.get_node(wsm.get_current_node_id())
    if not current or current.get("kind") != "branch":
        return {"success": False, "error": "当前不在分支点，无需选择路径。"}
    target_node_id = str(target_node_id or "").strip()
    routes = [r for r in (current.get("next") or []) if isinstance(r, dict)]
    valid = next((r for r in routes if r.get("target") == target_node_id), None)
    if valid is None:
        menu = "、".join(str(r.get("target")) for r in routes)
        return {"success": False, "error": f"「{target_node_id}」不在候选路径中。可选：{menu}"}
    target = wsm.get_node(target_node_id)
    if not target:
        return {"success": False, "error": f"目标节点不存在：{target_node_id}"}
    text = await _arrive_at(
        node=target,
        wsm=wsm,
        web_terminal=web_terminal,
        sender=sender,
        conversation_id=conversation_id,
        stage_info=None,
    )
    return {"success": True, "message": f"已选择路径：{valid.get('condition') or target.get('name')}\n\n{text}"}


async def _arrive_at(
    *,
    node: Dict[str, Any],
    wsm: WorkflowStateManager,
    web_terminal,
    sender,
    conversation_id: str,
    stage_info: Optional[Dict[str, Any]],
) -> str:
    """到达节点的统一处理（递归穿透 review / 单出线 branch）。

    stage_info 非空表示「刚汇报完成的 stage」——只在落地分支（stage/branch多/end）
    记入 history；review 驳回不记录（阶段未完成）。
    """
    kind = node.get("kind")
    name = str(node.get("name") or node.get("id") or "")

    if kind == "stage":
        if stage_info:
            wsm.record_stage_completion(summary=stage_info["summary"], rounds=stage_info["rounds"])
        wsm.move_to(str(node["id"]), msg_index=_history_len(web_terminal))
        emit_workflow_progress(wsm=wsm, sender=sender, conversation_id=conversation_id)
        head = f"阶段「{stage_info['name']}」已记录完成。\n\n" if stage_info else ""
        return head + _current_node_brief(wsm=wsm, current=node)

    if kind == "end":
        if stage_info:
            wsm.record_stage_completion(summary=stage_info["summary"], rounds=stage_info["rounds"])
        # 先广播「完成态」快照（前端播最后一行落定+「结束」行动画），再摘牌。
        # 注意顺序不能反：deactivate 后 progress_snapshot 只剩 {"active": False}，
        # 前端会瞬间卸载窗口，完成动画与「结束」行都播不出来。
        if callable(sender) and conversation_id:
            snap = wsm.progress_snapshot()
            snap.update({
                "status": "completed",
                "current": None,
                "next": None,
                "reviewing": False,
                "footnote": {"kind": "success", "text": "工作流已完成"},
                "event": "workflow_completed",
                "conversation_id": conversation_id,
            })
            try:
                sender("workflow_progress", snap)
            except Exception:
                pass
        wsm.deactivate(status=STATUS_COMPLETED, reason=REASON_COMPLETED)
        head = f"阶段「{stage_info['name']}」已记录完成。\n\n" if stage_info else ""
        return head + f"工作流「{wsm.state.get('workflow_name')}」已到达终点「{name}」，全部完成。请向用户输出总结后结束。"

    if kind == "branch":
        routes = [r for r in (node.get("next") or []) if isinstance(r, dict) and r.get("target")]
        if len(routes) <= 1:
            # 并线器（单出线）：自动穿过，不记完成、不停留
            target = wsm.get_node(routes[0].get("target")) if routes else None
            if not target:
                return "流程定义异常：分支节点没有有效的出线。"
            return await _arrive_at(
                node=target, wsm=wsm, web_terminal=web_terminal, sender=sender,
                conversation_id=conversation_id, stage_info=stage_info,
            )
        # AI 决策点（多出线）：记完成 + 停留等选择
        if stage_info:
            wsm.record_stage_completion(summary=stage_info["summary"], rounds=stage_info["rounds"])
        wsm.move_to(str(node["id"]), msg_index=_history_len(web_terminal))
        emit_workflow_progress(wsm=wsm, sender=sender, conversation_id=conversation_id)
        head = f"阶段「{stage_info['name']}」已记录完成。\n\n" if stage_info else ""
        return head + _current_node_brief(wsm=wsm, current=node)

    if kind == "review":
        result = await run_stage_review(
            web_terminal=web_terminal, wsm=wsm, sender=sender, conversation_id=conversation_id,
            stage_info=stage_info, review_node=node,
        )
        wsm.record_review(
            node_id=str(node.get("id")), name=name,
            decision=str(result.get("decision") or ""), message=str(result.get("message") or ""),
        )
        if result.get("decision") == "pass":
            nxt = wsm.get_node(node.get("next"))
            if not nxt:
                return f"审核「{name}」通过，但通过路由指向不存在的节点。流程定义异常，工作流无法继续。"
            inner = await _arrive_at(
                node=nxt, wsm=wsm, web_terminal=web_terminal, sender=sender,
                conversation_id=conversation_id, stage_info=stage_info,
            )
            return f"审核「{name}」通过：{result.get('message')}\n\n{inner}"
        # 驳回
        count = wsm.increment_reject(str(node.get("id")))
        max_rejects = int(node.get("maxRejects") or 3)
        review_message = str(result.get("message") or "")
        if count >= max_rejects:
            wsm.deactivate(status=STATUS_FAILED, reason=REASON_MAX_REJECTS)
            emit_workflow_progress(
                wsm=wsm, sender=sender, conversation_id=conversation_id,
                extra={"event": "workflow_failed", "reason": REASON_MAX_REJECTS},
            )
            return (
                f"审核「{name}」未通过（第 {count} 次，已达连续驳回上限 {max_rejects}）：{review_message}\n\n"
                "工作流已连续驳回超限而终止（failed）。请告知用户审核意见与终止原因；"
                "如需重新开始，可在调整流程或准备充分后重新激活。"
            )
        reject_target = wsm.get_node(node.get("rejectTo"))
        if not reject_target:
            return f"审核「{name}」未通过：{review_message}\n\n但驳回路由指向不存在的节点，流程定义异常，工作流无法继续。"
        wsm.move_to(str(reject_target["id"]), msg_index=_history_len(web_terminal))
        emit_workflow_progress(
            wsm=wsm, sender=sender, conversation_id=conversation_id,
            extra={"event": "workflow_rejected", "review_node": name, "reject_count": count},
        )
        return (
            f"审核「{name}」未通过（第 {count}/{max_rejects} 次）：{review_message}\n\n"
            f"你已回到「{reject_target.get('name')}」。请按整改意见修改后，重新调用 report_workflow_stage 汇报。"
        )

    return f"流程定义异常：未知节点类型 {kind!r}（节点「{name}」）。"


# ---------------------------------------------------------------- 审核


def _summarize_tool_args(raw_args: Any) -> str:
    """工具参数摘要（取关键字段，截断控长）。"""
    args = raw_args
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        return ""
    for key in ("command", "path", "skill_name", "query", "file_path", "url", "task", "summary", "name"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text[:80] + ("…" if len(text) > 80 else "")
    return ""


def build_stage_trace(web_terminal, start_index: int) -> str:
    """消息游标截取本阶段的工具调用时间线（审核 payload 的证据段）。"""
    try:
        history = web_terminal.context_manager.conversation_history or []
    except Exception:
        history = []
    slice_ = history[start_index:] if 0 <= start_index < len(history) else []
    lines: List[str] = []
    for msg in slice_:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for call in msg.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                fname = fn.get("name")
                if not fname:
                    continue
                summary = _summarize_tool_args(fn.get("arguments"))
                lines.append(f"→ {fname}：{summary}" if summary else f"→ {fname}")
        elif role == "tool":
            content = " ".join(str(msg.get("content") or "").split())
            if content:
                lines.append(f"  ↳ {content[:160]}{'…' if len(content) > 160 else ''}")
    text = "\n".join(lines)
    if len(text) > _STAGE_TRACE_MAX_CHARS:
        text = "…（前段略）\n" + text[-_STAGE_TRACE_MAX_CHARS:]
    return text or "（本阶段暂无工具调用记录）"


def build_review_payload(
    *,
    web_terminal,
    wsm: WorkflowStateManager,
    stage_info: Dict[str, Any],
    review_node: Dict[str, Any],
) -> str:
    """审核 payload：工作流信息 + 审核关注点 + 阶段目标要求 + 执行痕迹 + 汇报 + 历史驳回。"""
    definition = wsm.load_definition() or {}
    stage_node = wsm.get_node(stage_info.get("node_id")) or {}
    lines: List[str] = [
        f"【工作流】{definition.get('name')}：{definition.get('description')}",
        "",
        f"【本次审核把关】{review_node.get('name')}",
        f"审核关注点：{review_node.get('prompt') or '阶段产出是否达到进入下一阶段的门槛'}",
        "",
        f"【被审核阶段】{stage_info.get('name')}",
        f"阶段目标：{stage_node.get('goal') or '（未填写）'}",
    ]
    instructions = str(stage_node.get("instructions") or "").strip()
    if instructions:
        lines.append(f"阶段要求：{instructions}")
    lines += [
        "",
        "【阶段执行痕迹】（本阶段内的工具调用时间线）",
        build_stage_trace(web_terminal, wsm.get_stage_start_msg_index()),
        "",
        "【主执行模型阶段汇报】",
        stage_info.get("summary") or "（无）",
    ]
    rejects = [
        h for h in (wsm.state.get("history") or [])
        if isinstance(h, dict)
        and h.get("kind") == "review"
        and h.get("node_id") == review_node.get("id")
        and h.get("decision") == "reject"
    ]
    if rejects:
        lines.append("")
        lines.append("【历史审核意见】")
        for idx, item in enumerate(rejects, start=1):
            lines.append(f"第 {idx} 次驳回：{item.get('message') or ''}")
    return "\n".join(lines)


async def run_stage_review(
    *,
    web_terminal,
    wsm: WorkflowStateManager,
    sender,
    conversation_id: str,
    stage_info: Dict[str, Any],
    review_node: Dict[str, Any],
) -> Dict[str, Any]:
    """调审核智能体。异常兜底 = reject（定稿：视为驳回 + 请告知用户）。"""
    review_name = str(review_node.get("name") or "")
    if callable(sender):
        try:
            sender(
                "workflow_review_progress",
                {"conversation_id": conversation_id, "progress": {"stage": "start", "message": f"审核「{review_name}」开始"}},
            )
        except Exception:
            pass
    payload = build_review_payload(
        web_terminal=web_terminal, wsm=wsm, stage_info=stage_info, review_node=review_node,
    )
    definition = wsm.load_definition() or {}
    review_mode = definition.get("reviewMode") or "active"

    def _progress(progress: Dict[str, Any]) -> None:
        if callable(sender):
            try:
                sender("workflow_review_progress", {"conversation_id": conversation_id, "progress": progress})
            except Exception:
                pass

    try:
        agent = WorkflowReviewAgent(web_terminal=web_terminal)
        result = await agent.review(payload_text=payload, review_mode=review_mode, progress_cb=_progress)
    except Exception as exc:
        result = {
            "decision": "reject",
            "message": (
                f"审核智能体执行异常（{exc}）。本次按驳回处理：请告知用户审核服务可能异常；"
                "若属偶发，可稍后重新汇报本阶段。"
            ),
            "source": "workflow_review_agent",
        }
    if not isinstance(result, dict) or result.get("decision") not in ("pass", "reject"):
        result = {
            "decision": "reject",
            "message": "审核未产出有效结论。本次按驳回处理：请告知用户审核服务可能异常；若属偶发，可稍后重新汇报本阶段。",
            "source": "workflow_review_agent",
        }
    return result


# ---------------------------------------------------------------- 退出 / 通知文本


def deactivate_workflow(*, data_dir, conversation_id: str, reason: str, sender=None) -> Dict[str, Any]:
    """模型自主退出（deactivate_workflow 工具）：摘牌，工具返回闭环，不发 user 通知。"""
    if not conversation_id:
        return {"success": False, "error": "当前没有打开的对话。"}
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return {"success": False, "error": "当前对话没有激活的工作流。"}
    name = str(wsm.state.get("workflow_name") or "")
    wsm.deactivate(status=STATUS_STOPPED, reason=REASON_MODEL)
    # 摘牌后广播 {active: False} 快照（此时 progress_snapshot 只剩 active=False）：
    # 前端实时摘除快捷窗口卡片与 slash 菜单「进行中」状态，
    # 否则只能等下次刷新/切换对话静态校正才消失。
    emit_workflow_progress(wsm=wsm, sender=sender, conversation_id=conversation_id)
    note = str(reason or "").strip()
    return {
        "success": True,
        "message": f"工作流「{name}」已退出（{note or '模型自主退出'}）。工作流状态已摘牌，你可以继续自由工作。",
    }


def deactivate_workflow_by_user(*, data_dir, conversation_id: str) -> Dict[str, Any]:
    """用户 slash 退出（REST）：摘牌 + 柔性通知入池（忙时工具循环消费 / 闲时 REST 直发）。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return {"success": False, "error": "当前对话没有激活的工作流。"}
    name = str(wsm.state.get("workflow_name") or "")
    wsm.deactivate(status=STATUS_STOPPED, reason=REASON_USER)
    wsm.push_notice(
        notice_type="deactivated_by_user",
        message=(
            f"用户已退出工作流「{name}」。工作流已摘牌，无需继续按流程推进；"
            "你可以继续自由工作。若用户之后要求恢复，可重新激活。"
        ),
    )
    return {"success": True, "workflow_name": name}


def build_round_limit_notice(*, data_dir, conversation_id: str) -> Optional[str]:
    """max_stage_rounds 撞限通知文本（主循环层注入）。未撞限返回 None。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None or wsm.round_limit_notified():
        return None
    definition = wsm.load_definition() or {}
    max_rounds = int(definition.get("maxStageRounds") or 20)
    rounds = wsm.get_stage_rounds()
    if rounds < max_rounds:
        return None
    wsm.mark_round_limit_notified()
    current = wsm.get_node(wsm.get_current_node_id()) or {}
    return (
        f"工作流「{definition.get('name')}」的当前步骤「{current.get('name')}」已进行 {rounds} 轮，"
        f"达到单步轮数上限（{max_rounds}）。请立刻停下当前工作，告知用户已超过 {max_rounds} 轮，"
        "并询问是否还要继续。（工作流仍在进行中，等待用户决定；用户回复后可继续推进或退出。）"
    )


# ---------------------------------------------------------------- 状态查询 / 进度事件


def build_status_text(*, data_dir, conversation_id: str) -> str:
    """get_workflow_status 工具返回文本。"""
    wsm = get_active_manager(data_dir, conversation_id)
    if wsm is None:
        return "当前对话没有激活的工作流。"
    definition = wsm.load_definition() or {}
    current = wsm.get_node(wsm.get_current_node_id()) or {}
    lines: List[str] = [
        f"【工作流状态】{definition.get('name')}：{definition.get('description')}",
        f"已进行时长：{int(time.time() - float(wsm.state.get('started_at') or time.time()))} 秒",
        "",
        "【已完成的步骤】",
    ]
    stage_records = [h for h in (wsm.state.get("history") or []) if isinstance(h, dict) and h.get("kind") == "stage"]
    if stage_records:
        for item in stage_records:
            lines.append(f"- {item.get('name')}（{item.get('rounds') or 0} 轮）：{(item.get('summary') or '')[:80]}")
    else:
        lines.append("（暂无）")
    review_records = [h for h in (wsm.state.get("history") or []) if isinstance(h, dict) and h.get("kind") == "review"]
    if review_records:
        lines.append("")
        lines.append("【审核记录】")
        for item in review_records:
            label = "通过" if item.get("decision") == "pass" else "驳回"
            lines.append(f"- {item.get('name')}：{label} — {(item.get('message') or '')[:80]}")
    lines.append("")
    lines.append(_current_node_brief(wsm=wsm, current=current))
    lines.append(f"当前步骤已进行轮数：{wsm.get_stage_rounds()}")
    return "\n".join(lines)


def emit_workflow_progress(
    *,
    wsm: WorkflowStateManager,
    sender,
    conversation_id: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """广播工作流进度快照（sender → session_data → REST 轮询透传，对齐 goal 链路）。"""
    if not callable(sender) or not conversation_id:
        return
    snap = wsm.progress_snapshot()
    snap["conversation_id"] = conversation_id
    if extra:
        snap.update(extra)
    try:
        sender("workflow_progress", snap)
    except Exception:
        pass
