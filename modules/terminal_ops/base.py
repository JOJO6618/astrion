# modules/terminal_ops/base.py - 终端操作器基础类（Host-only）
#
# 本仓库是 Host-only 主仓：命令执行只支持宿主机（Host 沙箱或 direct）。
# 历史上这里挂着 Docker 容器会话句柄，命令经 ``docker exec`` 进入用户容器；
# 该路径已拆到独立 Docker fork。这里保留 ``container_session`` 构造参数只为
# 兼容仍在传 ``None``/host 句柄的旧调用点，任何非空 Docker 句柄都会明确报错，
# 绝不静默降级成宿主机执行。

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from modules.terminal_manager import TerminalManager


def reject_docker_session(session, *, component: str) -> None:
    """Host-only 构建统一拒绝非空 Docker 会话，避免回落到宿主机。

    ``None`` 与 host 句柄继续接受：迁移期旧调用点仍会传它们。
    """
    if session is None:
        return
    if getattr(session, "mode", None) == "docker":
        raise ValueError(
            f"{component} 是 Host-only 构建，不支持 Docker 容器会话；"
            "请使用独立 Docker fork，或移除容器句柄后重试。"
        )


class TerminalOperatorBase:
    """TerminalOperator 基础类。"""

    def __init__(
        self,
        project_path: str,
        container_session=None,
        data_dir: Optional[str] = None,
    ):
        reject_docker_session(container_session, component="TerminalOperator")
        self.project_path = Path(project_path).resolve()
        self.data_dir = Path(data_dir).expanduser() if data_dir is not None else None
        self.process = None
        # Host-only：不再保存容器句柄，执行目标固定为宿主机。
        self.container_session = None
        # 记录 TerminalManager 引用（CLI / 工作树等旧调用点仍会注入）。
        # Host-only 下不再用它复用容器环境，保留属性只为调用方兼容。
        self._terminal_manager: Optional["TerminalManager"] = None
        self.host_execution_mode: str = "sandbox"

    def set_host_execution_mode(self, mode: str) -> None:
        normalized = str(mode or "").strip().lower()
        self.host_execution_mode = "direct" if normalized == "direct" else "sandbox"

    def attach_terminal_manager(self, manager: Optional["TerminalManager"]):
        """由 MainTerminal/WebTerminal 注入 TerminalManager（保留调用接口）。"""
        self._terminal_manager = manager

    def set_container_session(self, session) -> None:
        """兼容旧调用点：只接受 None / host 句柄，Docker 会话明确拒绝。"""
        reject_docker_session(session, component="TerminalOperator")
        self.container_session = None

    def _assert_host_execution_target(self) -> None:
        """防御性检查：属性被外部直接改写为 Docker 句柄时也拒绝执行。"""
        reject_docker_session(
            getattr(self, "container_session", None),
            component="TerminalOperator",
        )
