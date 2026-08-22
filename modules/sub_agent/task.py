from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

_QUESTION_ID_RE = re.compile(r"^id:\s*(\S+)", re.MULTILINE)

from modules.sub_agent.toolkit import (
    SUB_AGENT_TOOLS,
    FINISH_TOOL,
    _format_tool_result,
    _build_sub_agent_profile,
)
from utils.api_client import APIClient
from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.sub_agent.manager import SubAgentManager
    from modules.multi_agent.state import MultiAgentState

from modules.multi_agent.debug_logger import ma_debug

logger = setup_logger(__name__)

# 多智能体模式下额外加载的工具定义
def _load_multi_agent_sub_agent_tools() -> List[Dict[str, Any]]:
    try:
        from modules.multi_agent.tools import build_sub_agent_tools_for_role
        return build_sub_agent_tools_for_role()
    except Exception as exc:
        logger.warning(f"[SubAgentTask] 加载多智能体工具失败: {exc}")
        return []


class SubAgentTask:
    """单个后台子智能体任务。"""

    def __init__(
        self,
        manager: "SubAgentManager",
        task_record: Dict[str, Any],
        task_message: str,
        system_prompt: str,
        model_key: Optional[str],
        thinking_mode: Optional[str],
        *,
        multi_agent_mode: bool = False,
        multi_agent_state: Optional["MultiAgentState"] = None,
        display_name: Optional[str] = None,
    ):
        self.manager = manager
        self.task_record = task_record
        self.task_message = task_message
        self.system_prompt = system_prompt
        self.model_key = model_key
        self.thinking_mode = thinking_mode or "fast"
        self.task_id = task_record["task_id"]
        self.agent_id = task_record["agent_id"]
        raw_timeout = task_record.get("timeout_seconds")
        self.timeout_seconds = int(raw_timeout) if raw_timeout is not None else None
        self.deliverables_dir = Path(task_record["deliverables_dir"])

        self.output_file = Path(task_record["output_file"])
        self.stats_file = Path(task_record["stats_file"])
        self.progress_file = Path(task_record["progress_file"])
        self.conversation_file = Path(task_record["conversation_file"])
        # system_prompt.txt 路径（冻结的 system prompt，压缩后从此重建）
        self.system_prompt_file = Path(task_record.get("task_root", "")) / "system_prompt.txt"

        # 上下文压缩配置
        # 默认阈值 150k tokens，可由外部覆盖（如个人空间子智能体管理配置）
        self.compress_threshold_tokens: int = int(task_record.get("compress_threshold_tokens") or 150_000)
        # 最大执行轮次（对应个人空间子智能体设置项 sub_agent_max_turns，仅传统模式生效）：
        # 未设置 → 默认 50；0（或负数）→ None 表示无上限；正整数 → 该值
        # 多智能体模式的子智能体是长期协作成员，一律无上限，不受该设置约束
        if multi_agent_mode:
            self.max_turns: Optional[int] = None
        else:
            _raw_max_turns = task_record.get("max_turns")
            if _raw_max_turns is None:
                self.max_turns = 50
            else:
                _max_turns_int = int(_raw_max_turns)
                self.max_turns = _max_turns_int if _max_turns_int > 0 else None
        self.current_context_tokens: int = 0
        self._compress_round: int = 0

        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_message},
        ]
        self.stats = {
            "runtime_start": time.time() * 1000,
            "runtime_seconds": 0,
            "files_read": 0,
            "write_files": 0,
            "edit_files": 0,
            "searches": 0,
            "web_pages": 0,
            "commands": 0,
            "api_calls": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0},
            "current_context_tokens": 0,
            "compress_round": 0,
        }
        self._stdout_lines: List[str] = []
        self._cancelled = False
        self._task: Optional[asyncio.Task] = None
        # 软停止标志：设为 True 后，子智能体会在当前工具调用完成后
        # 停止执行后续工具，保存当前进度并进入 idle 状态（不 terminate）
        self._soft_stop = False

        # 多智能体模式相关字段
        self.multi_agent_mode = bool(multi_agent_mode)
        self.multi_agent_state = multi_agent_state
        # display_name 不传时回退为 'Agent_{self.agent_id}'
        self.display_name = display_name or f"Agent_{self.agent_id}"
        # 多智能体运行期控制
        # 使用 asyncio.Event 在子智能体自己的事件循环内等待；
        # inject_message 可能跨线程调用，通过 loop.call_soon_threadsafe 唤醒。
        self._continue_event: Optional[asyncio.Event] = None
        self._idle = False
        self._pending_answer_question_id: Optional[str] = None
        self._answered_question_ids: Set[str] = set()
        # 纯上下文通知（不触发新一轮工作）延迟队列：当上下文末尾处于
        # assistant tool_calls 与 tool 结果之间时，直接 append 会违反 API 的
        # tool 消息顺序约束，先入队，由主循环在安全点 _flush_pending_notifications 写入。
        self._pending_notifications: List[str] = []

    def emit(self, type_: str, data: Dict[str, Any]) -> None:
        """输出一行 JSONL 到 progress 文件并缓存。"""
        line = json.dumps({"type": type_, **data}, ensure_ascii=False)
        self._stdout_lines.append(line)
        try:
            self.progress_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.progress_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    async def run(self) -> None:
        """主 LLM 循环。"""
        # 在子智能体自己的事件循环内初始化 asyncio.Event
        self._continue_event = asyncio.Event()
        try:
            await self._run_loop()
        except asyncio.CancelledError:
            if self._soft_stop:
                # 软停止：中断当前工作，从对话文件恢复，重新进入 idle 等待循环
                ma_debug(
                    "sub_agent_soft_stop_cancelled",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                )
                await asyncio.shield(self._recover_from_soft_stop())
                # 不 raise，不 return——重新进入 idle 等待后续消息
                # 注意：不能清除 self._task 引用！inject_message 跨线程唤醒时
                # 需要通过 self._task.get_loop() 获取事件循环来 call_soon_threadsafe。
                # 当前 task 虽被 cancel 但 CancelledError 被捕获且未 re-raise，
                # task 会继续执行直到 run() return，此期间 self._task 仍有效。
                # 进入 idle 等待循环，直到被再次唤醒或取消
                try:
                    await asyncio.shield(self._idle_wait_loop())
                except asyncio.CancelledError:
                    # 二次取消视为硬取消
                    self._cancelled = True
                    self._persist_conversation(partial_summary="子智能体被手动终止")
                    self._write_terminated_snapshot()
                    raise
                return
            # 硬取消（terminate）路径
            self._cancelled = True
            logger.debug(f"[SubAgent] task={self.task_id} 被取消")
            ma_debug("sub_agent_run_cancelled", task_id=self.task_id, agent_id=self.agent_id, display_name=self.display_name)
            # 写 terminated 终态快照而不是 failed：terminated 是吸收态，
            # 避免 output.json 的 failed 被其他 manager 读回后走复活路径
            self._persist_conversation(partial_summary="子智能体被手动终止")
            self._write_terminated_snapshot()
            raise
        except Exception as exc:
            logger.exception(f"[SubAgent] task={self.task_id} 执行异常")
            ma_debug("sub_agent_run_exception", task_id=self.task_id, agent_id=self.agent_id, display_name=self.display_name, error=str(exc))
            await self._write_failure(f"执行异常: {exc}")

    async def _run_loop(self) -> None:
        client, model_key = self._build_client()
        if self.multi_agent_mode:
            tools = list(SUB_AGENT_TOOLS)
            tools.extend(_load_multi_agent_sub_agent_tools())
            # 多智能体模式下不要求 finish_task，自然输出结束即进入 idle，可继续接收消息
        else:
            tools = list(SUB_AGENT_TOOLS)
            tools.append(FINISH_TOOL)

        start_time = time.time()
        max_turns = self.max_turns  # None 表示无上限（个人空间可配置，默认 50）
        turn = 0

        while not self._cancelled:
            elapsed = time.time() - start_time
            if self.timeout_seconds is not None and elapsed > self.timeout_seconds:
                await self._write_timeout(elapsed)
                return

            # 跨 manager 控制信号：terminate/soft_stop 可能由另一个 manager 经
            # control.json 下达（其实例无法直接操作本实例），每个 tick 检查一次
            control_action = self._consume_control_request()
            if control_action == "terminate":
                self._cancelled = True
                ma_debug(
                    "sub_agent_control_terminate",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                )
                if self.multi_agent_state:
                    self.multi_agent_state.mark_status(self.agent_id, "terminated")
                self._persist_conversation(partial_summary="子智能体已被手动终止")
                self._write_terminated_snapshot()
                return
            if control_action == "soft_stop" and not self._idle:
                ma_debug(
                    "sub_agent_control_soft_stop",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                )
                self._soft_stop = True

            # 多智能体模式下，idle 时等待新消息或外部回答；只有真正被注入消息时才继续运行
            if self.multi_agent_mode and self._idle:
                event_set = False
                try:
                    if self._continue_event is None:
                        self._continue_event = asyncio.Event()
                    await asyncio.wait_for(self._continue_event.wait(), timeout=1.0)
                    event_set = True
                except asyncio.TimeoutError:
                    pass
                if self._cancelled:
                    break
                if not event_set:
                    # 只是周期性检查取消状态，没有新消息，保持 idle 继续等待
                    continue
                self._continue_event.clear()
                ma_debug(
                    "sub_agent_idle_wake",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    pending_messages=[m.get("role") for m in self.messages[-3:]],
                )
                self._idle = False
                # 关键修复：从 idle 唤醒后必须同步更新 MultiAgentState 状态为 running，
                # 否则 has_running_multi_agent 会误判为 false，导致主对话提前进入空闲、
                # 后续子智能体输出无法推送到主智能体。
                if self.multi_agent_state:
                    self.multi_agent_state.mark_status(self.agent_id, "running")
                    ma_debug(
                        "sub_agent_idle_wake_mark_running",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                    )
                continue

            turn += 1
            if max_turns is not None and turn > max_turns:
                await self._write_failure("任务执行超过最大轮次限制", max_turns_exceeded=True)
                return

            self.stats["api_calls"] += 1
            self.stats["turn_count"] = turn
            self.stats["runtime_seconds"] = int(elapsed)
            self.emit("stats", {**self.stats, "turn_count": turn})

            # 运行期间实时持久化 stats，供 get_sub_agent_status 查询
            try:
                self.stats_file.parent.mkdir(parents=True, exist_ok=True)
                _stats_snapshot = {**self.stats, "runtime_seconds": int(elapsed), "turn_count": turn}
                self.stats_file.write_text(json.dumps(_stats_snapshot, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass

            # 多智能体模式：在模型调用前识别是否有待回答的提问
            if self.multi_agent_mode:
                self._pending_answer_question_id = self._peek_pending_question_id()

            # 调试：记录进入本轮模型调用前的上下文摘要
            ma_debug(
                "sub_agent_model_call_start",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
                turn=turn,
                message_count=len(self.messages),
                last_user_message=self.messages[-1].get("content", "")[:300] if self.messages and self.messages[-1].get("role") == "user" else "",
                pending_answer_question_id=self._pending_answer_question_id,
            )

            # 安全点：写入延迟的上下文通知（不触发新一轮工作，仅让下一轮模型调用看到）
            self._flush_pending_notifications()

            assistant_message, reasoning, tool_calls, usage = await self._call_model(client, model_key, tools)
            if usage:
                self._apply_usage(usage)

            # 上下文压缩检查：超过阈值时触发深度压缩
            if self.current_context_tokens > 0 and self.current_context_tokens >= self.compress_threshold_tokens:
                compressed = self._deep_compress_messages()
                if compressed:
                    ma_debug(
                        "sub_agent_deep_compressed",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                        display_name=self.display_name,
                        tokens_before=self.current_context_tokens,
                        messages_before=len(self.messages) + 1,  # +1 因为本转 assistant 还没 append
                        messages_after=len(self.messages),
                        compress_round=self._compress_round,
                    )

            # 多智能体模式：把 assistant 文本输出作为进度/完成 output 转发到主对话
            if self.multi_agent_mode and self.multi_agent_state and assistant_message.strip():
                self._forward_output_to_master(assistant_message, is_final=not tool_calls)

            final_message: Dict[str, Any] = {"role": "assistant", "content": assistant_message}
            if reasoning:
                final_message["reasoning_content"] = reasoning
            if tool_calls:
                final_message["tool_calls"] = tool_calls
            self.messages.append(final_message)
            self._persist_conversation(partial_summary=assistant_message[:200])

            if not tool_calls:
                # 多智能体模式：没有 tool_calls 表示本轮结束，进入 idle 等待
                if self.multi_agent_mode:
                    self._mark_idle()
                    self._idle = True
                    self._persist_conversation(partial_summary=assistant_message[:200])
                    continue
                # 普通模式：prompt 并要求继续 / finish_task
                self.messages.append({
                    "role": "user",
                    "content": "如果你已经完成了任务，请调用 finish_task 工具提交完成报告。如果还没有完成，请继续执行任务。",
                })
                continue

            for tool_call in tool_calls:
                if self._cancelled:
                    break
                # 软停止：如果检测到 _soft_stop，给未执行的工具调用插入取消结果并跳出
                if self._soft_stop:
                    ma_debug(
                        "sub_agent_soft_stop_break",
                        task_id=self.task_id,
                        agent_id=self.agent_id,
                        display_name=self.display_name,
                        skipped_tool=tool_call.get("function", {}).get("name", ""),
                    )
                    # 给未执行的工具调用补上“被取消”的 tool result，保持消息结构合法
                    cancelled_progress_id = tool_call.get("id") or f"tool_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
                    self.emit("progress", {"id": cancelled_progress_id, "tool": tool_call.get("function", {}).get("name", ""), "status": "cancelled", "args": self._parse_args(tool_call), "ts": int(time.time() * 1000)})
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", cancelled_progress_id),
                        "content": "⦶ 工具执行被取消（用户手动停止）",
                    })
                    self._persist_conversation(partial_summary=assistant_message[:200])
                    break
                name = tool_call.get("function", {}).get("name", "")
                args = self._parse_args(tool_call)
                progress_id = tool_call.get("id") or f"tool_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

                if name == "finish_task":
                    await self._write_finish(args, elapsed)
                    return

                self.emit("progress", {"id": progress_id, "tool": name, "status": "running", "args": args, "ts": int(time.time() * 1000)})
                result = await self._execute_tool(name, args)
                self.emit("progress", {"id": progress_id, "tool": name, "status": "completed" if result.get("success") else "failed", "args": args, "ts": int(time.time() * 1000)})

                self._update_stats(name)

                content = _format_tool_result(name, result)
                if name == "read_mediafile" and result.get("success"):
                    content = self._build_media_tool_content(result) or content
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", progress_id),
                    "content": content,
                })
                self._persist_conversation(partial_summary=assistant_message[:200])

            # 软停止：工具循环结束后进入 idle，不 terminate
            if self._soft_stop and not self._cancelled:
                ma_debug(
                    "sub_agent_soft_stop_idle",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                )
                # 重置软停止标志，下次 Team Leader 可以重新发消息唤醒
                self._soft_stop = False
                self._mark_idle()
                self._idle = True
                self._persist_conversation(partial_summary=assistant_message[:200])
                continue

        # 循环结束（取消或 idle 被外部终止）后的清理
        if self.multi_agent_mode and self._cancelled:
            if self.multi_agent_state:
                self.multi_agent_state.mark_status(self.agent_id, "terminated")

    def _forward_output_to_master(self, output_text: str, *, is_final: bool = False) -> None:
        """把子智能体的 assistant 文本输出转发成主对话的 user 消息，并写入进度文件供前端查看。"""
        ma_debug(
            "sub_agent_forward_output_enter",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            multi_agent_mode=self.multi_agent_mode,
            has_state=bool(self.multi_agent_state),
            state_id=id(self.multi_agent_state) if self.multi_agent_state else None,
            output_len=len(output_text),
            is_final=is_final,
        )
        if not self.multi_agent_state:
            ma_debug("sub_agent_forward_no_state", task_id=self.task_id, agent_id=self.agent_id)
            return
        # 如果这是对 pending 提问的回答，不走主对话转发，而是返回到 ask 工具结果
        if self._provide_answer(output_text):
            return
        try:
            from modules.multi_agent.state import build_sub_agent_output_text
            msg = build_sub_agent_output_text(self.display_name, output_text.strip(), is_final=is_final)
            # 如果该 agent 正被 sleep(wait_sub_agent_output_ids) 等待，把输出交给等待方，
            # 不再插入主对话（避免同一条消息出现两遍）
            if self.multi_agent_state.claim_output_wait(self.agent_id, msg):
                ma_debug(
                    "sub_agent_output_claimed_by_wait",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    msg_preview=msg[:200],
                )
                # 同时记录到实例状态，供 list_active_sub_agents 使用
                inst = self.multi_agent_state.get_instance(self.agent_id)
                if inst:
                    inst.last_output = output_text[:500]
                self.emit("progress", {
                    "subtype": "output",
                    "content": output_text,
                    "is_final": is_final,
                    "ts": int(time.time() * 1000),
                })
                return
            self.multi_agent_state.push_master_message(msg)
            ma_debug(
                "sub_agent_forward_pushed",
                task_id=self.task_id,
                agent_id=self.agent_id,
                state_id=id(self.multi_agent_state) if self.multi_agent_state else None,
                msg_preview=msg[:200],
            )
            # 同时记录到实例状态，供 list_active_sub_agents 使用
            inst = self.multi_agent_state.get_instance(self.agent_id)
            if inst:
                inst.last_output = output_text[:500]
            # 写入进度文件，前端子智能体进度弹窗可直接展示
            self.emit("progress", {
                "subtype": "output",
                "content": output_text,
                "is_final": is_final,
                "ts": int(time.time() * 1000),
            })
            ma_debug(
                "sub_agent_output_forwarded",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
                is_final=is_final,
                content_preview=output_text[:300],
            )
        except Exception as exc:
            logger.warning(f"[SubAgentTask] forward output to master failed: {exc}")

    def _mark_idle(self) -> None:
        """多智能体模式下，子智能体自然结束即本轮任务结束，进入 idle 状态。"""
        ma_debug(
            "sub_agent_mark_idle",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
        )
        if self.multi_agent_state:
            self.multi_agent_state.mark_status(self.agent_id, "idle")

    def inject_notification(self, text: str) -> None:
        """注入纯上下文通知（user 消息），不唤醒 idle、不触发新一轮工作。

        与 inject_message 的核心区别：不 set _continue_event。
        - idle 的子智能体保持 idle，通知在其下一次被真正消息唤醒时随上下文看到；
        - 运行中的子智能体在下一轮模型调用前看到（见 _flush_pending_notifications）；
        - 若末尾消息处于 tool 序列中（assistant tool_calls / tool），改为入队延迟写入，
          避免产生 tool 消息顺序非法的上下文。
        用途：执行环境变更等「需要知道但不需要行动」的运行期通知。
        """
        content = str(text or "").strip()
        if not content:
            return
        last = self.messages[-1] if self.messages else None
        mid_tool_sequence = bool(
            last is not None
            and (last.get("role") == "tool" or (last.get("role") == "assistant" and last.get("tool_calls")))
        )
        if mid_tool_sequence:
            self._pending_notifications.append(content)
        else:
            self.messages.append({"role": "user", "content": content})
        ma_debug(
            "sub_agent_notification_injected",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            queued=mid_tool_sequence,
            was_idle=self._idle,
            message_preview=content[:300],
        )

    def _flush_pending_notifications(self) -> None:
        """在主循环安全点（下一轮模型调用前）把延迟通知写入上下文。

        到达安全点时工具循环已结束：正常完成会写完全部 tool 结果，软停止会为跳过的
        tool_call 补「被取消」结果，取消则直接退出循环——因此末尾不会存在悬空的
        assistant tool_calls，append user 消息序列合法。
        """
        if not self._pending_notifications:
            return
        for content in self._pending_notifications:
            self.messages.append({"role": "user", "content": content})
        count = len(self._pending_notifications)
        self._pending_notifications.clear()
        ma_debug(
            "sub_agent_notifications_flushed",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            count=count,
        )

    def inject_message(self, message_text: str) -> None:
        """外部向子智能体上下文插入 user 消息，并唤醒 idle 状态。"""
        self.messages.append({"role": "user", "content": message_text})
        ma_debug(
            "sub_agent_message_injected",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            message_preview=str(message_text)[:500],
            was_idle=self._idle,
        )
        # inject_message 可能从其他线程（主对话线程）调用，需要线程安全唤醒。
        # 优先使用子智能体 Task 所属事件循环投递 set()，避免跨线程直接操作 Future。
        if self._continue_event is None:
            self._continue_event = asyncio.Event()
        if self._task is not None:
            try:
                task_loop = self._task.get_loop()
                if task_loop.is_running():
                    task_loop.call_soon_threadsafe(self._continue_event.set)
                    return
            except Exception:
                pass
        self._continue_event.set()

    def _peek_pending_question_id(self) -> Optional[str]:
        """检查最后一条 user 消息是否是向本智能体提问，返回 question_id。"""
        if not self.multi_agent_mode or not self.messages:
            return None
        for msg in reversed(self.messages):
            if msg.get("role") == "user":
                content = msg.get("content") or ""
                if "的提问" in content:
                    m = _QUESTION_ID_RE.search(content)
                    if m:
                        qid = m.group(1)
                        if qid not in self._answered_question_ids:
                            return qid
                break
        return None

    def _provide_answer(self, output_text: str) -> bool:
        """如果当前输出是对 pending 提问的回答，把回答写回 future 并阻止转发到主对话。"""
        if not self._pending_answer_question_id or not self.multi_agent_state:
            return False
        self.multi_agent_state.provide_answer(
            self._pending_answer_question_id,
            output_text.strip(),
        )
        self._answered_question_ids.add(self._pending_answer_question_id)
        self._pending_answer_question_id = None
        return True

    def _build_client(self) -> tuple:
        """加载模型配置并初始化 APIClient。"""
        config_path = self.manager.models_config_file
        models: List[Dict[str, Any]] = []
        default_key = ""
        if Path(config_path).exists():
            try:
                raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
                models = raw.get("models", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
                default_key = str(raw.get("default_model", "")) if isinstance(raw, dict) else ""
            except Exception as exc:
                logger.error(f"[SubAgent] 加载模型配置失败: {exc}")

        model_map = {}
        valid_models = []
        for item in models:
            profile = _build_sub_agent_profile(item)
            if profile:
                key = profile["name"]
                model_map[key] = profile
                valid_models.append(key)

        chosen_key = self.model_key or default_key
        if chosen_key not in model_map and valid_models:
            chosen_key = valid_models[0]
        if chosen_key not in model_map:
            raise RuntimeError(f"未找到可用子智能体模型配置: {config_path}")

        client = APIClient(thinking_mode=(self.thinking_mode == "thinking"), web_mode=True)
        client.model_key = chosen_key
        client.project_path = str(self.manager.project_path)
        client.apply_profile(model_map[chosen_key])
        return client, chosen_key

    async def _call_model(
        self,
        client: APIClient,
        model_key: str,
        tools: List[Dict[str, Any]],
    ) -> tuple:
        """调用模型并解析 assistant 消息。"""
        assistant_message = ""
        reasoning = ""
        tool_calls: List[Dict[str, Any]] = []
        usage = None

        async for chunk in client.chat(self.messages, tools=tools, stream=True):
            if self._soft_stop:
                # 软停止：优雅中断当前模型调用，后续由 _run_loop 丢弃半成品并进入 idle
                break
            if self._cancelled:
                # 硬取消：立即抛出 CancelledError，确保不会返回半成品的 tool_calls 继续执行
                raise asyncio.CancelledError()
            if chunk.get("error"):
                raise RuntimeError(f"API 调用失败: {chunk.get('error')}")
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if delta.get("content"):
                assistant_message += delta["content"]
            if delta.get("reasoning_content"):
                reasoning += delta["reasoning_content"]
            elif delta.get("reasoning_details"):
                rd = delta["reasoning_details"]
                if isinstance(rd, list):
                    reasoning += "".join(str(d.get("text") or "") for d in rd)
                elif isinstance(rd, str):
                    reasoning += rd
                elif isinstance(rd, dict):
                    reasoning += str(rd.get("text") or "")

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index")
                if idx is None:
                    continue
                while len(tool_calls) <= idx:
                    tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                existing = tool_calls[idx]
                if tc.get("id"):
                    existing["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    existing["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    existing["function"]["arguments"] += fn["arguments"]

            if chunk.get("usage"):
                usage = chunk["usage"]

        return assistant_message, reasoning, tool_calls, usage

    def _parse_args(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        raw = tool_call.get("function", {}).get("arguments") or "{}"
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw": raw}

    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """通过 manager 调用主进程执行工具。
        
        多智能体模式下，对于通信工具（ask_master / ask_other_agent / answer_other_agent/
        list_active_sub_agents）在主进程内直接处理，不再转发到 WebTerminal。
        """
        if self.multi_agent_mode and self.multi_agent_state:
            result = await self._execute_multi_agent_tool(name, args)
            if result is not None:
                return result
        return await self.manager.execute_tool_for_sub_agent(name, args, agent_id=self.agent_id)

    async def _execute_multi_agent_tool(self, name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理多智能体模式专属的通信工具。返回 None 表示不 属于多智能体工具。"""
        state = self.multi_agent_state
        if not state:
            return None
        try:
            if name == "ask_master":
                question = str(args.get("question") or "").strip()
                question_id = str(args.get("question_id") or f"ask_master_{uuid.uuid4().hex[:10]}")
                ma_debug(
                    "sub_agent_tool_ask_master",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    question_id=question_id,
                    question=question[:500],
                )
                if not question:
                    return {"success": False, "error": "question 不能为空"}
                # 插入到主对话
                from modules.multi_agent.state import build_sub_agent_ask_master_text
                msg = build_sub_agent_ask_master_text(self.display_name, question, question_id)
                state.push_master_message(msg)
                inst = state.get_instance(self.agent_id)
                if inst:
                    inst.last_output = f"[ask_master] {question[:200]}"
                # 阻塞等待回答（状态标为正在等待主智能体回答）
                state.mark_status(self.agent_id, "running")
                try:
                    answer = await state.wait_for_answer(question_id, self.agent_id, timeout=float(args.get("timeout_seconds") or 600))
                except asyncio.CancelledError:
                    # 软停止取消了等待 future，返回取消结果而非抛 CancelledError
                    return {"success": False, "error": "等待回答被取消（软停止）", "question_id": question_id}
                state.mark_status(self.agent_id, "running")
                return {"success": True, "answer": answer, "question_id": question_id}

            if name == "ask_other_agent":
                target_id = int(args.get("target_agent_id") or 0)
                question = str(args.get("question") or "").strip()
                question_id = str(args.get("question_id") or f"ask_other_{uuid.uuid4().hex[:10]}")
                ma_debug(
                    "sub_agent_tool_ask_other_agent",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    target_agent_id=target_id,
                    question_id=question_id,
                    question=question[:500],
                )
                if not target_id or not question:
                    return {"success": False, "error": "参数缺失"}
                # 查找目标实例
                target_inst = state.get_instance(target_id)
                if not target_inst:
                    return {"success": False, "error": f"agent {target_id} 不存在"}
                # 构造提问消息并插入到目标子对话；同时要求其在下一轮调用 answer_other_agent
                from modules.multi_agent.state import build_sub_agent_ask_other_text
                target_display = target_inst.display_name
                msg = build_sub_agent_ask_other_text(self.display_name, target_display, question, question_id)
                self.manager.inject_message_to_sub_agent(target_id, msg)
                # 阻塞等待回答
                try:
                    answer = await state.wait_for_answer(question_id, self.agent_id, timeout=float(args.get("timeout_seconds") or 600))
                except asyncio.CancelledError:
                    return {"success": False, "error": "等待回答被取消（软停止）", "question_id": question_id}
                return {"success": True, "answer": answer, "question_id": question_id}

            if name == "answer_other_agent":
                source_id = int(args.get("source_agent_id") or 0)
                question_id = str(args.get("question_id") or "")
                answer = str(args.get("answer") or "").strip()
                if not question_id or not answer:
                    return {"success": False, "error": "参数缺失"}
                ok = state.provide_answer(question_id, answer)
                return {"success": bool(ok), "question_id": question_id}

            if name == "list_active_sub_agents":
                return {"success": True, "agents": [a.to_dict() for a in state.list_all()]}
        except asyncio.TimeoutError:
            return {"success": False, "error": "等待回答超时", "question_id": args.get("question_id")}
        except Exception as exc:
            logger.exception(f"[SubAgent] 多智能体工具异常: {name}")
            return {"success": False, "error": f"多智能体工具异常: {exc}"}
        return None

    def _update_stats(self, name: str) -> None:
        if name == "read_file":
            self.stats["files_read"] += 1
        elif name == "write_file":
            self.stats["write_files"] += 1
        elif name == "edit_file":
            self.stats["edit_files"] += 1
        elif name in ("web_search", "extract_webpage", "save_webpage"):
            self.stats["web_pages"] += 1
        elif name == "run_command":
            self.stats["commands"] += 1

        # 工具调用后立即持久化 stats，供运行期间 get_sub_agent_status 查询
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            _elapsed = int((time.time() * 1000 - self.stats["runtime_start"]) / 1000)
            _snapshot = {**self.stats, "runtime_seconds": _elapsed, "turn_count": self.stats.get("turn_count", 0)}
            self.stats_file.write_text(json.dumps(_snapshot, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _apply_usage(self, usage: Any) -> None:
        try:
            if isinstance(usage, dict):
                prompt = usage.get("prompt_tokens") or usage.get("prompt") or 0
                completion = usage.get("completion_tokens") or usage.get("completion") or 0
                total = usage.get("total_tokens") or usage.get("total") or (prompt + completion)
                self.stats["token_usage"]["prompt"] += int(prompt)
                self.stats["token_usage"]["completion"] += int(completion)
                self.stats["token_usage"]["total"] += int(total)
                # prompt_tokens 即为当前上下文占用的 tokens
                self.current_context_tokens = int(prompt)
                self.stats["current_context_tokens"] = int(prompt)
        except Exception:
            pass

    def _rebuild_system_prompt(self) -> str:
        """从冻结的 system_prompt.txt 重新读取 system prompt（压缩后重建用）。"""
        try:
            if self.system_prompt_file.exists():
                return self.system_prompt_file.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 重建 system prompt 失败: {exc}")
        # 兜底用内存中的
        return self.system_prompt

    def _deep_compress_messages(self) -> bool:
        """深度压缩：把旧消息总结成一条 system 消息，重建 system prompt。

        策略（参考主智能体 compress_conversation）：
        1. 保留冻结的 system prompt（从 system_prompt.txt 读取）
        2. 把 system 之后、最近 N 条消息之前的所有消息压缩成一条 system 摘要
        3. 保留最近的若干条消息（含 tool_calls 配对）不动
        4. 重置 current_context_tokens 估算值

        返回 True 表示执行了压缩。
        """
        if len(self.messages) < 6:
            # 消息太少不压缩
            return False

        self._compress_round += 1
        self.stats["compress_round"] = self._compress_round

        # 保留最近 8 条消息不压缩（确保 tool_calls 和 tool 结果配对完整）
        keep_recent = 8
        # 找到一个安全的切割点：不能在 assistant.tool_calls 和其 tool 响应之间切
        cut_index = len(self.messages) - keep_recent
        # 向前调整，确保不在 tool_calls 配对中间切割
        while cut_index > 1:
            msg = self.messages[cut_index]
            role = msg.get("role")
            # 如果切点是 tool 消息，往前找到对应的 assistant
            if role == "tool":
                cut_index -= 1
                continue
            # 如果切点的 assistant 有 tool_calls，需要保留它和后续 tool
            if role == "assistant" and msg.get("tool_calls"):
                break
            break
        if cut_index <= 1:
            return False

        # 提取要压缩的消息（索引 1 到 cut_index-1，跳过索引 0 的 system prompt）
        old_messages = self.messages[1:cut_index]
        if not old_messages:
            return False

        # 生成压缩摘要
        summary_lines: List[str] = []
        summary_lines.append("系统提示：以下是根据之前的对话记录生成的压缩摘要，请在此基础上继续工作。")
        summary_lines.append(f"（压缩轮次：第 {self._compress_round} 次，压缩前消息数：{len(self.messages)}）")
        summary_lines.append("")

        tool_buffer: List[str] = []
        seen_tool_call_ids: Set[str] = set()

        def flush_tools():
            if not tool_buffer:
                return
            if summary_lines and summary_lines[-1] != "":
                summary_lines.append("")
            summary_lines.append("已执行的工具：")
            summary_lines.extend(f"- {entry}" for entry in tool_buffer)
            tool_buffer.clear()

        for message in old_messages:
            role = message.get("role")
            if role == "user":
                flush_tools()
                content = str(message.get("content") or "")[:500]  # 截断长内容
                summary_lines.append(f"user：{content}")
                continue
            if role == "assistant":
                content = str(message.get("content") or "")
                if content.strip():
                    flush_tools()
                    summary_lines.append(f"assistant：{content[:500]}")
                tool_calls = message.get("tool_calls") or []
                for tc in tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    if tc_id:
                        seen_tool_call_ids.add(tc_id)
                    func = tc.get("function") or {}
                    arguments = func.get("arguments")
                    args_obj = {}
                    if isinstance(arguments, str):
                        try:
                            args_obj = json.loads(arguments)
                        except Exception:
                            args_obj = {}
                    elif isinstance(arguments, dict):
                        args_obj = arguments
                    intent = args_obj.get("intent") if isinstance(args_obj, dict) else None
                    name = func.get("name") or "unknown_tool"
                    entry = intent.strip() if isinstance(intent, str) and intent.strip() else name
                    tool_buffer.append(entry)
                continue
            if role == "tool":
                tc_id = message.get("tool_call_id") or message.get("id")
                if tc_id and tc_id in seen_tool_call_ids:
                    continue
                name = message.get("name") or "unknown_tool"
                tool_buffer.append(name)
                continue
            # 其他角色
            flush_tools()
            content = str(message.get("content") or "")[:300]
            summary_lines.append(f"{role}：{content}" if role else content)

        flush_tools()
        summary_text = "\n".join(summary_lines)

        # 重建消息列表：冻结的 system prompt + 压缩摘要 system + 保留的最近消息
        frozen_system = self._rebuild_system_prompt()
        compressed_system = {
            "role": "system",
            "content": summary_text,
            "metadata": {
                "compression": {
                    "round": self._compress_round,
                    "compressed_count": len(old_messages),
                    "created_at": datetime.now().isoformat(),
                }
            },
        }
        recent_messages = self.messages[cut_index:]
        self.messages = [frozen_system, compressed_system] + recent_messages

        # 重置上下文 tokens 估算（压缩后无法精确知道，设为一个保守值）
        self.current_context_tokens = 0
        self.stats["current_context_tokens"] = 0

        logger.info(
            f"[SubAgentTask] task={self.task_id} 深度压缩完成: "
            f"压缩 {len(old_messages)} 条消息，保留 {len(recent_messages)} 条，"
            f"压缩轮次={self._compress_round}"
        )
        return True

    def _build_media_tool_content(self, result: Dict[str, Any]) -> Any:
        """把 read_mediafile 结果转成 OpenAI 多模态 content。"""
        b64 = result.get("b64")
        mime = result.get("mime")
        file_type = result.get("type")
        if not b64 or not mime:
            return None
        if file_type == "image":
            return [
                {"type": "text", "text": f"已附加图片: {result.get('path')}"},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        if file_type == "video":
            return [
                {"type": "text", "text": f"已附加视频: {result.get('path')}"},
                {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}},
            ]
        return None

    async def _write_finish(self, args: Dict[str, Any], elapsed: float) -> None:
        success = bool(args.get("success", False))
        summary = str(args.get("summary") or "").strip()
        error = None if success else (summary or "子智能体报告执行失败，未说明原因")
        self._finalize_task(success, summary, elapsed, error=error)

    async def _write_timeout(self, elapsed: float) -> None:
        self._finalize_task(False, "任务超时未完成", elapsed, timeout=True, error="任务超时未完成")

    async def _write_failure(self, message: str, *, max_turns_exceeded: bool = False, timeout: bool = False) -> None:
        elapsed = time.time() - (self.stats["runtime_start"] / 1000)
        ma_debug(
            "sub_agent_write_failure",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            message=message,
            max_turns_exceeded=max_turns_exceeded,
            timeout=timeout,
            idle=self._idle,
            cancelled=self._cancelled,
        )
        self._finalize_task(False, message, elapsed, max_turns_exceeded=max_turns_exceeded, timeout=timeout, error=message)

    def _persist_conversation(self, *, partial_summary: str = "") -> None:
        """每轮结束后立即落盘子智能体对话，避免跑完了才存一次导致中间状态丢失。"""
        try:
            runtime_seconds = int((time.time() * 1000 - self.stats["runtime_start"]) / 1000)
            status = "running"
            if self._cancelled:
                status = "terminated"
            elif self.multi_agent_mode and self._idle:
                status = "idle"
            ma_debug(
                "sub_agent_persist_conversation",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
                status=status,
                idle=self._idle,
                cancelled=self._cancelled,
                multi_agent_mode=self.multi_agent_mode,
            )
            conversation_data = {
                "agent_id": self.agent_id,
                "task_id": self.task_id,
                "created_at": datetime.fromtimestamp(self.stats["runtime_start"] / 1000).isoformat(),
                "updated_at": datetime.now().isoformat(),
                "status": status,
                "success": None,
                "summary": partial_summary,
                "messages": self.messages,
                "stats": {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)},
            }
            self.conversation_file.parent.mkdir(parents=True, exist_ok=True)
            conversation_json = json.dumps(conversation_data, ensure_ascii=False)
            self.conversation_file.write_text(conversation_json, encoding="utf-8")

            output_data = {
                "success": None,
                "status": status,
                "summary": partial_summary,
                "stats": conversation_data["stats"],
            }
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            output_json = json.dumps(output_data, ensure_ascii=False)
            self.output_file.write_text(output_json, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 增量保存失败: {exc}")

    async def _recover_from_soft_stop(self) -> None:
        """软停止恢复：从磁盘重新加载对话文件，丢弃半成品状态，变 idle。

        被取消时的半成品状态（调用一半的工具、未执行的 tool_calls）不会
        在对话文件里，因为 _persist_conversation 只在完整轮次结束时保存。
        """
        try:
            if self.conversation_file.exists():
                data = json.loads(self.conversation_file.read_text(encoding="utf-8"))
                self.messages = data.get("messages", [])
                ma_debug(
                    "sub_agent_soft_stop_recovered",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    messages_count=len(self.messages),
                )
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 软停止恢复失败: {exc}")

        # 重置标志和状态
        self._soft_stop = False
        self._cancelled = False
        self._idle = True
        if self.multi_agent_state:
            self.multi_agent_state.mark_status(self.agent_id, "idle")
        # 重新初始化 continue event 以备后续唤醒
        self._continue_event = asyncio.Event()
        # 持久化恢复后的状态
        self._persist_conversation(partial_summary="[soft stopped]")

    async def _idle_wait_loop(self) -> None:
        """软停止后进入的 idle 等待循环。

        保持子智能体活着，等待新消息注入后重新进入 _run_loop 正常工作。
        """
        while not self._cancelled:
            if self._continue_event is None:
                self._continue_event = asyncio.Event()
            try:
                await asyncio.wait_for(self._continue_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            if self._cancelled:
                break
            if not self._continue_event.is_set():
                continue
            # 被唤醒了，有新消息注入，重新进入正常 _run_loop
            self._continue_event.clear()
            self._idle = False
            if self.multi_agent_state:
                self.multi_agent_state.mark_status(self.agent_id, "running")
            ma_debug(
                "sub_agent_idle_wait_wake",
                task_id=self.task_id,
                agent_id=self.agent_id,
                display_name=self.display_name,
            )
            # 重新进入正常循环
            await self._run_loop()
            # _run_loop 退出后再次 idle 等待
            self._idle = True
            if self.multi_agent_state:
                self.multi_agent_state.mark_status(self.agent_id, "idle")

    def _finalize_task(self, success: bool, summary: str, elapsed: float, *, max_turns_exceeded: bool = False, timeout: bool = False, error: Optional[str] = None) -> None:
        runtime_seconds = int(elapsed)
        ma_debug(
            "sub_agent_finalize_task",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            success=success,
            summary=summary,
            idle=self._idle,
            cancelled=self._cancelled,
        )
        if timeout:
            status = "timeout"
        elif max_turns_exceeded:
            status = "failed"
        elif success:
            status = "completed"
        else:
            status = "failed"
        output_data = {
            "success": success,
            "status": status,
            "summary": summary,
            "error": error,
            "timeout": timeout,
            "max_turns_exceeded": max_turns_exceeded,
            "stats": {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)},
        }
        conversation_data = {
            "agent_id": self.agent_id,
            "created_at": datetime.fromtimestamp(self.stats["runtime_start"] / 1000).isoformat(),
            "completed_at": datetime.now().isoformat(),
            "success": success,
            "summary": summary,
            "messages": self.messages,
            "stats": output_data["stats"],
        }
        stats_data = {**self.stats, "runtime_seconds": runtime_seconds, "turn_count": self.stats.get("turn_count", 0)}

        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
        self.stats_file.write_text(json.dumps(stats_data, ensure_ascii=False), encoding="utf-8")
        self.conversation_file.write_text(json.dumps(conversation_data, ensure_ascii=False), encoding="utf-8")

        self.emit("output", output_data)
        self.emit("conversation", conversation_data)

        self.manager._mark_task_done(self.task_id, success, summary, runtime_seconds)

    def _consume_control_request(self) -> Optional[str]:
        """读取并消费 control.json 控制请求（跨 manager 信号通道）。

        terminate_sub_agent / stop_sub_agent 可能运行在另一个 manager 的内存里，
        无法直接操作本实例，通过任务目录下的 control.json 下达指令。
        返回 "terminate" / "soft_stop" / None。
        """
        control_file = self.output_file.parent / "control.json"
        try:
            if not control_file.exists():
                return None
            try:
                data = json.loads(control_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            action = str(data.get("action") or "").strip() if isinstance(data, dict) else ""
            # 消费后删除，避免重复执行；删除失败不阻塞主流程
            try:
                control_file.unlink()
            except Exception:
                pass
            if action in {"terminate", "soft_stop"}:
                return action
        except Exception:
            pass
        return None

    def _write_terminated_snapshot(self) -> None:
        """写入 terminated 终态快照到 output.json（硬取消/自杀路径）。"""
        try:
            runtime_seconds = int(time.time() - (self.stats["runtime_start"] / 1000))
            output_data = {
                "success": False,
                "status": "terminated",
                "summary": "子智能体已被手动终止",
                "stats": {**self.stats, "runtime_seconds": runtime_seconds},
            }
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[SubAgentTask] 写入终结快照失败: {exc}")

    def cancel(self) -> None:
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()

    def request_soft_stop(self) -> None:
        """请求软停止：中断子智能体当前工作 → 从对话文件恢复 → 变 idle。

        与 cancel() 的区别：
        - cancel(): 取消 asyncio.Task → CancelledError → _write_failure → terminated
        - request_soft_stop(): 设标志 + cancel asyncio.Task → CancelledError
          → 捕获后重载对话文件（丢弃半成品）→ mark idle → 保留实例
        """
        self._soft_stop = True
        ma_debug(
            "sub_agent_request_soft_stop",
            task_id=self.task_id,
            agent_id=self.agent_id,
            has_task=bool(self._task),
            task_done=self._task.done() if self._task else None,
        )
        # 取消子智能体正在阻塞等待的 ask_master/ask_other 未来，解除阻塞
        if self.multi_agent_state and self.agent_id is not None:
            try:
                self.multi_agent_state.cancel_pending_question_for_agent(self.agent_id)
            except Exception:
                pass
        # 直接 cancel asyncio.Task 以中断正在执行的工具调用/API 调用
        # CancelledError 会在 run() 中被捕获，检测到 _soft_stop 后走恢复路径
        if self._task and not self._task.done():
            try:
                loop = self._task.get_loop()
                loop.call_soon_threadsafe(self._task.cancel)
            except Exception:
                self._task.cancel()

        # 额外保险：如果 _task 为 None 但子智能体状态是 running，直接设 idle 状态
        if self._task is None and self.multi_agent_state and self.agent_id is not None:
            ma_debug("sub_agent_soft_stop_no_task_mark_idle", task_id=self.task_id, agent_id=self.agent_id)
            self._soft_stop = False
            self._idle = True
            self.multi_agent_state.mark_status(self.agent_id, "idle")
            try:
                self._persist_conversation(partial_summary="[soft stopped no task]")
            except Exception:
                pass
