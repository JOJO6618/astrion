"""内存终端替身：``FakeExecutionBackend.terminals`` 的实现。

只有会话表与输出缓冲，没有 pty、没有 shell 进程、没有读线程。

与真实 ``TerminalManager`` / ``PersistentTerminal`` 的差异（测试必须看得见）
-----------------------------------------------------------------------
- 不启动 shell：``send_to_terminal`` 只把固定回显追加到缓冲并立刻返回完成，
  永不出现 ``timeout`` 状态；
- 不做命令校验/权限裁决/工作目录存在性检查（那是管理器与 sandbox 的职责），
  ``working_dir`` 原样记录；
- 不做真实截断语义：``lines``/``max_chars`` 只是按行数/字符数取尾部，并在
  ``truncated`` 里标记，不影响缓冲内容；
- 不模拟 subshell、Ctrl-C、多会话并发。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

FAKE_TERMINAL_SHELL = "fake-shell"


class FakeTerminals:
    """终端会话表（内存）。"""

    def __init__(self, *, clock: Optional[Any] = None, echo_prefix: str = "[fake-terminal]") -> None:
        self._clock = clock or time.time
        self.echo_prefix = echo_prefix
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.active: Optional[str] = None
        self.calls: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _session(self, session_name: Optional[str]) -> Optional[str]:
        if session_name:
            return session_name if session_name in self.sessions else None
        return self.active

    def _ok(self, session: str, **extra: Any) -> Dict[str, Any]:
        payload = {"success": True, "session": session}
        payload.update(extra)
        return payload

    def _err(self, error: str, **extra: Any) -> Dict[str, Any]:
        payload = {"success": False, "error": error, "status": "error", "output": error}
        payload.update(extra)
        return payload

    # ------------------------------------------------------------------
    # 会话生命周期
    # ------------------------------------------------------------------
    def open_terminal(
        self,
        session_name: str = "default",
        working_dir: Optional[str] = None,
        make_active: bool = True,
    ) -> Dict[str, Any]:
        self.calls.append({"op": "open_terminal", "session": session_name})
        if session_name in self.sessions:
            return self._err(
                f"session already exists: {session_name}",
                existing_sessions=sorted(self.sessions),
            )
        self.sessions[session_name] = {
            "output": [],
            "working_dir": working_dir,
            "created_at": float(self._clock()),
            "is_running": True,
        }
        if make_active or self.active is None:
            self.active = session_name
        return self._ok(
            session_name,
            working_dir=working_dir if working_dir is not None else ".",
            shell=FAKE_TERMINAL_SHELL,
            is_active=self.active == session_name,
            total_sessions=len(self.sessions),
        )

    def close_terminal(self, session_name: str = "default") -> Dict[str, Any]:
        self.calls.append({"op": "close_terminal", "session": session_name})
        if session_name not in self.sessions:
            return self._err(f"session not found: {session_name}", existing_sessions=sorted(self.sessions))
        self.sessions.pop(session_name, None)
        if self.active == session_name:
            self.active = sorted(self.sessions)[0] if self.sessions else None
        return self._ok(session_name, closed=True, active=self.active)

    def reset_terminal(self, session_name: Optional[str] = None) -> Dict[str, Any]:
        target = self._session(session_name)
        self.calls.append({"op": "reset_terminal", "session": target})
        if target is None:
            return self._err("no active terminal session")
        self.sessions[target]["output"] = []
        return self._ok(target, reset=True)

    def switch_terminal(self, session_name: str) -> Dict[str, Any]:
        self.calls.append({"op": "switch_terminal", "session": session_name})
        if session_name not in self.sessions:
            return self._err(f"session not found: {session_name}", existing_sessions=sorted(self.sessions))
        self.active = session_name
        return self._ok(session_name, active=session_name)

    def list_terminals(self) -> Dict[str, Any]:
        return {
            "success": True,
            "terminals": self.get_terminal_list(),
            "active": self.active,
            "total_sessions": len(self.sessions),
        }

    def close_all(self) -> None:
        self.sessions.clear()
        self.active = None

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------
    def send_to_terminal(
        self,
        command: str,
        session_name: Optional[str] = None,
        output_wait: float = 10,
    ) -> Dict[str, Any]:
        target = self._session(session_name)
        self.calls.append({
            "op": "send_to_terminal",
            "session": target,
            "command": command,
            "output_wait": output_wait,
        })
        if target is None:
            return self._err("no active terminal session")
        echo = f"{self.echo_prefix} {command}"
        self.sessions[target]["output"].append(echo + "\n")
        return self._ok(
            target,
            command=command,
            output=echo + "\n",
            message="",
            status="completed",
            truncated=False,
            elapsed_ms=0,
            timeout=output_wait,
            output_wait=output_wait,
            never_timeout=False,
        )

    def get_terminal_output(
        self, session_name: Optional[str] = None, last_n_lines: int = 50
    ) -> Dict[str, Any]:
        target = self._session(session_name)
        if target is None:
            return self._err("no active terminal session")
        lines = self.sessions[target]["output"]
        selected = lines[-last_n_lines:] if last_n_lines else lines
        return self._ok(
            target,
            output="".join(selected),
            total_lines=len(lines),
            truncated=len(selected) < len(lines),
        )

    def get_terminal_snapshot(
        self,
        session_name: Optional[str] = None,
        lines: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        target = self._session(session_name)
        if target is None:
            return self._err("no active terminal session")
        buffer = self.sessions[target]["output"]
        line_limit = None
        if lines is not None and lines > 0:
            line_limit = lines
        selected = buffer[-line_limit:] if line_limit else list(buffer)
        text = "".join(selected)
        char_limit = max_chars if (max_chars and max_chars > 0) else None
        truncated = False
        if char_limit is not None and len(text) > char_limit:
            text = text[-char_limit:]
            truncated = True
        return self._ok(
            target,
            output=text,
            total_lines=len(buffer),
            line_limit=line_limit,
            char_limit=char_limit,
            truncated=truncated or len(selected) < len(buffer),
        )

    def get_terminal_list(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": name,
                "is_active": name == self.active,
                "is_running": bool(session["is_running"]),
                "working_dir": session["working_dir"] if session["working_dir"] is not None else ".",
            }
            for name, session in self.sessions.items()
        ]


__all__ = ["FakeTerminals", "FAKE_TERMINAL_SHELL"]
