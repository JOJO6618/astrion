"""子智能体任务管理（主进程内协程模式）。

子智能体不再作为独立子进程启动，而是作为 SubAgentManager 所在事件循环中的
asyncio.Task 运行。所有实际工具调用都通过主 WebTerminal 执行，因此自然复用
主进程的宿主机沙箱 / Docker 容器链路。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from config import (
    OUTPUT_FORMATS,
    SUB_AGENT_DEFAULT_TIMEOUT,
    SUB_AGENT_MAX_ACTIVE,
    SUB_AGENT_MODELS_CONFIG_FILE,
    SUB_AGENT_STATUS_POLL_INTERVAL,
)
from utils.logger import setup_logger
from modules.sub_agent.task import SubAgentTask
from modules.sub_agent.prompts import build_user_message, build_system_prompt
from modules.sub_agent.tools import handle_read_mediafile
from modules.sub_agent.state import SubAgentStateMixin
from modules.sub_agent.stats import SubAgentStatsMixin
from modules.sub_agent.creation import SubAgentCreationMixin
from modules.multi_agent.debug_logger import ma_debug
from modules.multi_agent.state import (
    GLOBAL_MULTI_AGENT_STATES,
    GLOBAL_MULTI_AGENT_STATES_LOCK,
)
from server.utils_common import debug_log

if TYPE_CHECKING:
    from core.web_terminal import WebTerminal
    from modules.user_container_manager import ContainerHandle

logger = setup_logger(__name__)
TERMINAL_STATUSES = {"completed", "failed", "timeout"}


class SubAgentManager(SubAgentStateMixin, SubAgentStatsMixin, SubAgentCreationMixin):
    """负责主智能体与子智能体的任务调度（协程模式）。"""

    def __init__(
        self,
        project_path: str,
        data_dir: str,
        container_session: Optional["ContainerHandle"] = None,
        owner_conversation_id: Optional[str] = None,
    ):
        self.project_path = Path(project_path).resolve()
        self.data_dir = Path(data_dir).resolve()
        # 对话级隔离：本 manager 所属 terminal 绑定的对话。
        # 非空时 restore_running_tasks 只恢复该对话的多智能体任务；
        # 为空（工作区级服务 terminal）时不恢复，避免同工作区多个
        # 对话级 manager 重复恢复同一批任务（任务延迟到对话激活时恢复）。
        self.owner_conversation_id = owner_conversation_id
        # 子智能体任务和状态按 data_dir 隔离（web 模式下按用户/工作区自动隔离）
        self.base_dir = self.data_dir / "sub_agent_tasks"
        self.state_file = self.data_dir / "sub_agents.json"
        self.models_config_file = SUB_AGENT_MODELS_CONFIG_FILE
        self.container_session: Optional["ContainerHandle"] = container_session
        self.host_execution_mode: str = "sandbox"
        self.terminal: Optional["WebTerminal"] = None
        # 多智能体模式：MultiAgentState 是会话级运行态，全进程共享一份注册表
        # （见 modules/multi_agent/state.py 中 GLOBAL_MULTI_AGENT_STATES 的注释），
        # 避免多 manager 并存时同一对话被 from_snapshot 复制出多份独立副本。
        # key = conversation_id, value = MultiAgentState
        self.multi_agent_states: Dict[str, Any] = GLOBAL_MULTI_AGENT_STATES

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.conversation_agents: Dict[str, List[int]] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        # _ensure_event_loop 并发创建保护（并发 create_sub_agent 时避免创建多个事件循环）
        self._loop_lock = threading.Lock()
        self._state_lock = threading.Lock()
        # 子智能体隔离待办存储：agent_id -> todo dict。
        # 子智能体的 todo_create/todo_update_task 不得写入主智能体的 todo_list，
        # 否则会串到主对话前端快捷菜单显示。
        self._sub_agent_todos: Dict[int, Dict[str, Any]] = {}
        # agent_id -> SubAgentTask 映射（供多智能体消息注入使用）
        self._sub_agent_instances: Dict[int, Any] = {}

        self._load_state()
        try:
            self.reconcile_task_states()
        except Exception:
            pass
        try:
            self.restore_running_tasks()
        except Exception:
            logger.exception("[SubAgentManager] 恢复运行中子智能体任务失败")
            pass

    # ------------------------------------------------------------------
    # 生命周期与事件循环
    # ------------------------------------------------------------------
    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        """确保有一个独立的后台事件循环供子智能体使用。"""
        if self._event_loop is not None and not self._event_loop.is_closed():
            return self._event_loop
        # 加锁 + 双重检查：并发 create_sub_agent 时避免创建多个事件循环，
        # 导致任务散落在不同 loop 上、状态回调与恢复逻辑错乱
        with self._loop_lock:
            if self._event_loop is not None and not self._event_loop.is_closed():
                return self._event_loop

            loop = asyncio.new_event_loop()
            self._event_loop = loop

            def run_loop():
                asyncio.set_event_loop(loop)
                try:
                    loop.run_forever()
                finally:
                    try:
                        loop.close()
                    except Exception:
                        pass

            thread = threading.Thread(target=run_loop, name="sub-agent-loop", daemon=True)
            thread.start()
            self._loop_thread = thread
            return loop

    async def _create_task(self, coro):
        """在事件循环内部把协程包装为 Task。"""
        return asyncio.create_task(coro)

    def _run_coro(self, coro):
        """在后台事件循环中调度一个协程并返回 asyncio.Task。

        调度失败（如循环线程繁忙超时）时必须取消提交并关闭协程，
        否则协程可能在循环空闲后被“幽灵执行”，而调用方已按失败处理，
        造成状态不一致（曾实测：超时返回失败后任务仍运行并交付结果）。
        """
        loop = self._ensure_event_loop()
        # 先提交创建 Task 的协程，阻塞等待拿到 Task 句柄
        future = asyncio.run_coroutine_threadsafe(self._create_task(coro), loop)
        try:
            return future.result(timeout=60)
        except Exception:
            # 取消尚未执行的调度请求，并关闭协程避免 never-awaited/幽灵执行
            future.cancel()
            try:
                coro.close()
            except Exception:
                pass
            raise

    def set_terminal(self, terminal: "WebTerminal") -> None:
        """注入主终端引用，用于工具执行代理。"""
        self.terminal = terminal

    def set_container_session(self, session: Optional["ContainerHandle"]):
        """更新容器会话信息。"""
        self.container_session = session

    def set_host_execution_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        target = "direct" if normalized == "direct" else "sandbox"
        changed = target != self.host_execution_mode
        self.host_execution_mode = target
        if changed:
            # 存活子智能体的工具调用走主终端工具链，脚下环境已实时切换；
            # 注入纯上下文通知告知「语言」变化（Windows 下 bash↔cmd），不触发新一轮工作。
            try:
                self.notify_execution_mode_changed(target)
            except Exception:
                logger.exception("[SubAgent] 执行环境变更通知失败")

    def notify_execution_mode_changed(self, mode: str) -> int:
        """执行环境切换后，向存活的多智能体子智能体注入上下文通知。

        纯通知语义（task.inject_notification）：不唤醒 idle、不触发新一轮工作，
        运行中的子智能体在下一轮模型调用前的安全点看到。
        传统子智能体按既定语义不通知（提示词为创建时快照，任务周期短）。
        返回成功注入的子智能体数。
        """
        normalized = str(mode or "").strip().lower()
        if normalized not in {"sandbox", "direct"}:
            return 0
        from modules.execution_env_text import build_sub_agent_mode_switch_notice

        text = "[系统通知|执行环境变更]\n" + build_sub_agent_mode_switch_notice(normalized)
        target_cid = (
            getattr(getattr(self.terminal, "context_manager", None), "current_conversation_id", None)
            or getattr(self, "owner_conversation_id", None)
        )
        injected = 0
        for inst in list(self._sub_agent_instances.values()):
            try:
                if not getattr(inst, "multi_agent_mode", False):
                    continue
                inst_task = getattr(inst, "_task", None)
                if inst_task is None or inst_task.done():
                    continue
                record = self.tasks.get(getattr(inst, "task_id", "")) or {}
                record_cid = record.get("conversation_id")
                if target_cid and record_cid and record_cid != target_cid:
                    continue
                inst.inject_notification(text)
                injected += 1
            except Exception:
                logger.exception("[SubAgent] 注入执行环境变更通知失败")
        if injected:
            logger.info(f"[SubAgent] 执行环境切换为 {normalized}，已通知 {injected} 个多智能体子智能体")
        return injected

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------
    def create_sub_agent(
        self,
        *,
        agent_id: int,
        summary: str,
        task: str,
        deliverables_dir: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        conversation_id: Optional[str] = None,
        run_in_background: bool = False,
        model_key: Optional[str] = None,
        thinking_mode: Optional[str] = None,
        multi_agent_mode: bool = False,
        role_id: Optional[str] = None,
        display_name: Optional[str] = None,
        system_prompt: Optional[str] = None,
        task_message: Optional[str] = None,
        compress_threshold_tokens: Optional[int] = None,
        max_turns: Optional[int] = None,
    ) -> Dict:
        """创建子智能体任务并启动协程。
        
        参数 multi_agent_mode: True 时启用多智能体模式。
        参数 role_id: 多智能体模式下的角色标诶。
        参数 display_name: 多智能体模式下的显示名（如 UI Operator_1）。
        """
        validation_error = self._validate_create_params(agent_id, summary, task, deliverables_dir, multi_agent_mode=multi_agent_mode)
        if validation_error:
            return {"success": False, "error": validation_error}

        if not thinking_mode:
            return {"success": False, "error": "缺少 thinking_mode 参数，必须指定 fast 或 thinking"}
        if thinking_mode not in {"fast", "thinking"}:
            return {"success": False, "error": "thinking_mode 仅支持 fast 或 thinking"}

        if not conversation_id:
            return {"success": False, "error": "缺少对话ID，无法创建子智能体"}

        if not self._ensure_agent_slot_available(conversation_id, agent_id):
            # 多智能体模式的 agent_id 由系统自动分配且不对外暴露，
            # 走到这里说明自动分配与其他写入路径竞争出错，不应把内部编号抛给模型
            if multi_agent_mode:
                return {
                    "success": False,
                    "error": "内部错误：实例编号分配冲突，请重试创建。"
                }
            return {
                "success": False,
                "error": f"该对话已使用过编号 {agent_id}，请更换新的子智能体代号。"
            }

        if self._active_task_count(conversation_id) >= SUB_AGENT_MAX_ACTIVE:
            return {
                "success": False,
                "error": f"该对话已存在 {SUB_AGENT_MAX_ACTIVE} 个运行中的子智能体，请稍后再试。",
            }

        task_id = self._generate_task_id(agent_id)
        task_root = self.base_dir / task_id
        task_root.mkdir(parents=True, exist_ok=True)

        try:
            deliverables_path = self._resolve_deliverables_dir(deliverables_dir, multi_agent_mode=multi_agent_mode)
        except ValueError as exc:
            # 回滚已创建的任务目录，避免残留状态（交付目录校验失败时 deliverables 不会创建）
            shutil.rmtree(task_root, ignore_errors=True)
            return {"success": False, "error": str(exc)}

        task_file = task_root / "task.txt"
        system_prompt_file = task_root / "system_prompt.txt"
        output_file = task_root / "output.json"
        stats_file = task_root / "stats.json"
        progress_file = task_root / "progress.jsonl"
        conversation_file = task_root / "conversation.json"

        prompt_workspace = self._get_runtime_path(self.project_path)
        deliverables_display = self._get_runtime_path(deliverables_path)
        if task_message:
            user_message = task_message
        else:
            display_timeout = timeout_seconds if timeout_seconds is not None else 0
            user_message = build_user_message(agent_id, summary, task, deliverables_display, display_timeout or SUB_AGENT_DEFAULT_TIMEOUT)
        task_file.write_text(user_message, encoding="utf-8")

        if system_prompt:
            final_system_prompt = system_prompt
        else:
            # 快照当前执行环境写入提示词；后续切换由 notify_execution_mode_changed 补充告知
            final_system_prompt = build_system_prompt(prompt_workspace, execution_mode=self.host_execution_mode)
        system_prompt_file.write_text(final_system_prompt, encoding="utf-8")

        # timeout_seconds 为 None 表示永久子智能体（不会被时间终结）
        task_record = {
            "task_id": task_id,
            "agent_id": agent_id,
            "summary": summary,
            "task": task,
            "status": "running",
            "deliverables_dir": str(deliverables_path),
            "timeout_seconds": timeout_seconds,
            "thinking_mode": thinking_mode,
            "created_at": time.time(),
            "updated_at": time.time(),
            "conversation_id": conversation_id,
            "run_in_background": run_in_background,
            "multi_agent_mode": bool(multi_agent_mode),
            "task_root": str(task_root),
            "output_file": str(output_file),
            "stats_file": str(stats_file),
            "progress_file": str(progress_file),
            "conversation_file": str(conversation_file),
            "model_key": model_key,
            "role_id": role_id,
            "display_name": display_name,
            "execution_mode": "in_process",
            "compress_threshold_tokens": compress_threshold_tokens,
            "max_turns": max_turns,
            "container_name": None,
        }
        # 多智能体模式：为该会话创建或复用 MultiAgentState
        # 注意：状态提交（self.tasks / _mark_agent_id_used / _save_state）必须在
        # 调度成功之后进行，否则调度失败会留下幽灵记录，且 reconcile 会在
        # “记录已存在但 _running_tasks 无句柄”的窗口期把任务误标为 terminated
        multi_agent_state = None
        if multi_agent_mode:
            multi_agent_state = self.get_or_create_multi_agent_state(conversation_id)
            # 把实例注册到 state
            from modules.multi_agent.state import AgentInstance
            inst = AgentInstance(
                agent_id=agent_id,
                role_id=role_id or "",
                display_name=display_name or f"Agent_{agent_id}",
                task_id=task_id,
                status="running",
                summary=summary,
            )
            try:
                multi_agent_state.register_instance(inst)
            except ValueError:
                shutil.rmtree(task_root, ignore_errors=True)
                # 多智能体模式的 agent_id 是内部编号，不把具体值抛给模型
                return {"success": False, "error": "内部错误：实例注册冲突，请重试创建。"}

        sub_agent = SubAgentTask(
            manager=self,
            task_record=task_record,
            task_message=user_message,
            system_prompt=final_system_prompt,
            model_key=model_key,
            thinking_mode=thinking_mode,
            multi_agent_mode=multi_agent_mode,
            multi_agent_state=multi_agent_state,
            display_name=display_name,
        )
        ma_debug(
            "manager_create_sub_agent_state",
            task_id=task_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            state_id=id(multi_agent_state) if multi_agent_state else None,
        )
        task_coro = sub_agent.run()
        try:
            asyncio_task = self._run_coro(task_coro)
        except Exception as exc:
            # 调度失败：完整回滚已创建的资源，避免幽灵任务/残留状态
            try:
                task_coro.close()
            except Exception:
                pass
            if multi_agent_state is not None:
                try:
                    multi_agent_state.agents.pop(agent_id, None)
                    multi_agent_state.task_id_to_agent_id.pop(task_id, None)
                except Exception:
                    pass
            shutil.rmtree(task_root, ignore_errors=True)
            logger.exception(f"[SubAgent] 子智能体调度失败: agent_id={agent_id}, task_id={task_id}")
            return {"success": False, "error": f"子智能体调度失败（事件循环繁忙），请稍后重试: {exc}"}

        # 调度成功后再提交状态：self.tasks 记录与 _running_tasks 句柄同步出现，
        # reconcile_task_states 任何时刻看到该记录都能拿到运行句柄，不会误标 terminated
        self.tasks[task_id] = task_record
        self._mark_agent_id_used(conversation_id, agent_id)
        sub_agent._task = asyncio_task
        self._running_tasks[task_id] = asyncio_task
        # 缓存 sub_agent 实例供给多智能体模式 Poli注入使用
        self._sub_agent_instances[agent_id] = sub_agent
        self._save_state()

        def _on_done(fut):
            try:
                self._running_tasks.pop(task_id, None)
                # 清理该子智能体的隔离待办存储
                self._sub_agent_todos.pop(agent_id, None)
                # 多智能体模式下 failed 视为可复活状态，保留实例引用供后续 send_message_to_sub_agent 重新激活
                if multi_agent_mode:
                    final_task = self.tasks.get(task_id) or {}
                    if final_task.get("status") != "failed":
                        self._sub_agent_instances.pop(agent_id, None)
                else:
                    self._sub_agent_instances.pop(agent_id, None)
                self.reconcile_task_states(conversation_id=conversation_id)
                # 多智能体模式：结束时把状态写回 MultiAgentState
                if multi_agent_mode and multi_agent_state:
                    self._on_multi_agent_task_done(task_id, agent_id, multi_agent_state, sub_agent)
            except Exception as exc:
                logger.exception(f"[SubAgent] task {task_id} 完成回调异常: {exc}")
                ma_debug("manager_on_done_exception", task_id=task_id, agent_id=agent_id, error=str(exc))

        asyncio_task.add_done_callback(_on_done)

        message = f"子智能体{agent_id} 已创建，任务ID: {task_id}"
        if multi_agent_mode and display_name:
            # 多智能体模式：对用户/模型只暴露显示名，task_id 为内部细节不进文案
            message = f"{display_name} 已创建。"
        print(f"{OUTPUT_FORMATS['info']} {message}")
        ma_debug(
            "manager_create_sub_agent",
            task_id=task_id,
            agent_id=agent_id,
            display_name=display_name,
            multi_agent_mode=multi_agent_mode,
            run_in_background=task_record.get("run_in_background"),
            timeout_seconds=timeout_seconds,
        )

        return {
            "success": True,
            "task_id": task_id,
            "agent_id": agent_id,
            "status": "running",
            "message": message,
            "deliverables_dir": str(deliverables_path),
            "run_in_background": run_in_background,
            "display_name": display_name,
        }

    def wait_for_completion(
        self,
        *,
        task_id: Optional[str] = None,
        agent_id: Optional[int] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict:
        """阻塞等待子智能体完成或超时。"""
        task = self._select_task(task_id, agent_id)
        if not task:
            return {"success": False, "error": "未找到对应的子智能体任务"}

        if task.get("status") in TERMINAL_STATUSES or task.get("status") == "terminated":
            if task.get("final_result"):
                return task["final_result"]
            return {"success": False, "status": task.get("status"), "message": "子智能体已结束。"}

        real_task_id = task["task_id"]
        deadline = time.time() + (timeout_seconds or task.get("timeout_seconds") or SUB_AGENT_DEFAULT_TIMEOUT)

        while time.time() < deadline:
            self.reconcile_task_states()
            # 关键：其他线程（如前端轮询 /api/sub_agents）可能调用 _load_state()
            # 并替换 self.tasks 字典，导致旧 task 引用失效。每次循环重新获取引用。
            task = self.tasks.get(real_task_id)
            if not task:
                return {"success": False, "error": "未找到对应的子智能体任务"}
            running_task = self._running_tasks.get(real_task_id)
            status = task.get("status")

            # 已到达终态：返回最终结果（持续 reconcile 直到 final_result 就绪）
            if status in TERMINAL_STATUSES or status == "terminated":
                if task.get("final_result"):
                    return task["final_result"]
                # 终态但 final_result 尚未写入，短暂等待后重试
                time.sleep(SUB_AGENT_STATUS_POLL_INTERVAL)
                self.reconcile_task_states()
                task = self.tasks.get(real_task_id) or task
                if task.get("final_result"):
                    return task["final_result"]
                return {"success": False, "status": status, "message": "子智能体已结束，但未获取到结果。"}

            # asyncio Task 已结束但状态可能还没同步：等待 final_result 就绪
            if running_task and running_task.done():
                self.reconcile_task_states()
                task = self.tasks.get(real_task_id) or task
                if task.get("final_result"):
                    return task["final_result"]
                # 结果尚未落盘，继续轮询，避免把「已创建」误判为失败
                time.sleep(SUB_AGENT_STATUS_POLL_INTERVAL)
                continue

            time.sleep(SUB_AGENT_STATUS_POLL_INTERVAL)

        return self._handle_timeout(task)

    def soft_stop_all_agents(self, conversation_id: str) -> int:
        """软停止指定会话的所有运行中子智能体。

        与 terminate_sub_agent 的区别：不取消 asyncio.Task 而是设 _soft_stop 标志，
        让子智能体在当前工具完成后进入 idle 状态，保留上下文。
        返回实际发出软停止信号的子智能体数量。
        """
        ma_debug("soft_stop_all_agents_enter", conversation_id=conversation_id)
        count = 0
        matched = 0
        skipped_terminal = 0
        skipped_no_instance = 0
        for task_id, task_info in list(self.tasks.items()):
            if task_info.get("conversation_id") != conversation_id:
                continue
            matched += 1
            status = task_info.get("status")
            ma_debug(
                "soft_stop_iter",
                task_id=task_id,
                status=status,
            )
            if status in TERMINAL_STATUSES.union({"terminated", "idle"}):
                skipped_terminal += 1
                continue
            agent_id = task_info.get("agent_id")
            if agent_id is None:
                continue
            # 從 _sub_agent_instances 查找 SubAgentTask 实例
            sub_agent_task = self._sub_agent_instances.get(agent_id)
            if sub_agent_task and hasattr(sub_agent_task, "request_soft_stop"):
                try:
                    sub_agent_task.request_soft_stop()
                    count += 1
                except Exception as exc:
                    ma_debug("soft_stop_failed", task_id=task_id, error=str(exc))
            else:
                # 实例跑在另一个 manager 内存里：写控制文件让其自行软停止
                self._write_sub_agent_control_request(task_info, "soft_stop")
                count += 1
                skipped_no_instance += 1
        ma_debug(
            "soft_stop_all_agents_done",
            conversation_id=conversation_id,
            matched=matched,
            count=count,
            skipped_terminal=skipped_terminal,
            skipped_no_instance=skipped_no_instance,
        )
        return count

    def stop_sub_agent(
        self,
        *,
        task_id: Optional[str] = None,
        agent_id: Optional[int] = None,
    ) -> Dict:
        """暂停指定子智能体，使其进入 idle 状态而不终结。"""
        task = self._select_task(task_id, agent_id, include_idle=True)
        if not task:
            return {"success": False, "error": "未找到对应的子智能体任务"}

        real_task_id = task["task_id"]
        real_agent_id = task.get("agent_id")
        if not task.get("multi_agent_mode"):
            return {"success": False, "error": "stop_sub_agent 仅在多智能体模式下可用"}
        if task.get("status") == "terminated":
            return {"success": False, "error": "子智能体已被终结，无法暂停"}

        # 查找或复活实例，确保能接收软停止信号
        if real_agent_id is not None:
            sub_agent = self._find_or_revive_sub_agent_task(real_agent_id)
        else:
            sub_agent = None

        if sub_agent and hasattr(sub_agent, "request_soft_stop"):
            try:
                sub_agent.request_soft_stop()
            except Exception as exc:
                return {"success": False, "error": f"暂停子智能体失败: {exc}"}
        else:
            # 本地没有活实例：实例可能跑在另一个 manager 内存里，
            # 写控制文件让对端自行软停止；同时先把记录置 idle 作为即时反馈
            self._write_sub_agent_control_request(task, "soft_stop")
            task["status"] = "idle"
            task["updated_at"] = time.time()
            self._save_state()

        # 同步更新 MultiAgentState
        conversation_id = task.get("conversation_id")
        if conversation_id:
            state = self.get_multi_agent_state(conversation_id)
            if state and real_agent_id is not None:
                state.mark_status(real_agent_id, "idle")

        ma_debug(
            "manager_stop_sub_agent",
            task_id=real_task_id,
            agent_id=real_agent_id,
            had_instance=bool(sub_agent),
        )
        display_name = task.get("display_name") or f"子智能体{real_agent_id}"
        return {
            "success": True,
            "task_id": real_task_id,
            "agent_id": real_agent_id,
            "display_name": task.get("display_name") or None,
            "message": f"{display_name} 已暂停，可用 send_message_to_sub_agent 重新激活。",
        }

    def terminate_sub_agent(
        self,
        *,
        task_id: Optional[str] = None,
        agent_id: Optional[int] = None,
    ) -> Dict:
        """强制关闭指定子智能体。

        terminated 是吸收态，必须同时清理四层状态，否则会被复活：
        1) 内存实例（_sub_agent_instances / _running_tasks）——取消并移除；
        2) 任务记录 status —— 标记 terminated；
        3) MultiAgentState 实例状态 —— 标记 terminated；
        4) output.json 快照 —— 改写为 terminated，防止陈旧 idle 快照被读回复活；
        另外写 control.json 作为跨 manager 击杀通道（实例可能跑在别的 manager 内存里）。
        """
        # 多智能体模式下子智能体可能处于 idle，仍需支持终结
        task = self._select_task(task_id, agent_id, include_idle=True)
        if not task:
            return {"success": False, "error": "未找到对应的子智能体任务"}

        task_id = task["task_id"]
        agent_id = task.get("agent_id")
        display_name = task.get("display_name") or f"子智能体{agent_id}"
        ma_debug(
            "terminate_sub_agent_enter",
            task_id=task_id,
            agent_id=agent_id,
            before_status=task.get("status"),
            multi_agent_mode=task.get("multi_agent_mode"),
        )

        # ---- 1) 取消内存中的 asyncio 任务 ----
        # _running_tasks 可能因多 manager 并存/注册缺失而拿不到真正的句柄，
        # 必须同时通过 _sub_agent_instances 找到活实例直接取消它的 _task。
        inst = self._sub_agent_instances.get(agent_id) if agent_id is not None else None
        cancel_targets: List[Any] = []
        if inst is not None:
            # cancel 未送达时的兜底：让 run 循环在下一个 tick 自行退出
            inst._cancelled = True
            inst_task = getattr(inst, "_task", None)
            if inst_task is not None:
                cancel_targets.append(inst_task)
        running_task = self._running_tasks.pop(task_id, None)
        if running_task is not None and running_task not in cancel_targets:
            cancel_targets.append(running_task)
        for target in cancel_targets:
            if target.done():
                continue
            try:
                # 子智能体运行在独立事件循环线程中，取消操作必须投递到该循环
                loop = target.get_loop()
                loop.call_soon_threadsafe(target.cancel)
            except Exception:
                try:
                    target.cancel()
                except Exception:
                    pass
        if cancel_targets:
            deadline = time.time() + 5
            for target in cancel_targets:
                while not target.done() and time.time() < deadline:
                    time.sleep(0.05)

        # 实例从注册表移除，防止后续 inject/revive 找到它直接复活
        if agent_id is not None:
            self._sub_agent_instances.pop(agent_id, None)

        # ---- 2) 任务记录标记 terminated（含实例对应记录，若与选中记录不同） ----
        self._mark_task_terminated(
            task,
            message="子智能体已被强制关闭。",
            system_message=f"🛑 {display_name} 已被手动关闭。",
            notified=True,
        )
        inst_task_id = getattr(inst, "task_id", None) if inst is not None else None
        if inst_task_id and inst_task_id != task_id:
            other_task = self.tasks.get(inst_task_id)
            if other_task and other_task.get("status") not in TERMINAL_STATUSES.union({"terminated"}):
                self._mark_task_terminated(
                    other_task,
                    message="子智能体已被强制关闭。",
                    system_message=f"🛑 {display_name} 已被手动关闭。",
                    notified=True,
                )

        # ---- 3) MultiAgentState 实例状态标记 terminated ----
        conversation_id = task.get("conversation_id")
        if conversation_id and agent_id is not None:
            state = self.get_multi_agent_state(conversation_id)
            if state:
                state.mark_status(agent_id, "terminated")

        # ---- 4) output.json 终态快照 + control.json 跨 manager 击杀信号 ----
        self._write_terminated_output_snapshot(task, display_name=display_name)
        self._write_sub_agent_control_request(task, "terminate")

        self._save_state()

        ma_debug(
            "terminate_sub_agent_done",
            task_id=task_id,
            agent_id=agent_id,
            had_instance=bool(inst),
            cancel_targets=len(cancel_targets),
        )
        return {
            "success": True,
            "task_id": task_id,
            "agent_id": agent_id,
            "display_name": display_name,
            "message": "子智能体已被强制关闭。",
            "system_message": f"🛑 {display_name} 已被手动关闭。",
        }

    def _write_terminated_output_snapshot(self, task: Dict, *, display_name: str = "") -> None:
        """把 output.json 改写为 terminated 终态快照。

        防止子智能体此前周期写入的 {status: idle/running, success: null} 陈旧快照
        被 _check_task_status 读回后把任务记录复活为 idle/running；同时作为跨
        manager 的终结广播：其他 manager 读到 terminated 快照后同步本地记录。
        """
        try:
            raw_output_file = task.get("output_file", "")
            if not raw_output_file:
                return
            output_file = Path(raw_output_file)
            try:
                existing = json.loads(output_file.read_text(encoding="utf-8")) if output_file.exists() else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            existing.update({
                "success": False,
                "status": "terminated",
                "summary": f"{display_name or '子智能体'} 已被手动关闭。",
                "terminated_at": time.time(),
            })
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[terminate] 写入终态快照失败: {exc}")

    def _write_sub_agent_control_request(self, task: Dict, action: str) -> None:
        """向子智能体任务目录写控制请求（跨 manager 信号通道）。

        子智能体的 asyncio 实例可能跑在另一个 manager 的内存里（多 WebTerminal
        并存），本 manager 无法直接 cancel。子智能体运行循环在每个 idle tick /
        每轮开始都会读取 control.json，看到请求后自行执行 terminate/soft_stop。
        """
        try:
            raw_output_file = task.get("output_file", "")
            if not raw_output_file:
                return
            control_file = Path(raw_output_file).parent / "control.json"
            control_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"action": action, "requested_at": time.time()}
            tmp_file = control_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp_file.replace(control_file)
        except Exception as exc:
            logger.warning(f"[control] 写入控制请求失败: {exc}")

    def _latest_task_for_agent(self, agent_id: int, conversation_id: Optional[str] = None) -> Optional[Dict]:
        """返回指定 agent_id 最新一条任务记录（不限状态）。

        agent_id 只在单会话内唯一，tasks 字典跨会话共享，必须按会话过滤，
        否则其他会话的同名 agent 状态会污染判断（如误拒注入）。
        """
        cid = conversation_id or getattr(self, "owner_conversation_id", None)
        candidates = [
            t for t in self.tasks.values()
            if isinstance(t, dict) and t.get("agent_id") == agent_id
            and (not cid or t.get("conversation_id") == cid)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return candidates[0]

    def get_sub_agent_status(
        self,
        *,
        agent_ids: Optional[List[int]] = None,
    ) -> Dict:
        """获取指定子智能体的详细状态。

        对于已结束（completed/failed/timeout/terminated）的子智能体，同样返回其
        最终状态，而不是返回「不存在」。
        """
        if not agent_ids:
            return {"success": False, "error": "必须指定至少一个agent_id"}

        def _find_task_by_agent_id(aid: int):
            # 先查运行中/待运行的任务
            task = self._select_task(None, aid)
            if task:
                return task
            # 再查已结束的任务（按创建时间取最新一条）
            candidates = [
                t for t in self.tasks.values()
                if t.get("agent_id") == aid
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: item.get("created_at", 0), reverse=True)
            return candidates[0]

        results = []
        for agent_id in agent_ids:
            task = _find_task_by_agent_id(agent_id)
            if not task:
                results.append({
                    "agent_id": agent_id,
                    "found": False,
                    "error": "子智能体不存在",
                })
                continue

            status = task.get("status")
            if status not in TERMINAL_STATUSES.union({"terminated"}):
                self._check_task_status(task)
                status = task.get("status")

            stats = {}
            stats_file = Path(task.get("stats_file", ""))
            if stats_file.exists():
                try:
                    stats = json.loads(stats_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            stats_summary = self._build_stats_summary(stats)

            results.append({
                "agent_id": agent_id,
                "found": True,
                "task_id": task["task_id"],
                "status": status,
                "summary": task.get("summary"),
                "display_name": task.get("display_name"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
                "deliverables_dir": task.get("deliverables_dir"),
                "stats": stats,
                "stats_summary": stats_summary,
                "final_result": task.get("final_result"),
            })

        return {"success": True, "results": results}

    def poll_updates(self) -> List[Dict]:
        """检查运行中的子智能体任务，返回新完成的结果。"""
        updates: List[Dict] = []
        self.reconcile_task_states()
        pending_tasks = [
            task for task in self.tasks.values()
            if task.get("status") not in TERMINAL_STATUSES.union({"terminated"})
        ]
        if not pending_tasks:
            return updates

        state_changed = False
        for task in pending_tasks:
            result = self._check_task_status(task)
            if result["status"] in TERMINAL_STATUSES:
                updates.append(result)
                state_changed = True

        if state_changed:
            self._save_state()
        return updates

    def lookup_task(self, *, task_id: Optional[str] = None, agent_id: Optional[int] = None) -> Optional[Dict]:
        """只读查询任务信息。"""
        task = self._select_task(task_id, agent_id)
        if not task:
            return None
        return {
            "task_id": task.get("task_id"),
            "agent_id": task.get("agent_id"),
            "status": task.get("status"),
            "timeout_seconds": task.get("timeout_seconds"),
            "conversation_id": task.get("conversation_id"),
        }

    def get_overview(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """返回子智能体任务概览，用于前端展示。"""
        self.reconcile_task_states(conversation_id=conversation_id)
        overview: List[Dict[str, Any]] = []
        for task_id, task in self.tasks.items():
            if conversation_id and task.get("conversation_id") != conversation_id:
                continue

            snapshot = {
                "task_id": task_id,
                "agent_id": task.get("agent_id"),
                "summary": task.get("summary"),
                "status": task.get("status"),
                "display_name": task.get("display_name") or "",
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
                "target_dir": task.get("target_project_dir"),
                "last_tool": task.get("last_tool"),
                "deliverables_dir": task.get("deliverables_dir"),
                "copied_path": task.get("copied_path"),
                "conversation_id": task.get("conversation_id"),
                "sub_conversation_id": task.get("sub_conversation_id"),
            }

            # 读取 stats 文件获取当前上下文 token
            stats_file = Path(task.get("stats_file", ""))
            if stats_file.exists():
                try:
                    stats = json.loads(stats_file.read_text(encoding="utf-8"))
                    snapshot["current_context_tokens"] = stats.get("current_context_tokens", 0)
                    snapshot["stats_summary"] = self._build_stats_summary(stats)
                except Exception:
                    snapshot["current_context_tokens"] = 0
            else:
                snapshot["current_context_tokens"] = 0

            if snapshot["status"] in TERMINAL_STATUSES or snapshot["status"] == "terminated":
                final_result = task.get("final_result") or {}
                snapshot["final_message"] = final_result.get("system_message") or final_result.get("message")
                snapshot["success"] = final_result.get("success")

            overview.append(snapshot)

        overview.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
        return overview

    # ------------------------------------------------------------------
    # 工具执行代理
    # ------------------------------------------------------------------
    def _execute_sub_agent_todo(self, tool_name: str, arguments: Dict[str, Any], agent_id: Optional[int]) -> Dict[str, Any]:
        """子智能体待办工具：写入该 agent 隔离的存储。

        子智能体的 todo_create/todo_update_task 不得写入主智能体的 todo_list，
        否则会覆盖主对话的待办列表并串到前端快捷菜单显示。
        """
        from modules.todo_manager import TodoManager

        class _TodoContext:
            """适配 TodoManager 所需的最小 context_manager 接口。"""
            def __init__(self, store: Dict[str, Any]):
                self._store = store

            @property
            def todo_list(self):
                return self._store.get("todo")

            def set_todo_list(self, todo):
                self._store["todo"] = todo

        key = agent_id if agent_id is not None else -1
        store = self._sub_agent_todos.setdefault(key, {})
        todo_manager = TodoManager(_TodoContext(store))
        if tool_name == "todo_create":
            return todo_manager.create_todo_list(
                overview=arguments.get("overview", ""),
                tasks=arguments.get("tasks", []),
            )
        if tool_name == "todo_update_task":
            task_indices = arguments.get("task_indices")
            if task_indices is None:
                task_indices = arguments.get("task_index")
            return todo_manager.update_task_status(
                task_indices=task_indices,
                completed=arguments.get("completed", True),
            )
        if tool_name == "todo_get":
            return {"success": True, "todo_list": todo_manager.get_snapshot()}
        return {"success": False, "error": f"未知待办工具: {tool_name}"}

    async def execute_tool_for_sub_agent(self, tool_name: str, arguments: Dict[str, Any], agent_id: Optional[int] = None) -> Dict[str, Any]:
        """代表子智能体在主进程中执行工具。"""
        if not self.terminal:
            return {"success": False, "error": "子智能体管理器未绑定终端，无法执行工具"}

        # 待办工具走子智能体隔离存储，避免覆盖主智能体待办并串到前端显示
        if tool_name in {"todo_create", "todo_update_task", "todo_get"}:
            return self._execute_sub_agent_todo(tool_name, arguments, agent_id)

        try:
            # 多智能体模式常见问答工具已在 SubAgentTask._execute_multi_agent_tool 中处理
            # 这里只处理实际通过主进程执行的工具
            if tool_name == "read_mediafile":
                return await handle_read_mediafile(self.project_path, arguments)

            # 其余工具直接走主进程 handle_tool_call，自然经过沙箱/容器/权限链路
            result_text = await self.terminal.handle_tool_call(tool_name, arguments)
            try:
                return json.loads(result_text)
            except Exception:
                return {"success": True, "output": result_text}
        except Exception as exc:
            logger.exception(f"[SubAgent] 工具执行异常: {tool_name}")
            return {"success": False, "error": f"工具执行异常: {exc}"}

    # ------------------------------------------------------------------
    # 重启后恢复运行中任务
    # ------------------------------------------------------------------
    def restore_running_tasks(self) -> int:
        """程序重启后，从 conversation.json 恢复非终态子智能体任务并重新运行。

        返回成功恢复的任务数。
        """
        from modules.sub_agent.task import SubAgentTask

        restored = 0
        terminal_statuses = TERMINAL_STATUSES.union({"terminated"})
        # 对话级隔离：仅恢复本 manager 绑定对话的任务；未绑定（工作区级
        # 服务 terminal）不恢复，任务延迟到对应对话激活（创建对话级 terminal）时恢复。
        owner_cid = getattr(self, "owner_conversation_id", None)
        for task_id, task in list(self.tasks.items()):
            if not isinstance(task, dict):
                continue
            # 仅恢复多智能体模式任务；传统子智能体保持原有清理逻辑
            if not task.get("multi_agent_mode"):
                continue
            if not owner_cid or task.get("conversation_id") != owner_cid:
                continue
            status = task.get("status", "running")
            if status in terminal_statuses:
                continue
            # 已在内存中运行，无需恢复
            if task_id in self._running_tasks:
                continue

            task_root = Path(task.get("task_root", ""))
            conversation_file = Path(task.get("conversation_file", ""))
            system_prompt_file = task_root / "system_prompt.txt"
            task_message_file = task_root / "task.txt"

            if not conversation_file.exists():
                logger.warning(f"[restore] 任务 {task_id} 的对话文件缺失，无法恢复")
                continue

            try:
                conversation_data = json.loads(conversation_file.read_text(encoding="utf-8"))
                messages = list(conversation_data.get("messages") or [])
            except Exception as exc:
                logger.warning(f"[restore] 读取任务 {task_id} 对话文件失败: {exc}")
                continue

            system_prompt = ""
            if system_prompt_file.exists():
                try:
                    system_prompt = system_prompt_file.read_text(encoding="utf-8")
                except Exception:
                    pass

            task_message = ""
            if task_message_file.exists():
                try:
                    task_message = task_message_file.read_text(encoding="utf-8")
                except Exception:
                    pass

            # 如果对话历史为空，用 task_message 兜底
            if not messages:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": task_message},
                ]

            agent_id = int(task.get("agent_id", 0))
            conversation_id = task.get("conversation_id")
            multi_agent_mode = bool(task.get("multi_agent_mode"))
            thinking_mode = task.get("thinking_mode") or "fast"
            model_key = task.get("model_key")
            display_name = task.get("display_name")
            role_id = task.get("role_id")

            multi_agent_state = None
            if multi_agent_mode and conversation_id:
                multi_agent_state = self.get_or_create_multi_agent_state(conversation_id)
                # 如果 snapshot 里没有该实例，根据 task_record 重建一个
                if multi_agent_state and not multi_agent_state.get_instance(agent_id):
                    from modules.multi_agent.state import AgentInstance
                    inst = AgentInstance(
                        agent_id=agent_id,
                        role_id=role_id or "",
                        display_name=display_name or f"Agent_{agent_id}",
                        task_id=task_id,
                        status=status if status in ("running", "idle") else "running",
                        summary=task.get("summary", ""),
                    )
                    try:
                        multi_agent_state.register_instance(inst)
                    except ValueError:
                        pass

            sub_agent = SubAgentTask(
                manager=self,
                task_record=task,
                task_message=task_message,
                system_prompt=system_prompt,
                model_key=model_key,
                thinking_mode=thinking_mode,
                multi_agent_mode=multi_agent_mode,
                multi_agent_state=multi_agent_state,
                display_name=display_name,
            )
            sub_agent.messages = messages
            # 重启后统一置为 idle，等待主智能体再次发消息才继续
            if multi_agent_mode:
                sub_agent._idle = True
                task["status"] = "idle"
                task["updated_at"] = time.time()
                if multi_agent_state:
                    multi_agent_state.mark_status(agent_id, "idle")
                # 同步落盘 output.json，保证前端状态一致
                try:
                    output_file = Path(task.get("output_file", ""))
                    if output_file.exists():
                        output_data = json.loads(output_file.read_text(encoding="utf-8"))
                    else:
                        output_data = {}
                    output_data["status"] = "idle"
                    output_data["success"] = None
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
                except Exception as exc:
                    logger.warning(f"[restore] 更新任务 {task_id} output 文件失败: {exc}")

            # 恢复的任务提示词是创建时快照，执行环境可能已变化：
            # 注入当前环境告知（纯上下文不触发工作；排序安全由 inject_notification 保证）
            try:
                from modules.execution_env_text import build_sub_agent_restore_notice

                sub_agent.inject_notification(
                    "[系统通知|执行环境]\n" + build_sub_agent_restore_notice(
                        self._get_runtime_path(self.project_path), self.host_execution_mode
                    )
                )
            except Exception:
                logger.warning(f"[restore] 任务 {task_id} 注入执行环境告知失败", exc_info=True)

            task_coro = sub_agent.run()
            asyncio_task = self._run_coro(task_coro)
            sub_agent._task = asyncio_task
            self._running_tasks[task_id] = asyncio_task
            self._sub_agent_instances[agent_id] = sub_agent

            def _on_done(fut, tid=task_id, aid=agent_id, state=multi_agent_state, sa=sub_agent):
                try:
                    self._running_tasks.pop(tid, None)
                    self._sub_agent_instances.pop(aid, None)
                    self.reconcile_task_states(conversation_id=conversation_id)
                    if multi_agent_mode and state:
                        self._on_multi_agent_task_done(tid, aid, state, sa)
                except Exception as exc:
                    logger.exception(f"[SubAgent] restored task {tid} 完成回调异常: {exc}")
                    ma_debug("manager_restore_on_done_exception", task_id=tid, agent_id=aid, error=str(exc))

            asyncio_task.add_done_callback(_on_done)
            restored += 1
            ma_debug(
                "manager_restore_sub_agent",
                task_id=task_id,
                agent_id=agent_id,
                display_name=display_name,
                multi_agent_mode=multi_agent_mode,
                status=status,
                message_count=len(messages),
            )

        if restored:
            self._save_state()
        return restored

    # ------------------------------------------------------------------
    # 多智能体模式：状态管理、外部接口、消息注入
    # ------------------------------------------------------------------
    def get_or_create_multi_agent_state(self, conversation_id: str):
        """获取或为该会话创建 MultiAgentState。"""
        from modules.multi_agent.state import MultiAgentState
        with GLOBAL_MULTI_AGENT_STATES_LOCK:
            state = self.multi_agent_states.get(conversation_id)
            if state:
                ma_debug(
                    "manager_get_or_create_ma_state_reuse",
                    conversation_id=conversation_id,
                    state_id=id(state),
                    manager_id=id(self),
                )
                return state
            state = MultiAgentState(conversation_id=conversation_id)
            self.multi_agent_states[conversation_id] = state
            ma_debug(
                "manager_get_or_create_ma_state_create",
                conversation_id=conversation_id,
                state_id=id(state),
                manager_id=id(self),
            )
            return state

    def get_multi_agent_state(self, conversation_id: str):
        """获取该会话的多智能体状态。"""
        state = self.multi_agent_states.get(conversation_id)
        ma_debug(
            "manager_get_multi_agent_state",
            conversation_id=conversation_id,
            found=bool(state),
            state_id=id(state) if state else None,
            manager_id=id(self),
        )
        return state

    def drop_multi_agent_state(self, conversation_id: str) -> None:
        """删除会话状态（会话结束时调用）。"""
        with GLOBAL_MULTI_AGENT_STATES_LOCK:
            self.multi_agent_states.pop(conversation_id, None)

    def reconcile_task_states(self, conversation_id: Optional[str] = None) -> int:
        """修正运行态任务状态。

        在父类实现前先根据内存中的 MultiAgentState 给旧任务补上 multi_agent_mode
        标记，避免任务记录缺字段导致被当成普通子智能体误判为 failed。
        """
        if conversation_id and conversation_id in self.multi_agent_states:
            state = self.multi_agent_states[conversation_id]
            agent_ids = {a.agent_id for a in state.list_all()}
            for task in self.tasks.values():
                if (
                    isinstance(task, dict)
                    and task.get("conversation_id") == conversation_id
                    and task.get("agent_id") in agent_ids
                    and task.get("multi_agent_mode") is None
                ):
                    task["multi_agent_mode"] = True
                    task["updated_at"] = time.time()
        changed = super().reconcile_task_states(conversation_id=conversation_id)

        # 多智能体模式下，子智能体进入 idle 后底层 asyncio.Task 仍在等待唤醒，
        # 父类 reconcile 会据此把任务标回 running。这里根据内存中的 SubAgentTask
        # 实例重新把 idle 状态写回任务记录，使运行态与 MultiAgentState 保持一致。
        extra_changed = 0
        for task in self.tasks.values():
            if not isinstance(task, dict):
                continue
            if conversation_id and task.get("conversation_id") != conversation_id:
                continue
            if not task.get("multi_agent_mode"):
                continue
            # terminated 是吸收态：idle/running 修正不得触碰终结任务
            if task.get("status") in TERMINAL_STATUSES.union({"terminated"}):
                continue
            agent_id = task.get("agent_id")
            inst = self._sub_agent_instances.get(agent_id) if agent_id else None
            if inst:
                if getattr(inst, "_idle", False):
                    if task.get("status") != "idle":
                        task["status"] = "idle"
                        task["updated_at"] = time.time()
                        ma_debug(
                            "reconcile_task_runtime_state_idle_fix",
                            task_id=task.get("task_id"),
                            agent_id=agent_id,
                        )
                        extra_changed += 1
                elif task.get("status") == "idle":
                    # 子智能体已被唤醒且 _idle=false，但 output 文件或父类 reconcile
                    # 可能仍把任务标为 idle。这里强制同步回 running。
                    task["status"] = "running"
                    task["updated_at"] = time.time()
                    ma_debug(
                        "reconcile_task_runtime_state_running_fix",
                        task_id=task.get("task_id"),
                        agent_id=agent_id,
                    )
                    extra_changed += 1
        if extra_changed:
            self._save_state()
            changed += extra_changed
        return changed

    def inject_message_to_sub_agent(self, agent_id: int, message_text: str) -> bool:
        """同事件循环中向子智能体上下文插入 user 消息。

        适用于 ask_other_agent / send_message_to_sub_agent / answer_sub_agent_question_
        （非阻塞到工具结果的路径）。返回 True 表示成功注入。
        若内存中无运行实例（如 failed 后保留的实例已结束），会尝试从 conversation
        文件重建子智能体（保留原 agent_id 和 role_id）后再注入消息。
        """
        # 终结检查：该 agent 最新任务记录为 terminated 时拒绝注入，
        # 并顺带清理可能残活的内存实例（孤儿实例），防止复活
        latest_task = self._latest_task_for_agent(agent_id)
        if latest_task and latest_task.get("status") == "terminated":
            orphan = self._sub_agent_instances.pop(agent_id, None)
            if orphan is not None:
                orphan._cancelled = True
                orphan_task = getattr(orphan, "_task", None)
                if orphan_task is not None and not orphan_task.done():
                    try:
                        orphan_task.get_loop().call_soon_threadsafe(orphan_task.cancel)
                    except Exception:
                        pass
            ma_debug(
                "inject_message_rejected_terminated",
                agent_id=agent_id,
                task_id=latest_task.get("task_id"),
                had_orphan_instance=bool(orphan),
            )
            return False
        # 查找或复活该 agent_id 对应的 SubAgentTask
        sub_agent = self._find_or_revive_sub_agent_task(agent_id)
        ma_debug(
            "manager_inject_message_to_sub_agent",
            agent_id=agent_id,
            message_preview=str(message_text)[:500],
            found=bool(sub_agent),
            task_id=sub_agent.task_id if sub_agent else None,
        )
        if not sub_agent:
            return False
        sub_agent.inject_message(message_text)
        return True

    def _find_or_revive_sub_agent_task(self, agent_id: int) -> Optional[Any]:
        """查找内存中的 SubAgentTask；不存在或已结束时从磁盘复活（多智能体模式）。"""
        inst = self._find_sub_agent_task_by_agent_id(agent_id)
        if inst is not None:
            task = getattr(inst, "_task", None)
            if task is None or not task.done():
                return inst
            # 实例存在但已结束，需要复活前先清理旧引用
            self._sub_agent_instances.pop(agent_id, None)
        revived = self._revive_sub_agent(agent_id)
        return revived

    def _revive_sub_agent(self, agent_id: int) -> Optional[Any]:
        """从 conversation.json 重建一个多智能体子智能体实例（保留原 agent_id/role_id）。

        用于 failed/idle 等可复活状态被 send_message_to_sub_agent 重新激活的场景。
        """
        from modules.sub_agent.task import SubAgentTask

        candidates = [
            t for t in self.tasks.values()
            if isinstance(t, dict) and t.get("agent_id") == agent_id and t.get("multi_agent_mode")
            and t.get("status") != "terminated"  # terminated 是吸收态，不可复活
        ]
        if not candidates:
            return None
        # 按创建时间取最新一条
        candidates.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        task = candidates[0]
        task_id = task.get("task_id")
        if not task_id:
            return None
        # 已在运行中则不重复重建
        if task_id in self._running_tasks:
            existing_inst = self._sub_agent_instances.get(agent_id)
            if existing_inst is not None:
                return existing_inst

        task_root = Path(task.get("task_root", ""))
        conversation_file = Path(task.get("conversation_file", ""))
        system_prompt_file = task_root / "system_prompt.txt"
        task_message_file = task_root / "task.txt"

        if not conversation_file.exists():
            logger.warning(f"[revive] 任务 {task_id} 的对话文件缺失，无法复活")
            return None

        try:
            conversation_data = json.loads(conversation_file.read_text(encoding="utf-8"))
            messages = list(conversation_data.get("messages") or [])
        except Exception as exc:
            logger.warning(f"[revive] 读取任务 {task_id} 对话文件失败: {exc}")
            return None

        system_prompt = ""
        if system_prompt_file.exists():
            try:
                system_prompt = system_prompt_file.read_text(encoding="utf-8")
            except Exception:
                pass

        task_message = ""
        if task_message_file.exists():
            try:
                task_message = task_message_file.read_text(encoding="utf-8")
            except Exception:
                pass

        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_message},
            ]

        conversation_id = task.get("conversation_id")
        multi_agent_state = None
        if conversation_id:
            multi_agent_state = self.get_or_create_multi_agent_state(conversation_id)
            if multi_agent_state and not multi_agent_state.get_instance(agent_id):
                from modules.multi_agent.state import AgentInstance
                inst = AgentInstance(
                    agent_id=agent_id,
                    role_id=task.get("role_id") or "",
                    display_name=task.get("display_name") or f"Agent_{agent_id}",
                    task_id=task_id,
                    status="idle",
                    summary=task.get("summary", ""),
                )
                try:
                    multi_agent_state.register_instance(inst)
                except ValueError:
                    pass

        sub_agent = SubAgentTask(
            manager=self,
            task_record=task,
            task_message=task_message,
            system_prompt=system_prompt,
            model_key=task.get("model_key"),
            thinking_mode=task.get("thinking_mode") or "fast",
            multi_agent_mode=True,
            multi_agent_state=multi_agent_state,
            display_name=task.get("display_name"),
        )
        sub_agent.messages = messages
        sub_agent._idle = True
        task["status"] = "idle"
        task["updated_at"] = time.time()
        if multi_agent_state:
            multi_agent_state.mark_status(agent_id, "idle")
        # 同步落盘 output.json
        try:
            output_file = Path(task.get("output_file", ""))
            if output_file.exists():
                output_data = json.loads(output_file.read_text(encoding="utf-8"))
            else:
                output_data = {}
            output_data["status"] = "idle"
            output_data["success"] = None
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(json.dumps(output_data, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[revive] 更新任务 {task_id} output 文件失败: {exc}")

        task_coro = sub_agent.run()
        asyncio_task = self._run_coro(task_coro)
        sub_agent._task = asyncio_task
        self._running_tasks[task_id] = asyncio_task
        self._sub_agent_instances[agent_id] = sub_agent

        def _on_done(fut, tid=task_id, aid=agent_id, state=multi_agent_state, sa=sub_agent):
            try:
                self._running_tasks.pop(tid, None)
                # failed 保留实例供复活；其余终态清理
                if state:
                    final_task = self.tasks.get(tid) or {}
                    if final_task.get("status") != "failed":
                        self._sub_agent_instances.pop(aid, None)
                else:
                    self._sub_agent_instances.pop(aid, None)
                self.reconcile_task_states(conversation_id=conversation_id)
                if state:
                    self._on_multi_agent_task_done(tid, aid, state, sa)
            except Exception as exc:
                logger.exception(f"[SubAgent] revived task {tid} 完成回调异常: {exc}")
                ma_debug("manager_revive_on_done_exception", task_id=tid, agent_id=aid, error=str(exc))

        asyncio_task.add_done_callback(_on_done)
        ma_debug(
            "manager_revive_sub_agent",
            task_id=task_id,
            agent_id=agent_id,
            display_name=task.get("display_name"),
            message_count=len(messages),
        )
        return sub_agent

    def _find_sub_agent_task_by_agent_id(self, agent_id: int) -> Optional[Any]:
        """通过遍历创建中的 task 查找活 SubAgentTask 实例。
        
        这是个 helper：在主实现中我们需要保留从 agent_id 到 SubAgentTask 的引用。
        理论上可以在 create_sub_agent 时把 sub_agent 存起来，这里使用 rs safer贪心法：
        遊历 _running_tasks 不为可行，因为 asyncio.Task 不抽不包含 SubAgentTask引用。
        我们改为 `SubAgentTask` 对象列表供查询。
        """
        # 优先查缓存：create_sub_agent 时的字段
        for inst in self._sub_agent_instances.values():
            if inst.agent_id == agent_id:
                return inst
        return None

    def _on_multi_agent_task_done(self, task_id: str, agent_id: int, state: Any, sub_agent: Any) -> None:
        """SubAgentTask 结束回调会调这个更新 MultiAgentState 实例状态。"""
        final_task = self.tasks.get(task_id) or {}
        final_status_before = final_task.get("status")
        ma_debug(
            "manager_on_multi_agent_task_done",
            task_id=task_id,
            agent_id=agent_id,
            sub_agent_idle=getattr(sub_agent, "_idle", False),
            sub_agent_cancelled=getattr(sub_agent, "_cancelled", False),
            task_status_before=final_status_before,
        )
        # 多智能体模式下，子智能体自然进入 idle 后 Task 可能被外部事件循环取消，
        # 或者 reconcile 把 idle 误判为 failed。优先以 SubAgentTask 自身状态为准：
        # - 被手动取消 -> terminated
        # - 自然进入 idle -> idle（可继续接收消息）
        # - 真正异常/超时/finish_task 失败 -> failed/timeout
        if getattr(sub_agent, "_cancelled", False):
            state.mark_status(agent_id, "terminated")
            ma_debug("manager_ma_state_set", agent_id=agent_id, status="terminated", reason="sub_agent_cancelled")
            return
        if getattr(sub_agent, "_idle", False):
            state.mark_status(agent_id, "idle")
            ma_debug("manager_ma_state_set", agent_id=agent_id, status="idle", reason="sub_agent_idle")
            return

        # 兜底：取出当前 task status（由 _finalize_task 设置）
        final_status = final_task.get("status")
        if final_status in TERMINAL_STATUSES:
            state.mark_status(agent_id, final_status, last_output=str(final_task.get("final_result") or ""))
            ma_debug("manager_ma_state_set", agent_id=agent_id, status=final_status, reason="task_terminal_status")
        elif final_status == "terminated":
            state.mark_status(agent_id, "terminated")
            ma_debug("manager_ma_state_set", agent_id=agent_id, status="terminated", reason="task_terminated_status")
        else:
            state.mark_status(agent_id, "idle")
            ma_debug("manager_ma_state_set", agent_id=agent_id, status="idle", reason="fallback_idle")

    def _get_runtime_path(self, host_path: Path) -> str:
        """将宿主机路径映射为容器内路径（仅用于提示展示）。"""
        if not self.container_session or getattr(self.container_session, "mode", None) != "docker":
            return str(host_path)
        mount_path = (getattr(self.container_session, "mount_path", None) or "/workspace").rstrip("/") or "/workspace"
        try:
            relative = host_path.resolve().relative_to(self.project_path)
        except Exception:
            return mount_path
        if str(relative) in {"", "."}:
            return mount_path
        return str(PurePosixPath(mount_path) / PurePosixPath(relative.as_posix()))

