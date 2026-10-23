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
from typing import Any, Dict, List, Optional, Set

from modules.sub_agent.task_common import (
    APIClient, SubAgentModelCallError, _SUB_AGENT_MAX_API_RETRIES,
    _SUB_AGENT_RETRY_DELAY_SECONDS, _QUESTION_ID_RE, _format_tool_result,
    resolve_locked_model_profile, logger, ma_debug, tr,
)

class SubAgentCommunicationMixin:
    def _forward_output_to_master(self, output_text: str, *, is_final: bool = False) -> None:
        """把子智能体的 assistant 文本输出转发成主对话的 user 消息，并写入进度文件供前端查看。"""
        ma_debug(
            "sub_agent_forward_output_enter",
            task_id=self.task_id,
            agent_id=self.agent_id,
            display_name=self.display_name,
            has_state=bool(self.multi_agent_state),
            state_id=id(self.multi_agent_state) if self.multi_agent_state else None,
            output_len=len(output_text),
            is_final=is_final,
        )
        if not self.multi_agent_state:
            ma_debug("sub_agent_forward_no_state", task_id=self.task_id, agent_id=self.agent_id)
            return
        # 如果这是对 pending 提问的回答，不走主对话转发，而是返回到 ask 工具结果
        self._provide_answer(output_text)
        try:
            from modules.multi_agent.state import build_sub_agent_output_text
            msg = build_sub_agent_output_text(self.display_name, output_text.strip(), is_final=is_final)
            self.multi_agent_state.push_master_message(msg)
            ma_debug(
                "sub_agent_forward_pushed",
                task_id=self.task_id,
                agent_id=self.agent_id,
                state_id=id(self.multi_agent_state) if self.multi_agent_state else None,
                msg_preview=msg[:200],
            )
            # 同时记录到实例状态，供协作运行态查询使用
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
        """Queue work on the child loop, never append during a tool batch."""
        self.enqueue_work(message_text, timeout=None)


    def _peek_pending_question_id(self) -> Optional[str]:
        """检查最后一条 user 消息是否是向本智能体提问，返回 question_id。"""
        if not self.messages:
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


    async def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """通过 manager 调用主进程执行工具。

        通信与查询工具（ask_master / ask_other_agent / answer_other_agent /
        get_sub_agent_status）在主进程内直接处理，不再转发到 WebTerminal。
        """
        if self.multi_agent_state:
            result = await self._execute_multi_agent_tool(name, args)
            if result is not None:
                return result
        mcp_result = await self._execute_mcp_tool(name, args)
        if mcp_result is not None:
            return mcp_result
        return await self.manager.execute_tool_for_sub_agent(name, args, agent_id=self.agent_id, executor=self)


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
                self.manager._save_state()
                inst = state.get_instance(self.agent_id)
                if inst:
                    inst.last_output = f"[ask_master] {question[:200]}"
                # 阻塞等待回答（状态标为正在等待主智能体回答）
                state.mark_status(self.agent_id, "running")
                try:
                    answer = await state.wait_for_answer(question_id, self.agent_id, timeout=float(args.get("timeout_seconds") or 600))
                except asyncio.CancelledError:
                    raise
                state.mark_status(self.agent_id, "running")
                return {"success": True, "answer": answer, "question_id": question_id}

            if name == "ask_other_agent":
                target_name = str(args.get("name") or "").strip()
                question = str(args.get("question") or "").strip()
                question_id = str(args.get("question_id") or f"ask_other_{uuid.uuid4().hex[:10]}")
                ma_debug(
                    "sub_agent_tool_ask_other_agent",
                    task_id=self.task_id,
                    agent_id=self.agent_id,
                    display_name=self.display_name,
                    target_display_name=target_name,
                    question_id=question_id,
                    question=question[:500],
                )
                if not target_name or not question:
                    return {"success": False, "error": "参数缺失"}
                # 显示名寻址：子智能体只知道对方的角色内编号显示名，
                # 全局 agent_id 在内部解析，不暴露给模型
                target_inst = state.get_instance_by_display_name(target_name)
                if not target_inst:
                    available = "、".join(
                        n for n in state.list_display_names() if n != self.display_name
                    ) or "（无）"
                    return {"success": False, "error": f"未找到子智能体「{target_name}」。当前可用: {available}"}
                # 构造提问消息并插入到目标子对话；同时要求其在下一轮调用 answer_other_agent
                from modules.multi_agent.state import build_sub_agent_ask_other_text
                target_display = target_inst.display_name
                msg = build_sub_agent_ask_other_text(self.display_name, target_display, question, question_id)
                delivered = await asyncio.to_thread(
                    self.manager.inject_message_to_sub_agent, target_inst.agent_id, msg)
                if not delivered:
                    return {"success": False, "error": "目标子智能体已终结或原上下文不可恢复。"}
                state.push_master_message(msg)
                self.manager._save_state()
                # 阻塞等待回答
                try:
                    answer = await state.wait_for_answer(question_id, self.agent_id, timeout=float(args.get("timeout_seconds") or 600))
                except asyncio.CancelledError:
                    raise
                return {"success": True, "answer": answer, "question_id": question_id}

            if name == "answer_other_agent":
                # question_id 全局唯一，足以定位等待方，无需提问方编号
                question_id = str(args.get("question_id") or "")
                answer = str(args.get("answer") or "").strip()
                if not question_id or not answer:
                    return {"success": False, "error": "参数缺失"}
                ok = state.provide_answer(question_id, answer)
                return {"success": bool(ok), "question_id": question_id}

            if name == "get_sub_agent_status":
                from modules.sub_agent.named import query_agent_status
                return query_agent_status(self.manager, self.task_record["conversation_id"], args)
        except asyncio.TimeoutError:
            return {"success": False, "error": "等待回答超时", "question_id": args.get("question_id")}
        except Exception as exc:
            logger.exception(f"[SubAgent] 多智能体工具异常: {name}")
            return {"success": False, "error": f"多智能体工具异常: {exc}"}
        return None
