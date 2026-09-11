"""系统状态重置与对话级 terminal 24h TTL 回收器。"""
from __future__ import annotations

import os
import time
from typing import Optional

from core.web_terminal import WebTerminal
from server import state
from server.utils_common import debug_log


def reset_system_state(terminal: Optional[WebTerminal]):
    """完整重置系统状态"""
    if not terminal:
        return
    try:
        if hasattr(terminal, 'current_session_id'):
            terminal.current_session_id += 1
            debug_log(f"重置会话ID为: {terminal.current_session_id}")
        web_attrs = ['streamingMessage', 'currentMessageIndex', 'preparingTools', 'activeTools']
        for attr in web_attrs:
            if hasattr(terminal, attr):
                if attr in ['streamingMessage']:
                    setattr(terminal, attr, False)
                elif attr in ['currentMessageIndex']:
                    setattr(terminal, attr, -1)
                elif attr in ['preparingTools', 'activeTools'] and hasattr(getattr(terminal, attr), 'clear'):
                    getattr(terminal, attr).clear()
        debug_log("系统状态重置完成")
    except Exception as e:
        debug_log(f"状态重置过程中出现错误: {e}")
        import traceback
        debug_log(f"错误详情: {traceback.format_exc()}")


# ====== 对话级 terminal 24h TTL 回收器 ======
# 对话级 terminal（key 为 username::workspace_id::conversation_id 三段）常驻内存，
# 仅当「超过 TTL 无活动 且 该对话无运行中工作」时回收；工作区级服务 terminal 不回收。
CONVERSATION_TERMINAL_TTL_SECONDS = float(os.environ.get("CONVERSATION_TERMINAL_TTL_SECONDS", str(24 * 3600)))
CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS = float(os.environ.get("CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS", "600"))
_conversation_terminal_reaper_started = False


def _conversation_terminal_has_running_work(
    username: str,
    workspace_id: str,
    conversation_id: str,
    terminal: WebTerminal,
) -> bool:
    """判定对话是否仍有运行中的工作（主任务/子智能体/后台命令/多智能体）。

    判定失败时保守返回 True（不回收）。
    """
    try:
        from server.tasks import task_manager
        active_statuses = {"pending", "running", "cancel_requested"}
        for rec in task_manager.list_tasks(username, workspace_id):
            if rec.conversation_id == conversation_id and rec.status in active_statuses:
                return True
        status = task_manager.get_conversation_running_status(terminal, conversation_id)
        if any(bool(v) for v in (status or {}).values()):
            return True
    except Exception as exc:
        debug_log(f"[ConvTerminalReaper] 运行状态判定失败 {conversation_id}: {exc}")
        return True
    return False


def reap_idle_conversation_terminals(now: Optional[float] = None) -> int:
    """回收超过 TTL 且无运行任务的对话级 terminal，返回回收数量（可测试）。"""
    now = now or time.time()
    reaped = 0
    for term_key, terminal in list(state.user_terminals.items()):
        parts = term_key.split("::")
        if len(parts) < 3:
            continue  # 工作区级服务 terminal 不回收
        username, workspace_id = parts[0], parts[1]
        conversation_id = "::".join(parts[2:])
        try:
            last_active = float(getattr(terminal, "last_activity_at", None))
        except (TypeError, ValueError):
            last_active = None
        if last_active is None:
            # 无时间戳实例（旧版本创建）：补上当前时间，下轮再判定
            try:
                terminal.last_activity_at = now
            except Exception:
                pass
            continue
        if now - last_active < CONVERSATION_TERMINAL_TTL_SECONDS:
            continue
        if _conversation_terminal_has_running_work(username, workspace_id, conversation_id, terminal):
            continue
        # 竞态防护：判定到关闭之间存在窗口，期间新请求可能拿到该实例并建任务。
        # 先打关闭标记（get_user_resources 见到标记会原地重建新实例），
        # 并二次确认活动时间/运行工作未变化，最后 pop 时校验实例身份。
        try:
            terminal._reaper_closing = True
        except Exception:
            pass
        aborted = False
        latest_active = float(getattr(terminal, "last_activity_at", 0) or 0)
        if latest_active > last_active:
            debug_log(f"[ConvTerminalReaper] 关闭前检测到新活动，取消回收: {term_key}")
            aborted = True
        elif _conversation_terminal_has_running_work(username, workspace_id, conversation_id, terminal):
            debug_log(f"[ConvTerminalReaper] 关闭前检测到运行任务，取消回收: {term_key}")
            aborted = True
        if aborted:
            try:
                terminal._reaper_closing = False
            except Exception:
                pass
            continue
        try:
            cm = getattr(terminal, "context_manager", None)
            # 与 __del__ 同理：空 history 保存会把磁盘上非空对话覆盖为空
            if cm and getattr(cm, "current_conversation_id", None) and getattr(cm, "conversation_history", None):
                cm.save_current_conversation()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 保存对话失败 {conversation_id}: {exc}")
        try:
            tm = getattr(terminal, "terminal_manager", None)
            if tm:
                tm.close_all()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 关闭 shell 失败 {conversation_id}: {exc}")
        try:
            mcp = getattr(terminal, "mcp_client_manager", None)
            if mcp:
                mcp.close_all_clients()
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 关闭 MCP 失败 {conversation_id}: {exc}")
        # 仅当缓存里仍是本实例时才移除（可能已被请求侧原地重建）
        if state.user_terminals.get(term_key) is terminal:
            state.user_terminals.pop(term_key, None)
            reaped += 1
            debug_log(f"[ConvTerminalReaper] 已回收对话级 terminal: {term_key} (idle {int(now - last_active)}s)")
    return reaped


def _conversation_terminal_reaper_loop():
    """后台循环：定期扫描回收空闲对话级 terminal。"""
    while True:
        try:
            reap_idle_conversation_terminals()
            time.sleep(CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS)
        except Exception as exc:
            debug_log(f"[ConvTerminalReaper] 后台循环异常: {exc}")
            time.sleep(CONVERSATION_TERMINAL_REAP_INTERVAL_SECONDS)


def start_conversation_terminal_reaper():
    """幂等启动对话级 terminal TTL 回收后台线程。"""
    global _conversation_terminal_reaper_started
    if _conversation_terminal_reaper_started:
        return
    _conversation_terminal_reaper_started = True
    threading.Thread(target=_conversation_terminal_reaper_loop, daemon=True).start()
