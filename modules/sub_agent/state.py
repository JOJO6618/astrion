"""子智能体状态管理 Mixin。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger
from modules.multi_agent.debug_logger import ma_debug

logger = setup_logger(__name__)
TERMINAL_STATUSES = {"completed", "failed", "timeout"}
# output.json 停留在 running/idle 快照（success 为 None）时的僵尸判定阈值：
# 传统模式且本进程无运行句柄（如进程重启后），快照超过该时长未更新才允许判 failed，
# 避免把仍在运行的子智能体误判为失败。
STALE_RUNNING_SNAPSHOT_SECONDS = 600


class SubAgentStateMixin:
    """提供子智能体任务状态加载、保存、刷新与结果落盘能力。"""

    state_file: Path
    tasks: Dict[str, Dict[str, Any]]
    conversation_agents: Dict[str, List[int]]
    _running_tasks: Dict[str, Any]
    _state_lock: Any

    def _load_state(self):
        with self._state_lock:
            if not self.state_file.exists():
                self.tasks = {}
                self.conversation_agents = {}
                return
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                loaded_tasks = data.get("tasks", {})
                loaded_agents = data.get("conversation_agents", {})
            except json.JSONDecodeError:
                logger.warning("子智能体状态文件损坏，已忽略。")
                self.tasks = {}
                self.conversation_agents = {}
                return

            runtime_only_keys = {"_stdout_lines"}
            merged_tasks: Dict[str, Dict] = {}
            for task_id, task in loaded_tasks.items():
                existing = self.tasks.get(task_id)
                if existing:
                    for key in runtime_only_keys:
                        if key in existing:
                            task[key] = existing[key]
                merged_tasks[task_id] = task
            self.tasks = merged_tasks
            self.conversation_agents = loaded_agents

            # 恢复多智能体运行态（如果状态文件包含）
            try:
                from modules.multi_agent.state import (
                    MultiAgentState,
                    GLOBAL_MULTI_AGENT_STATES_LOCK,
                )

                manager = self
                multi_agent_states = getattr(manager, "multi_agent_states", None)
                if multi_agent_states is not None and isinstance(multi_agent_states, dict):
                    loaded_ma_states = data.get("multi_agent_states", {})
                    # multi_agent_states 是全进程共享注册表；加锁防止多个 manager
                    # 并发 _load_state 时对同一 conv 重复 from_snapshot 出多份副本。
                    with GLOBAL_MULTI_AGENT_STATES_LOCK:
                        for conv_id, snapshot in loaded_ma_states.items():
                            try:
                                if isinstance(snapshot, dict):
                                    # 关键：不要覆盖内存中已存在的 MultiAgentState，
                                    # 否则 SubAgentTask 持有的旧引用上的 pending_master_messages
                                    # 会被新的空 state 覆盖，导致子智能体输出丢失。
                                    if conv_id in multi_agent_states:
                                        ma_debug(
                                            "load_state_skip_existing_ma_state",
                                            conversation_id=conv_id,
                                            existing_state_id=id(multi_agent_states[conv_id]),
                                        )
                                        continue
                                    multi_agent_states[conv_id] = MultiAgentState.from_snapshot(snapshot)
                                    ma_debug(
                                        "load_state_restore_ma_state",
                                        conversation_id=conv_id,
                                        state_id=id(multi_agent_states[conv_id]),
                                    )
                            except Exception as exc:
                                logger.warning(f"恢复多智能体状态失败 {conv_id}: {exc}")
                    # 用任务记录（持久真相）校准实例终态：terminate 只改了发起 manager
                    # 的内存与磁盘任务记录，而磁盘上的 ma 快照可能是更早的 idle 副本
                    # （多 manager 并存时代其他 manager 会用陈旧内存快照覆盖落盘）。
                    # 任务记录的 terminated/终态是吸收态，恢复快照后必须以此校准，
                    # 否则已终结实例会以 idle 复活显示。
                    for cal_state in list(multi_agent_states.values()):
                        for agent in cal_state.list_all():
                            cal_task = self.tasks.get(agent.task_id)
                            if not isinstance(cal_task, dict):
                                continue
                            cal_status = cal_task.get("status")
                            if (
                                cal_status in TERMINAL_STATUSES.union({"terminated"})
                                and agent.status != cal_status
                            ):
                                ma_debug(
                                    "load_state_calibrate_agent_status",
                                    conversation_id=getattr(cal_state, "conversation_id", ""),
                                    agent_id=agent.agent_id,
                                    before=agent.status,
                                    after=cal_status,
                                )
                                cal_state.mark_status(agent.agent_id, cal_status)
                    # 存量脏数据自愈：历史版本曾把内部 agent_id 与角色内编号混用，
                    # 在编号缺失时生成过 "_None" 后缀显示名（如 Full-Stack Engineer_None）。
                    # 这里按「对话 × 角色 × 任务创建时间」重新排序编号修复，幂等：
                    # 修完后 display_name 不再以 _None 结尾，不会再命中。
                    none_agents = []
                    for st in list(multi_agent_states.values()):
                        for agent in st.list_all():
                            if str(agent.display_name or "").endswith("_None"):
                                none_agents.append((st, agent))
                    if none_agents:
                        groups = {}
                        for st, agent in none_agents:
                            groups.setdefault((id(st), agent.role_id), []).append((st, agent))
                        for (_sid, _role_id), items in groups.items():
                            def _created_of(pair):
                                t = self.tasks.get(pair[1].task_id) or {}
                                return t.get("created_at") or 0
                            items.sort(key=_created_of)
                            seq = 1
                            for st, agent in items:
                                base = str(agent.display_name)[: -len("_None")]
                                if not base:
                                    continue
                                new_name = f"{base}_{seq}"
                                seq += 1
                                ma_debug(
                                    "load_state_fix_none_display_name",
                                    agent_id=agent.agent_id,
                                    before=agent.display_name,
                                    after=new_name,
                                )
                                agent.display_name = new_name
                                task = self.tasks.get(agent.task_id)
                                if isinstance(task, dict) and str(task.get("display_name") or "").endswith("_None"):
                                    task["display_name"] = new_name
            except Exception as exc:
                logger.warning(f"加载多智能体状态失败: {exc}")

            if self.tasks:
                migrated = False
                for task in self.tasks.values():
                    if task.get("parent_conversation_id"):
                        continue
                    candidate = task.get("conversation_id") or (task.get("service_payload") or {}).get("parent_conversation_id")
                    if candidate:
                        task["parent_conversation_id"] = candidate
                        migrated = True
                if migrated:
                    self._save_state_unsafe()

    def _save_state(self):
        with self._state_lock:
            self._save_state_unsafe()

    def _save_state_unsafe(self):
        payload = {
            "tasks": self.tasks,
            "conversation_agents": self.conversation_agents,
        }
        # 多智能体运行态持久化
        try:
            manager = self
            multi_agent_states = getattr(manager, "multi_agent_states", None)
            if multi_agent_states:
                payload["multi_agent_states"] = {
                    conv_id: state.to_snapshot()
                    # 全局共享注册表可能被其他线程并发增删，先拷贝再遍历
                    for conv_id, state in list(multi_agent_states.items())
                    if isinstance(state, object) and hasattr(state, "to_snapshot")
                }
        except Exception as exc:
            logger.warning(f"保存多智能体状态失败: {exc}")
        try:
            self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"保存子智能体状态失败: {exc}")

    def _check_task_status(self, task: Dict) -> Dict:
        """检查任务状态，读取输出文件。"""
        task_id = task["task_id"]
        # terminated 是吸收态：不允许 output.json 中的陈旧 running/idle 快照复活
        if task.get("status") == "terminated":
            return {"success": False, "status": "terminated", "task_id": task_id}
        output_file = Path(task.get("output_file", ""))
        if not output_file.exists():
            running_task = self._running_tasks.get(task_id)
            if running_task and running_task.done():
                try:
                    running_task.result(timeout=0)
                except Exception:
                    pass
                task["status"] = "failed"
                task["updated_at"] = time.time()
            return {"status": task.get("status", "running"), "task_id": task_id}

        try:
            output = json.loads(output_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[check_task_status] output 文件解析失败: {exc}")
            task["status"] = "failed"
            task["updated_at"] = time.time()
            return {"success": False, "status": "failed", "task_id": task_id, "message": f"输出文件解析失败: {exc}"}

        raw_success = output.get("success")
        success = bool(raw_success)
        summary = output.get("summary", "")
        stats = output.get("stats", {})
        elapsed_seconds = self._compute_elapsed_seconds(task)

        # output.json 被写入终态快照（terminate 的跨 manager 击杀通道）：同步记录为终结
        if output.get("status") == "terminated":
            task["status"] = "terminated"
            task["updated_at"] = time.time()
            ma_debug("check_task_status_output_terminated", task_id=task_id, agent_id=task.get("agent_id"))
            return {"success": False, "status": "terminated", "task_id": task_id, "message": summary or "子智能体已被终结"}

        ma_debug(
            "check_task_status",
            task_id=task_id,
            agent_id=task.get("agent_id"),
            multi_agent_mode=task.get("multi_agent_mode"),
            output_status=output.get("status"),
            output_success=success,
        )

        # output.json 在子智能体存活期间是运行中周期快照（success 为 None，
        # status 为 running/idle），最终写入必然带 success 布尔值。
        # 遇到运行中快照一律先走存活判定：任何模式下都不能把仍在运行的
        # 子智能体误判为 failed（传统模式曾因此被 poll_updates 误杀）。
        if raw_success is None and output.get("status") in {"running", "idle"}:
            return self._handle_running_snapshot(task, output_file, str(output.get("status")))

        if output.get("timeout"):
            status = "timeout"
        elif output.get("max_turns_exceeded"):
            status = "failed"
            summary = f"任务执行超过最大轮次限制。{summary}"
        elif success:
            status = "completed"
        else:
            status = "failed"

        ma_debug("check_task_status_result", task_id=task_id, agent_id=task.get("agent_id"), status=status)
        task["status"] = status
        task["updated_at"] = time.time()
        if status == "completed" and elapsed_seconds is not None:
            task["elapsed_seconds"] = elapsed_seconds
            task["runtime_seconds"] = elapsed_seconds

        agent_id = task.get("agent_id")
        task_summary = task.get("summary")
        deliverables_dir = task.get("deliverables_dir")
        stats_summary = self._build_stats_summary(stats)

        if status == "completed":
            system_message = self._compose_sub_agent_message(
                prefix=f"✅ 子智能体{agent_id} 任务摘要：{task_summary} 已完成。",
                stats_summary=stats_summary,
                summary=summary,
                deliverables_dir=deliverables_dir,
                duration_seconds=elapsed_seconds,
            )
        elif status == "timeout":
            system_message = self._compose_sub_agent_message(
                prefix=f"⏱️ 子智能体{agent_id} 任务摘要：{task_summary} 超时未完成。",
                stats_summary=stats_summary,
                summary=summary,
            )
        else:
            system_message = self._compose_sub_agent_message(
                prefix=f"❌ 子智能体{agent_id} 任务摘要：{task_summary} 执行失败。",
                stats_summary=stats_summary,
                summary=summary,
            )

        result = {
            "success": success,
            "status": status,
            "task_id": task_id,
            "agent_id": agent_id,
            "message": summary,
            "deliverables_dir": deliverables_dir,
            "stats": stats,
            "stats_summary": stats_summary,
            "system_message": system_message,
        }
        if status in {"failed", "timeout"}:
            error_text = str(output.get("error") or "").strip()
            if error_text:
                result["error"] = error_text
        if status == "completed" and elapsed_seconds is not None:
            result["elapsed_seconds"] = elapsed_seconds
            result["runtime_seconds"] = elapsed_seconds
        task["final_result"] = result
        return result

    def _handle_running_snapshot(self, task: Dict, output_file: Path, output_status: str) -> Dict:
        """处理 output.json 停留在 running/idle 快照（success 为 None）的状态判定。

        - 本进程内 asyncio 句柄仍存活：任务确实在跑，保持 running（所有模式）；
        - 句柄已结束但快照未更新：子智能体异常退出、未来得及写最终结果，判 failed 并记录异常；
        - 多智能体模式无句柄：running/idle 均为正常存活态，保持原状；
        - 传统模式无句柄（如进程重启）：快照新鲜视为仍在运行（可能由同数据目录的其他
          进程写入），超过 STALE_RUNNING_SNAPSHOT_SECONDS 未更新按僵尸任务判 failed。
        """
        task_id = task["task_id"]
        running_task = self._running_tasks.get(task_id)
        if running_task is not None:
            if not running_task.done():
                task["status"] = "running"
                task["updated_at"] = time.time()
                ma_debug("check_task_status_keep_alive", task_id=task_id, status="running")
                return {"status": "running", "task_id": task_id}
            # 句柄已结束但输出仍是运行中快照：子智能体异常退出
            error_text = "子智能体异常退出，未写入最终执行结果"
            try:
                running_task.result(timeout=0)
            except asyncio.CancelledError:
                error_text = "子智能体任务被取消，未写入最终执行结果"
            except BaseException as exc:  # 需要保留真实异常信息
                detail = str(exc) or exc.__class__.__name__
                error_text = f"子智能体异常退出：{detail}"
            ma_debug("check_task_status_crashed_snapshot", task_id=task_id, error=error_text)
            return self._fail_task_from_snapshot(task, error_text)
        if task.get("multi_agent_mode"):
            # 多智能体模式：running/idle 表示仍在运行或本轮结束但上下文保留
            task["status"] = output_status
            task["updated_at"] = time.time()
            ma_debug("check_task_status_keep_alive", task_id=task_id, status=output_status)
            return {"status": output_status, "task_id": task_id}
        # 传统模式无运行句柄：按快照新旧程度兜底
        try:
            snapshot_age = time.time() - output_file.stat().st_mtime
        except OSError:
            snapshot_age = float("inf")
        if snapshot_age < STALE_RUNNING_SNAPSHOT_SECONDS:
            task["status"] = "running"
            task["updated_at"] = time.time()
            ma_debug(
                "check_task_status_keep_alive_no_handle",
                task_id=task_id,
                snapshot_age=int(snapshot_age),
            )
            return {"status": "running", "task_id": task_id}
        error_text = (
            f"子智能体输出停留在运行中快照且已 {int(snapshot_age)} 秒未更新，"
            "疑似进程中断或任务崩溃"
        )
        ma_debug("check_task_status_stale_snapshot", task_id=task_id, snapshot_age=int(snapshot_age))
        return self._fail_task_from_snapshot(task, error_text)

    def _fail_task_from_snapshot(self, task: Dict, error_text: str) -> Dict:
        """将无法恢复的运行中快照任务标记为 failed，并落盘可读的错误信息。"""
        task["status"] = "failed"
        task["updated_at"] = time.time()
        result = {
            "success": False,
            "status": "failed",
            "task_id": task.get("task_id"),
            "agent_id": task.get("agent_id"),
            "message": error_text,
            "error": error_text,
            "system_message": (
                f"❌ 子智能体{task.get('agent_id')} 任务摘要：{task.get('summary')} "
                f"执行失败。{error_text}"
            ),
        }
        task["final_result"] = result
        return result

    def _handle_timeout(self, task: Dict) -> Dict:
        """处理任务超时。"""
        task_id = task["task_id"]
        running_task = self._running_tasks.pop(task_id, None)
        if running_task and not running_task.done():
            running_task.cancel()
            deadline = time.time() + 5
            while not running_task.done() and time.time() < deadline:
                time.sleep(0.05)

        task["status"] = "timeout"
        task["updated_at"] = time.time()

        stats = {}
        stats_file = Path(task.get("stats_file", ""))
        if stats_file.exists():
            try:
                stats = json.loads(stats_file.read_text(encoding="utf-8"))
            except Exception:
                stats = {}
        stats_summary = self._build_stats_summary(stats)
        system_message = self._compose_sub_agent_message(
            prefix=f"⏱️ 子智能体{task.get('agent_id')} 任务摘要：{task.get('summary')} 超时未完成。",
            stats_summary=stats_summary,
            summary="等待超时，子智能体已被终止。",
        )

        result = {
            "success": False,
            "status": "timeout",
            "task_id": task_id,
            "agent_id": task.get("agent_id"),
            "message": "等待超时，子智能体已被终止。",
            "stats": stats,
            "stats_summary": stats_summary,
            "system_message": system_message,
        }
        task["final_result"] = result
        self._save_state()
        return result

    def _mark_task_terminated(
        self,
        task: Dict[str, Any],
        *,
        message: str,
        system_message: Optional[str] = None,
        notified: bool = False,
    ) -> Dict[str, Any]:
        task["status"] = "terminated"
        task["updated_at"] = time.time()
        if notified:
            task["notified"] = True
        result = {
            "success": False,
            "status": "terminated",
            "task_id": task.get("task_id"),
            "agent_id": task.get("agent_id"),
            "message": message,
            "system_message": system_message or message,
        }
        task["final_result"] = result
        return result

    def _mark_task_done(
        self,
        task_id: str,
        success: bool,
        summary: str,
        runtime_seconds: int,
    ) -> None:
        task = self.tasks.get(task_id)
        if not task:
            return
        # 如果 terminate_sub_agent 已把任务标为 terminated，下渗异步任务自然结束时
        # 不要被“已完成/fulfilled覆盖为failed”值覆盖，否则 task 被碰成 failed 但实际上
        # 用户手动终止已发生，状态语义错误且“successful=false”。
        existing_status = task.get("status")
        if existing_status in TERMINAL_STATUSES.union({"terminated"}):
            ma_debug(
                "mark_task_done_skip_already_terminal",
                task_id=task_id,
                existing_status=existing_status,
            )
            return
        status = "completed" if success else "failed"
        ma_debug(
            "mark_task_done",
            task_id=task_id,
            agent_id=task.get("agent_id"),
            success=success,
            new_status=status,
        )
        task["status"] = status
        task["updated_at"] = time.time()
        task["runtime_seconds"] = runtime_seconds
        self._check_task_status(task)
        self._save_state()

    def _refresh_task_runtime_state(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """刷新单个任务运行态。"""
        status = task.get("status")
        task_id = task.get("task_id")
        agent_id = task.get("agent_id")
        multi_agent_flag = task.get("multi_agent_mode")
        ma_debug(
            "refresh_task_runtime_state_start",
            task_id=task_id,
            agent_id=agent_id,
            before_status=status,
            multi_agent_mode=multi_agent_flag,
        )
        if status in TERMINAL_STATUSES.union({"terminated"}):
            return {"status": status, "task_id": task_id}

        running_task = self._running_tasks.get(task_id) if task_id else None
        if running_task:
            if running_task.done():
                try:
                    running_task.result(timeout=0)
                except Exception:
                    pass
                # 多智能体模式：任务自然进入 idle 时不写输出文件，不应标记为失败
                if task.get("multi_agent_mode") and not Path(task.get("output_file", "")).exists():
                    task["status"] = "idle"
                    task["updated_at"] = time.time()
                    ma_debug("refresh_task_runtime_state_idle_no_output", task_id=task_id)
                    return {"status": "idle", "task_id": task_id}
                result = self._check_task_status(task)
                ma_debug("refresh_task_runtime_state_done_result", task_id=task_id, result=result)
                return result
            ma_debug("refresh_task_runtime_state_still_running", task_id=task_id)
            return {"status": "running", "task_id": task_id}

        output_file = Path(task.get("output_file", ""))
        if output_file.exists():
            result = self._check_task_status(task)
            ma_debug("refresh_task_runtime_state_output_result", task_id=task_id, result=result)
            return result

        # 多智能体模式：没有输出文件表示 idle，不强制清理
        if task.get("multi_agent_mode"):
            task["status"] = "idle"
            task["updated_at"] = time.time()
            ma_debug("refresh_task_runtime_state_idle_no_output_file", task_id=task_id)
            return {"status": "idle", "task_id": task_id}

        # 归属守卫：对话级任务只由所属对话的 manager 做终态判定。
        # 其他 manager（工作区级或同工作区其他对话的 manager）本地没有该任务的
        # 运行句柄，继续往下走会把正在运行的任务误标为 terminated（吸收态，不可逆）。
        owner_cid = getattr(self, "owner_conversation_id", None)
        task_cid = task.get("conversation_id")
        if task_cid and task_cid != owner_cid:
            ma_debug("refresh_task_runtime_state_skip_foreign_task", task_id=task_id, task_cid=task_cid, owner_cid=owner_cid)
            return {"status": status, "task_id": task_id}

        # 调度窗口宽限：新创建任务的 asyncio.Task 可能尚未登记到 _running_tasks
        # （或状态刚由另一线程提交），宽限期内不做 terminated 判定
        try:
            created_at = float(task.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        if created_at > 0 and (time.time() - created_at) < 120:
            return {"status": "running", "task_id": task_id}

        if self._should_force_cleanup_stale_task(task):
            return self._mark_task_terminated(
                task,
                message="子智能体疑似僵尸任务，已超时自动清理运行状态。",
                system_message="⚠️ 子智能体长时间未结束，系统已自动清理运行状态。",
                notified=True,
            )

        return self._mark_task_terminated(
            task,
            message="检测到子智能体任务已退出，已自动清理运行状态。",
            system_message="⚠️ 子智能体任务异常退出，系统已自动清理运行状态。",
            notified=True,
        )

    def reconcile_task_states(self, conversation_id: Optional[str] = None) -> int:
        """修正运行态任务状态，返回修正条目数。"""
        changed = 0
        ma_debug("reconcile_task_states_start", conversation_id=conversation_id, task_count=len(self.tasks))
        for task in self.tasks.values():
            if not isinstance(task, dict):
                continue
            if conversation_id and task.get("conversation_id") != conversation_id:
                continue
            before_status = task.get("status")
            before_notified = task.get("notified")
            self._refresh_task_runtime_state(task)
            after_status = task.get("status")
            if after_status != before_status or task.get("notified") != before_notified:
                ma_debug(
                    "reconcile_task_states_changed",
                    task_id=task.get("task_id"),
                    agent_id=task.get("agent_id"),
                    before_status=before_status,
                    after_status=after_status,
                )
                changed += 1
        if changed:
            self._save_state()
        ma_debug("reconcile_task_states_end", conversation_id=conversation_id, changed=changed)
        return changed

    def _should_force_cleanup_stale_task(self, task: Dict[str, Any]) -> bool:
        try:
            created_at = float(task.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0
        if created_at <= 0:
            return False
        # timeout_seconds 为 None 表示永久子智能体，不强制清理
        raw_timeout = task.get("timeout_seconds")
        if raw_timeout is None:
            return False
        from config import SUB_AGENT_DEFAULT_TIMEOUT
        timeout_seconds = int(raw_timeout or SUB_AGENT_DEFAULT_TIMEOUT or 0)
        timeout_seconds = max(timeout_seconds, 1)
        grace_seconds = 120
        elapsed = time.time() - created_at
        if elapsed <= (timeout_seconds + grace_seconds):
            return False
        output_file = Path(task.get("output_file", ""))
        if output_file.exists():
            return False
        progress_file = Path(task.get("progress_file", ""))
        if progress_file.exists():
            try:
                stale_span = time.time() - progress_file.stat().st_mtime
                if stale_span <= grace_seconds:
                    return False
            except Exception:
                pass
        return True
