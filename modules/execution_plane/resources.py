"""执行资源装配：不依赖 MainTerminal / Flask / APIClient 的最小执行环境。

定位（见 docs/execution_contract.md「资源与所有权」）：
- `ExecutionResources` 是一组「真实执行资源」的显式容器（文件、命令、终端、后台进程）；
- `create_local_resources(...)` 现场构造这组资源，供薄执行器（独立进程/节点）使用；
- 与 `core/main_terminal.MainTerminal` 无继承、无反向依赖：这里只拿
  `modules.file_manager` / `modules.terminal_ops` / `modules.terminal_manager` /
  `modules.background_command_manager` 四个既有能力模块。

刻意不做的事（避免把「装配」变成「授权」）：
- `project_path` / `data_dir` 必填；依赖模块仍会加载项目配置，隔离启动应在导入前
  设置 `ASTRION_IGNORE_DOTENV=1` 及显式数据路径，不能宣称工厂完全不读配置；
- 不写 `host_sandbox_policy`、不创建/修改任何路径授权、不安装沙箱助手；
- 不做权限裁决：只读身份、网络权限、命令校验都以显式参数交给既有模块执行。
  `readonly` 只影响**新建**持久终端的身份（与 MainTerminal 的
  `terminal_readonly_getter` 语义一致），不改变 `run_command` 的
  `sandbox_write_access`（后者由调用方逐次传入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

_VALID_MODES = {"sandbox", "direct"}
_VALID_NETWORK = {"restricted", "full", "none"}


def _normalize_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in _VALID_MODES:
        raise ValueError(f"execution_mode 必须是 sandbox/direct，收到: {value!r}")
    return mode


def _normalize_network(value: Any) -> str:
    network = str(value or "").strip().lower()
    if network not in _VALID_NETWORK:
        raise ValueError(f"network_permission 必须是 restricted/full/none，收到: {value!r}")
    return network


@dataclass
class ExecutionResources:
    """一组真实执行资源（文件 / 命令 / 终端 / 后台进程）。

    字段顺序：必填的资源在前，行为开关在后；构造后可用 `set_execution_mode`
    与 `set_network_permission` 在运行期调整（会同步到终端管理器的 getter）。
    """

    project_path: str
    data_dir: str
    file_manager: Any
    terminal_ops: Any
    terminal_manager: Any
    background_command_manager: Any
    execution_mode: str = "sandbox"
    network_permission: str = "restricted"
    readonly: bool = True
    container_session: Any = None
    command_validator: Optional[Callable[[str], Tuple[bool, str]]] = None
    _toggles: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # ---- 构造后校验与同步 ----

    def __post_init__(self) -> None:
        self.project_path = str(Path(self.project_path).expanduser().resolve())
        self.data_dir = str(Path(self.data_dir).expanduser().resolve())
        if not Path(self.project_path).is_dir():
            raise ValueError(f"project_path 不是已存在的目录: {self.project_path}")
        self.execution_mode = _normalize_mode(self.execution_mode)
        self.network_permission = _normalize_network(self.network_permission)
        self.readonly = bool(self.readonly)
        for name in (
            "file_manager",
            "terminal_ops",
            "terminal_manager",
            "background_command_manager",
        ):
            if getattr(self, name) is None:
                raise ValueError(f"ExecutionResources.{name} 不能为空")
        # getter 闭包读这个字典：构造期管理器先拿到闭包，资源对象后完成装配。
        self._toggles.update(
            {
                "execution_mode": self.execution_mode,
                "network_permission": self.network_permission,
                "readonly": self.readonly,
            }
        )

    # ---- 只读视图 ----

    @property
    def workspace_root(self) -> Path:
        return Path(self.project_path)

    @property
    def cwd(self) -> Path:
        """默认命令 cwd（与终端默认工作目录一致：工作区根）。"""
        return self.workspace_root

    @property
    def is_docker(self) -> bool:
        session = self.container_session
        return bool(session is not None and getattr(session, "mode", None) == "docker")

    # ---- 运行期开关（同步到已注入的 getter） ----

    def set_execution_mode(self, mode: str) -> str:
        mode = _normalize_mode(mode)
        self.execution_mode = mode
        self._toggles["execution_mode"] = mode
        for target in (self.file_manager, self.terminal_ops, self.terminal_manager):
            setter = getattr(target, "set_host_execution_mode", None)
            if callable(setter):
                setter(mode)
        return mode

    def set_network_permission(self, network: str) -> str:
        network = _normalize_network(network)
        self.network_permission = network
        self._toggles["network_permission"] = network
        return network

    def set_readonly(self, readonly: bool) -> bool:
        """只影响**后续新建**的持久终端身份，已打开会话不重建。"""
        self.readonly = bool(readonly)
        self._toggles["readonly"] = self.readonly
        return self.readonly

    def get_network_permission(self) -> str:
        return str(self._toggles.get("network_permission") or self.network_permission)

    def get_readonly(self) -> bool:
        return bool(self._toggles.get("readonly", self.readonly))

    # ---- 生命周期 ----

    def close(self) -> None:
        """关闭本资源持有的终端会话（不改动文件/后台记录）。"""
        closer = getattr(self.terminal_manager, "close_all", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass


def create_local_resources(
    *,
    project_path: str,
    data_dir: str,
    execution_mode: str = "sandbox",
    network_permission: str = "restricted",
    readonly: bool = True,
    container_session: Any = None,
    command_validator: Optional[Callable[[str], Tuple[bool, str]]] = None,
) -> ExecutionResources:
    """构造本机执行资源（Host 或已注入的 Docker 会话）。

    参数全部显式；容器会话需由调用方提供（本函数不建容器、不装沙箱）。
    `command_validator` 缺省时沿用 `TerminalOperator._validate_command`
    （基础危险命令校验）；个人 command_blocking 规则仍由调用方在编排层执行。
    """
    # 局部导入：让 `import modules.execution_plane.local` 不连带这些模块，
    # 也避免 resources 在模块级形成对 config/终端实现的硬依赖。
    from modules.background_command_manager import BackgroundCommandManager
    from modules.file_manager import FileManager
    from modules.terminal_manager import TerminalManager
    from modules.terminal_ops import TerminalOperator

    project = str(Path(project_path).expanduser().resolve())
    data = str(Path(data_dir).expanduser().resolve())
    mode = _normalize_mode(execution_mode)
    network = _normalize_network(network_permission)
    toggles: Dict[str, Any] = {
        "execution_mode": mode,
        "network_permission": network,
        "readonly": bool(readonly),
    }

    file_manager = FileManager(project, container_session=container_session, data_dir=data)
    terminal_ops = TerminalOperator(project, container_session=container_session, data_dir=data)
    validator = command_validator or terminal_ops._validate_command
    terminal_manager = TerminalManager(
        project_path=project,
        container_session=container_session,
        network_permission_getter=lambda: str(toggles.get("network_permission") or network),
        terminal_readonly_getter=lambda: bool(toggles.get("readonly", readonly)),
        command_validator=validator,
    )
    terminal_ops.attach_terminal_manager(terminal_manager)
    background_command_manager = BackgroundCommandManager(project_path=project)

    for target in (file_manager, terminal_ops, terminal_manager):
        setter = getattr(target, "set_host_execution_mode", None)
        if callable(setter):
            setter(mode)

    return ExecutionResources(
        project_path=project,
        data_dir=data,
        file_manager=file_manager,
        terminal_ops=terminal_ops,
        terminal_manager=terminal_manager,
        background_command_manager=background_command_manager,
        execution_mode=mode,
        network_permission=network,
        readonly=bool(readonly),
        container_session=container_session,
        command_validator=validator,
        _toggles=toggles,
    )


__all__ = ["ExecutionResources", "create_local_resources"]
