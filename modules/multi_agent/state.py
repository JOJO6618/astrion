"""多智能体会话状态机。

一个 MultiAgentState 绑定到一个多智能体对话的 conversation_id，维护：
- 已创建的子智能体实例（agent_id ↔ role_id ↔ display_name ↔ task_id ↔ status）
- 待插入到主对话的待发 user 消息队列（pending_master_messages）
- 主智能体工具调用 answer_sub_agent_question / answer_other_agent 写回答案的 futomap
- 子智能体调用 ask_master / ask_other_agent 时挂起的 futomap

关键约定（来自 .astrion/memory/multi_agent_mode_design.md）：
- 消息格式：`来自 {显示名} 的{类型}\\nid: {消息id}\\n\\n<{显示名}>\\n<{标签}>\\n{内容}\\n</{标签}>\\n</{显示名}>`
- 接收方决定插入方式：
  - 子智能体 ask 阻塞等待 → main 调 answer_* 返回到工具结果
  - 子智能体 idle 状态 → 主对话的 pending_master_messages 直接插入新轮 user 消息
  - 子智能体 running 中 → inline 插入到当前末尾（在下一轮 model 调用前合并 messages）
- 通信是「工具调用提问」+「回答返回到工具结果」；其他场景（输出/进度/完成/任务发布/消息/回答）
  才以 user 消息格式插入对话。
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from asyncio import AbstractEventLoop
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from modules.multi_agent.debug_logger import ma_debug

if TYPE_CHECKING:
    from modules.sub_agent.task import SubAgentTask

# ---------- 消息类型常量 ----------
TYPE_TASK = "Task"               # 主→子 任务发布
TYPE_OUTPUT = "Output"           # 子→主 进度/完成输出（统一）
TYPE_ASK = "Ask"                 # 子→主 / 子→子 提问
TYPE_ANSWER = "Answer"           # 主→子 / 子→子 回答（不插入对话，仅做工具结果）
TYPE_MESSAGE = "Message"         # 任意方向 消息
# 内部枚举到此

QUESTION_PREFIX_ASK_MASTER = "ask_master"
QUESTION_PREFIX_ASK_OTHER = "ask_other"


def format_multi_agent_message(
    *,
    display_name: str,
    msg_type: str,
    content: str,
    msg_id: Optional[str] = None,
    target: Optional[str] = None,
    extra_attrs: Optional[Dict[str, str]] = None,
    msg_type_text: Optional[str] = None,
    subtype: Optional[str] = None,
) -> str:
    """按统一格式构造 user 消息字符串。

    Args:
        display_name: 发出方显示名（如 UI Operator_1 / Team Leader）
        msg_type: 消息类型，对应上方 TYPE_* 常量
        content: 消息正文
        msg_id: 消息 id；不传则自动生成
        target: 接收方显示名（用于子→子 提问时标明对谁提问）
        extra_attrs: 额外标签属性（如 question_id="ask_xxx"）
        msg_type_text: 覆盖默认的中文消息类型文案（如"任务结束汇报"）
        subtype: 渲染/分类使用的子类型（如 progress_output / completion_report / ask_master）
    """
    if not msg_id:
        msg_id = f"msg_{uuid.uuid4().hex[:10]}"

    type_label = msg_type_text or msg_type_to_text(msg_type)
    # 第一行：自然语言前缀（含 target 标识）
    if target:
        prefix = f"来自 {display_name} 向 {target} 的{type_label}"
    else:
        prefix = f"来自 {display_name} 的{type_label}"

    # 第二行：id
    id_line = f"id: {msg_id}"

    # 属性 attr 字符串
    attrs = ""
    if target:
        attrs += f' target="{target}"'
    if subtype:
        attrs += f' subtype="{subtype}"'
    if extra_attrs:
        for k, v in extra_attrs.items():
            attrs += f' {k}="{v}"'

    # XML 包裹
    tag = msg_type
    xml = (
        f"<{display_name}>\n"
        f"<{tag}{attrs}>\n"
        f"{content}\n"
        f"</{tag}>\n"
        f"</{display_name}>"
    )

    return f"{prefix}\n{id_line}\n\n{xml}"


def msg_type_to_text(msg_type: str) -> str:
    """把 TYPE_* 转为中文短语，用于 prompt 前缀。"""
    mapping = {
        TYPE_TASK: "任务发布",
        TYPE_OUTPUT: "任务进度输出",
        TYPE_ASK: "提问",
        TYPE_ANSWER: "回答",
        TYPE_MESSAGE: "消息",
    }
    return mapping.get(msg_type, msg_type)


def build_master_dispatch_text(task: str, msg_id: Optional[str] = None) -> str:
    """主智能体发布任务时插入到子智能体对话的 user 消息文本。"""
    return format_multi_agent_message(
        display_name="Team Leader",
        msg_type=TYPE_TASK,
        content=task,
        msg_id=msg_id,
    )


# 子类型常量（用于前端渲染与后端分类）
SUBTYPE_PROGRESS_OUTPUT = "progress_output"
SUBTYPE_COMPLETION_REPORT = "completion_report"
SUBTYPE_ASK_MASTER = "ask_master"
SUBTYPE_ASK_OTHER = "ask_other"


def build_sub_agent_output_text(display_name: str, content: str, msg_id: Optional[str] = None, *, is_final: bool = False) -> str:
    """子智能体输出（进度或完成）插入到主对话的 user 消息文本。"""
    return format_multi_agent_message(
        display_name=display_name,
        msg_type=TYPE_OUTPUT,
        content=content,
        msg_id=msg_id,
        msg_type_text="任务结束汇报" if is_final else "任务进度输出",
        subtype=SUBTYPE_COMPLETION_REPORT if is_final else SUBTYPE_PROGRESS_OUTPUT,
    )


def build_sub_agent_ask_master_text(display_name: str, question: str, question_id: str) -> str:
    """子智能体向主智能体提问时插入到主对话的 user 消息文本。"""
    return format_multi_agent_message(
        display_name=display_name,
        msg_type=TYPE_ASK,
        content=question,
        msg_id=question_id,
        subtype=SUBTYPE_ASK_MASTER,
    )


def build_sub_agent_ask_other_text(
    display_name: str,
    target_display: str,
    question: str,
    question_id: str,
) -> str:
    """子智能体向另一个子智能体提问时插入到目标子智能体对话的文本。"""
    return format_multi_agent_message(
        display_name=display_name,
        msg_type=TYPE_ASK,
        content=question,
        msg_id=question_id,
        target=target_display,
        subtype=SUBTYPE_ASK_OTHER,
    )


_MULTI_AGENT_MESSAGE_RE = re.compile(
    r"^来自\s+(?P<display_name>.+?)\s+的(?P<type_text>.+?)\n"
    r"id:\s*(?P<msg_id>\S+)\n\n"
    r"<(?P=display_name)>\n"
    r"<(?P<tag>\w+)(?P<attrs>[^>]*)>\n"
    r"(?P<content>.*?)\n"
    r"</(?P=tag)>\n"
    r"</(?P=display_name)>$",
    re.DOTALL,
)


def parse_multi_agent_message(text: str) -> Optional[Dict[str, str]]:
    """解析标准多智能体消息格式。

    返回字段：display_name, type_text, msg_id, tag, subtype, content。
    若不是标准格式则返回 None。
    """
    if not text:
        return None
    m = _MULTI_AGENT_MESSAGE_RE.search(text)
    if not m:
        return None
    attrs = m.group("attrs") or ""
    subtype_match = re.search(r'subtype="([^"]+)"', attrs)
    return {
        "display_name": m.group("display_name").strip(),
        "type_text": m.group("type_text").strip(),
        "msg_id": m.group("msg_id").strip(),
        "tag": m.group("tag").strip(),
        "subtype": subtype_match.group(1) if subtype_match else "",
        "content": m.group("content"),
    }


def build_master_message_to_sub_agent(message: str, msg_id: Optional[str] = None) -> str:
    """主智能体 send_message_to_sub_agent 时插入子对话的 user 消息文本。"""
    return format_multi_agent_message(
        display_name="Team Leader",
        msg_type=TYPE_MESSAGE,
        content=message,
        msg_id=msg_id,
    )


def build_master_answer_to_sub_agent(
    display_name: str,
    target_display: str,
    answer: str,
    question_id: str,
) -> str:
    """主智能体回答插入到子对话（仅当子智能体 not waiting 或 idle 时走 user 消息路径）。"""
    return format_multi_agent_message(
        display_name=display_name,
        msg_type=TYPE_ANSWER,
        content=answer,
        msg_id=question_id,
        target=target_display,
        extra_attrs={"question_id": question_id},
    )


# ---------- 运行态状态机 ----------
@dataclass
class AgentInstance:
    """一个多智能体会话中已创建的子智能体实例。"""

    agent_id: int
    role_id: str
    display_name: str
    task_id: str
    status: str = "running"          # running / idle / terminated / failed / timeout
    summary: str = ""
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    last_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role_id": self.role_id,
            "display_name": self.display_name,
            "task_id": self.task_id,
            "status": self.status,
            "summary": self.summary,
            "created_at": self.created_at,
            "last_output": self.last_output,
        }


class MultiAgentState:
    """绑到一个 conversation_id 的多智能体运行态。

    线程安全：所有 pubic 方法均假设在 SubAgentManager 的事件循环线程中调用，
    或者由 chat task 主线程通过 manager 的 _run_coro 进入此循环。
    跨线程访问通过 manager._run_coro 桥接，避免直接调用。
    """

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        # agent_id 映射；同一会话里 agent_id 唯一
        self.agents: Dict[int, AgentInstance] = {}
        # task_id -> agent_id（便于在 SubAgentTask 完成时回写）
        self.task_id_to_agent_id: Dict[str, int] = {}
        # 主智能体待插入消息队列（每条都是字符串，由 chat task 取走）
        self.pending_master_messages: List[str] = []
        # ask_master / ask_other_agent 的等待 future
        # key = question_id, value = asyncio.Future (结果为 answer str 或 Exception)
        self.pending_questions: Dict[str, asyncio.Future] = {}
        # question_id -> 创建 future 时所在的事件循环，用于跨循环安全 set_result
        self.pending_question_loops: Dict[str, AbstractEventLoop] = {}
        # 回答早于 wait_for_answer 注册时先暂存
        self.pending_answers: Dict[str, str] = {}
        # 一个 agent 可能同时只阻塞在一个 ask 工具上（最简实现）
        # key = agent_id, value = question_id（表示当前 agent 正阻塞等待）
        self.agent_blocking_question: Dict[int, str] = {}
        # 主智能体通过 sleep(wait_sub_agent_output) 等待某个子智能体下一次输出
        # key = agent_id, value = asyncio.Future（结果为完整消息文本）
        self.output_waits: Dict[int, asyncio.Future] = {}
        # 角色实例计数：role_id -> 已成功创建实例使用的最大角色内编号
        # 角色内编号是显示名后缀（如 UI Operator_1），也是唯一对模型/用户暴露的编号；
        # 全局 agent_id 为内部实现细节，不对外暴露。
        # 采用 peek + commit 两步：创建失败不消耗编号，避免跳号。
        self.role_counters: Dict[str, int] = {}

    # ----- 创建/查询 -----
    def peek_agent_id_for_role(self, role_id: str) -> int:
        """预取指定角色的下一个角色内编号（不递增计数器）。

        创建子智能体时先 peek 构造显示名，创建成功后必须调
        commit_agent_id_for_role 提交；失败则不提交，编号不被消耗。
        """
        return self.role_counters.get(role_id, 0) + 1

    def commit_agent_id_for_role(self, role_id: str, seq: int) -> None:
        """创建成功后提交角色内编号（单调递增，不回退）。"""
        if seq > self.role_counters.get(role_id, 0):
            self.role_counters[role_id] = seq

    def get_instance_by_display_name(self, display_name: str) -> Optional[AgentInstance]:
        """按显示名（如 UI Operator_1）查找实例；精确匹配优先，大小写不敏感兜底。"""
        name = (display_name or "").strip()
        if not name:
            return None
        for a in self.agents.values():
            if a.display_name == name:
                return a
        lowered = name.lower()
        for a in self.agents.values():
            if a.display_name.lower() == lowered:
                return a
        return None

    def list_display_names(self) -> List[str]:
        """返回当前所有实例的显示名（用于错误提示）。"""
        return [a.display_name for a in self.agents.values()]

    def register_instance(self, instance: AgentInstance) -> None:
        if instance.agent_id in self.agents:
            raise ValueError(f"agent_id {instance.agent_id} 已存在")
        self.agents[instance.agent_id] = instance
        self.task_id_to_agent_id[instance.task_id] = instance.agent_id

    def get_instance(self, agent_id: int) -> Optional[AgentInstance]:
        return self.agents.get(agent_id)

    def get_instance_by_task_id(self, task_id: str) -> Optional[AgentInstance]:
        aid = self.task_id_to_agent_id.get(task_id)
        if aid is None:
            return None
        return self.agents.get(aid)

    def list_active(self) -> List[AgentInstance]:
        # failed 视为可复活状态，仍算活跃
        return [a for a in self.agents.values() if a.status in ("running", "idle", "failed")]

    def list_all(self) -> List[AgentInstance]:
        return list(self.agents.values())

    def mark_status(self, agent_id: int, status: str, last_output: str = "") -> None:
        a = self.agents.get(agent_id)
        if not a:
            return
        # 多智能体原则：terminated 是真正终结（吸收态），不允许被覆盖回 idle/running
        if a.status == "terminated" and status != "terminated":
            ma_debug(
                "mark_status_blocked_terminal",
                agent_id=agent_id,
                current_status=a.status,
                requested_status=status,
            )
            return
        a.status = status
        if last_output:
            a.last_output = last_output
        # 当子智能体进入终态/idle 时，立即取消 sleep(wait_sub_agent_output) 的等待
        if status in ("failed", "terminated", "idle"):
            self._cancel_output_wait(agent_id, status)

    def _cancel_output_wait(self, agent_id: int, status: str) -> None:
        """取消指定 agent 的 sleep 输出等待，并提示可用 send_message_to_sub_agent 重新激活。"""
        fut = self.output_waits.pop(agent_id, None)
        if not fut or fut.done():
            return
        inst = self.agents.get(agent_id)
        name = inst.display_name if inst else str(agent_id)
        if status == "terminated":
            msg = f"子智能体 {name} 已终止，无法继续等待输出。"
        elif status == "failed":
            msg = f"子智能体 {name} 已失败，无法继续等待输出。该子智能体可被复活，可用 send_message_to_sub_agent 重新激活。"
        else:
            msg = f"子智能体 {name} 已进入空闲状态，无法继续等待输出。可用 send_message_to_sub_agent 重新激活。"
        try:
            loop = fut.get_loop()
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(fut.set_exception, RuntimeError(msg))
                return
        except Exception:
            pass
        try:
            fut.set_exception(RuntimeError(msg))
        except Exception:
            pass

    # ----- 主对话注入 -----
    def push_master_message(self, message_text: str) -> None:
        """把一条 user 消息追加到主对话待插入队列。"""
        ma_debug(
            "state_push_master_message",
            conversation_id=self.conversation_id,
            state_id=id(self),
            queue_len_before=len(self.pending_master_messages),
            msg_preview=str(message_text)[:200],
        )
        self.pending_master_messages.append(message_text)

    def drain_master_messages(self) -> List[str]:
        """取出（清空）所有待插入主对话的消息。"""
        msgs = self.pending_master_messages
        self.pending_master_messages = []
        ma_debug(
            "state_drain_master_messages",
            conversation_id=self.conversation_id,
            state_id=id(self),
            drained_count=len(msgs),
            previews=[str(m)[:150] for m in msgs],
        )
        return msgs

    def has_pending_master_messages(self) -> bool:
        return len(self.pending_master_messages) > 0

    # ----- 等待子智能体输出（sleep wait_sub_agent_output） -----
    def register_output_wait(
        self, agent_id: int, loop: AbstractEventLoop
    ) -> asyncio.Future:
        """注册等待指定子智能体的下一次输出。

        如果 `pending_master_messages` 里已经有该子智能体的未消费输出，
        则立即消费并返回该消息；否则返回一个 future，由后续输出唤醒。
        """
        fut: asyncio.Future = loop.create_future()
        inst = self.agents.get(agent_id)
        if not inst:
            fut.set_exception(ValueError("未找到该子智能体"))
            return fut

        if inst.status in ("terminated", "failed"):
            fut.set_exception(
                RuntimeError(
                    f"子智能体 {inst.display_name} 当前状态为 {inst.status}，无法等待输出"
                )
            )
            return fut

        # 优先消费 pending_master_messages 里已有的未消费输出
        display_name = inst.display_name
        for i, msg in enumerate(self.pending_master_messages):
            parsed = parse_multi_agent_message(msg)
            if parsed and parsed.get("display_name") == display_name:
                self.pending_master_messages.pop(i)
                fut.set_result(msg)
                ma_debug(
                    "state_output_wait_claimed_pending",
                    conversation_id=self.conversation_id,
                    agent_id=agent_id,
                    display_name=display_name,
                )
                return fut

        # 没有未消费输出，注册等待
        self.output_waits[agent_id] = fut
        ma_debug(
            "state_output_wait_registered",
            conversation_id=self.conversation_id,
            agent_id=agent_id,
            display_name=display_name,
        )
        return fut

    def claim_output_wait(self, agent_id: int, message_text: str) -> bool:
        """如果该 agent 正被 sleep 等待输出，则把消息交给等待方并阻止进入主对话。"""
        fut = self.output_waits.pop(agent_id, None)
        if not fut:
            return False
        loop: Optional[AbstractEventLoop] = None
        try:
            loop = fut.get_loop()
        except Exception:
            pass
        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(fut.set_result, message_text)
                return True
            except Exception:
                pass
        try:
            fut.set_result(message_text)
        except Exception:
            pass
        return True

    # ----- 阻塞问答 -----
    async def wait_for_answer(self, question_id: str, agent_id: int, timeout: float = 600.0) -> str:
        """子智能体 ask_* 工具调用后阻塞等待答案。

        返回 answer 字符串；超时/取消抛 asyncio.TimeoutError 或 CancelledError。
        """
        # 如果回答已经提前到达，直接返回
        if question_id in self.pending_answers:
            return self.pending_answers.pop(question_id)
        if question_id in self.pending_questions:
            old_fut = self.pending_questions[question_id]
            try:
                old_loop = old_fut.get_loop()
                if old_loop.is_closed():
                    self.pending_questions.pop(question_id, None)
                    self.pending_question_loops.pop(question_id, None)
                else:
                    raise RuntimeError(f"question_id 已存在: {question_id}")
            except Exception:
                self.pending_questions.pop(question_id, None)
                self.pending_question_loops.pop(question_id, None)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending_questions[question_id] = fut
        self.pending_question_loops[question_id] = loop
        self.agent_blocking_question[agent_id] = question_id
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self.pending_questions.pop(question_id, None)
            self.pending_question_loops.pop(question_id, None)
            if self.agent_blocking_question.get(agent_id) == question_id:
                self.agent_blocking_question.pop(agent_id, None)

    async def _do_provide_answer(self, question_id: str, answer: str) -> bool:
        """在同 future 所属事件循环内设置结果。"""
        fut = self.pending_questions.get(question_id)
        if not fut or fut.done():
            return False
        try:
            fut.set_result(answer)
        except asyncio.InvalidStateError:
            return False
        return True

    def provide_answer(self, question_id: str, answer: str) -> bool:
        """主/其他子智能体 answer_* 工具调用时回写答案。

        返回 True 表示找到等待中的 future；False 表示无等待方或已超时。
        支持跨事件循环调用（例如主对话循环回答子智能体循环里的提问）。
        """
        ma_debug(
            "state_provide_answer",
            question_id=question_id,
            has_pending=question_id in self.pending_questions,
            answer_preview=str(answer)[:300],
        )
        # 如果 wait_for_answer 还没注册，先把答案暂存
        if question_id not in self.pending_questions:
            self.pending_answers[question_id] = answer
            return True
        fut = self.pending_questions.get(question_id)
        if not fut or fut.done():
            self.pending_answers[question_id] = answer
            return False
        loop = self.pending_question_loops.get(question_id)
        if loop is None:
            try:
                loop = fut.get_loop()
            except Exception:
                pass
        if loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._do_provide_answer(question_id, answer), loop)
                return True
            except Exception:
                pass
        # 同循环回退（future 所属循环可能已关闭，失败时把答案暂存，避免阻塞方永远等不到）
        try:
            return asyncio.run_coroutine_threadsafe(self._do_provide_answer(question_id, answer), asyncio.get_event_loop()).result(timeout=5)
        except Exception:
            self.pending_questions.pop(question_id, None)
            self.pending_question_loops.pop(question_id, None)
            self.pending_answers[question_id] = answer
            return True

    def is_agent_blocking(self, agent_id: int) -> bool:
        return agent_id in self.agent_blocking_question

    def get_blocking_question_id(self, agent_id: int) -> Optional[str]:
        return self.agent_blocking_question.get(agent_id)

    def cancel_pending_question_for_agent(self, agent_id: int) -> bool:
        """取消指定子智能体正在等待的问答 future，用于软停止时解除阻塞。"""
        question_id = self.agent_blocking_question.pop(agent_id, None)
        if not question_id:
            return False
        fut = self.pending_questions.pop(question_id, None)
        loop = self.pending_question_loops.pop(question_id, None)
        if fut and not fut.done():
            try:
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(fut.cancel)
                else:
                    fut.cancel()
            except Exception:
                pass
        if fut:
            return True
        return False

    # ----- 持久化（最简版） -----
    def to_snapshot(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "agents": [a.to_dict() for a in self.agents.values()],
            "role_counters": self.role_counters,
        }

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any]) -> "MultiAgentState":
        state = cls(conversation_id=snapshot.get("conversation_id", ""))
        state.role_counters = dict(snapshot.get("role_counters") or {})
        for a_data in snapshot.get("agents") or []:
            a = AgentInstance(**a_data)
            state.agents[a.agent_id] = a
            if a.task_id:
                state.task_id_to_agent_id[a.task_id] = a.agent_id
        return state

    def clear(self) -> None:
        """清空所有运行态：实例、pending消息、阻塞问答、角色计数。

        用于用户连续第二下按停止按钮时，强制丢弃所有运行中/半成品状态。
        """
        # 取消所有阻塞中的 future，避免泄漏
        for qid, fut in list(self.pending_questions.items()):
            loop = self.pending_question_loops.get(qid)
            if loop and not loop.is_closed() and not fut.done():
                try:
                    loop.call_soon_threadsafe(fut.cancel)
                except Exception:
                    pass
        for fut in list(self.output_waits.values()):
            if not fut.done():
                try:
                    loop = fut.get_loop()
                    if loop and not loop.is_closed():
                        loop.call_soon_threadsafe(fut.cancel)
                    else:
                        fut.cancel()
                except Exception:
                    pass
        self.output_waits.clear()
        self.agents.clear()
        self.task_id_to_agent_id.clear()
        self.pending_master_messages.clear()
        self.pending_questions.clear()
        self.pending_question_loops.clear()
        self.pending_answers.clear()
        self.agent_blocking_question.clear()
        self.role_counters.clear()


# ----------------------------------------------------------------------
# 进程级全局注册表（所有 SubAgentManager 共享）
# ----------------------------------------------------------------------
# MultiAgentState 是会话级运行态，本质上不属于任何单个 SubAgentManager。
# 历史上它存放在 manager.multi_agent_states（manager 实例属性）里，而对话级
# terminal 缓存重建会产生多个 manager；每个 manager 的 _load_state 都从磁盘快照
# from_snapshot 恢复一份副本，导致同一对话的 MultiAgentState 在内存中同时存在
# N 份（terminate 只标记了其中一份，其余副本仍是陈旧 idle，前端轮询落到哪份
# 就看到哪份的状态）。现在全进程共享同一份注册表，任何 manager 读写的都是同一
# 对象，从根上消除多副本分裂。
GLOBAL_MULTI_AGENT_STATES: Dict[str, "MultiAgentState"] = {}
# get-or-create / drop / _load_state restore 的 check-then-act 需要互斥；
# 用 RLock 防止与调用方已有锁重入死锁。
GLOBAL_MULTI_AGENT_STATES_LOCK = threading.RLock()