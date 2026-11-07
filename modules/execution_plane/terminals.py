"""持久终端视图：LocalExecutionBackend.terminals 的稳定接口面。

定位：把 `modules.terminal_manager.TerminalManager` 的现有会话操作原样暴露给
执行层调用方（open/close/reset/switch/list/input/snapshot/close_all），不引入
新语义，也不做只读/权限判定——只读身份、网络权限与命令校验由管理器在创建
会话时按注入的 getter/validator 决定（见 `modules/terminal_manager.py`）。

同步方法：`send_to_terminal` 会阻塞到 output_wait 结束，调用方需要并发时应
自行放进 `asyncio.to_thread`（与现有编排层一致）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class TerminalSessions:
    """`TerminalManager` 的执行层视图（纯转发）。"""

    CONTRACT_METHODS = (
        "open_terminal",
        "close_terminal",
        "reset_terminal",
        "switch_terminal",
        "list_terminals",
        "send_to_terminal",
        "get_terminal_output",
        "get_terminal_snapshot",
        "get_terminal_list",
        "close_all",
    )

    def __init__(self, manager: Any) -> None:
        if manager is None:
            raise ValueError("TerminalSessions 需要有效的 terminal_manager。")
        self._manager = manager

    @property
    def manager(self) -> Any:
        """底层 TerminalManager（逃生口）。"""
        return self._manager

    @property
    def active(self) -> Optional[str]:
        return getattr(self._manager, "active_terminal", None)

    @property
    def names(self) -> List[str]:
        return sorted(getattr(self._manager, "terminals", {}) or {})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._manager, name)

    # ---- 会话生命周期 ----

    def open(
        self,
        session_name: str = "default",
        working_dir: Optional[str] = None,
        make_active: bool = True,
    ) -> Dict[str, Any]:
        return self._manager.open_terminal(
            session_name=session_name, working_dir=working_dir, make_active=make_active
        )

    # 兼容既有命名（与 TerminalManager 同名同参）
    def open_terminal(
        self,
        session_name: str = "default",
        working_dir: Optional[str] = None,
        make_active: bool = True,
    ) -> Dict[str, Any]:
        return self.open(session_name, working_dir=working_dir, make_active=make_active)

    def close(self, session_name: str = "default") -> Dict[str, Any]:
        return self._manager.close_terminal(session_name=session_name)

    def close_terminal(self, session_name: str = "default") -> Dict[str, Any]:
        return self.close(session_name)

    def reset(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        return self._manager.reset_terminal(session_name=session_name)

    def reset_terminal(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        return self.reset(session_name)

    def switch(self, session_name: str) -> Dict[str, Any]:
        return self._manager.switch_terminal(session_name)

    def switch_terminal(self, session_name: str) -> Dict[str, Any]:
        return self.switch(session_name)

    def list_sessions(self) -> Dict[str, Any]:
        return self._manager.list_terminals()

    def list_terminals(self) -> Dict[str, Any]:
        return self.list_sessions()

    def close_all(self) -> Any:
        return self._manager.close_all()

    # ---- IO ----

    def send(
        self,
        command: str,
        session_name: Optional[str] = None,
        output_wait: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._manager.send_to_terminal(command, session_name, output_wait)

    def send_to_terminal(
        self,
        command: str,
        session_name: Optional[str] = None,
        output_wait: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self.send(command, session_name, output_wait)

    def output(
        self, session_name: Optional[str] = None, last_n_lines: int = 50
    ) -> Dict[str, Any]:
        return self._manager.get_terminal_output(session_name, last_n_lines)

    def get_terminal_output(
        self, session_name: Optional[str] = None, last_n_lines: int = 50
    ) -> Dict[str, Any]:
        return self.output(session_name, last_n_lines)

    def snapshot(
        self,
        session_name: Optional[str] = None,
        lines: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._manager.get_terminal_snapshot(session_name, lines, max_chars)

    def get_terminal_snapshot(
        self,
        session_name: Optional[str] = None,
        lines: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self.snapshot(session_name, lines, max_chars)

    def get_terminal_list(self) -> List[Dict[str, Any]]:
        return self._manager.get_terminal_list()


__all__ = ["TerminalSessions"]
