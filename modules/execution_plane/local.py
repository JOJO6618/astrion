"""LocalExecutionBackend：真实 Host/Docker 的薄适配执行后端。

定位：把「执行层契约」落到本机既有实现上，供两类调用方使用：
1. 完整 App：`LocalExecutionBackend(web_terminal)`，owner 就是终端实例
   （其 file_manager / terminal_manager / background_command_manager 已是权威）；
2. 薄执行环境：`create_local_resources(...)` + `LocalExecutionBackend(resources)`，
   不需要 MainTerminal、Flask、APIClient、模型配置与会话库。

与替身后端（`fake.py`）的区别：
- `is_local = True`：请求真的会在本机产生副作用；
- `supports_full_access = True`：具备宿主机真实执行能力（是否允许单次完全访问
  仍由权限层与 `execution_scope.validate_full_access_request` 裁决，见局部说明）；
- 不缓存结果、不伪造状态：`run_command` 的输出/退出码/超时/取消全部来自
  `TerminalOperator`，`get_environment()` 只报告真实探测到的本机事实。

刻意不做：不判定权限、不做审批、不写路径授权；`sandbox_write_access` /
`network_permission` 是**上游裁决结论**，这里只透传。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.execution_plane.processes import BackgroundProcesses
from modules.execution_plane.terminals import TerminalSessions

# owner 上可能提供的执行资源（按需解析，构造时不校验：存量精简 owner 只给 terminal_ops 也要能用）
_OWNER_RESOURCES = (
    "project_path",
    "data_dir",
    "file_manager",
    "terminal_ops",
    "terminal_manager",
    "background_command_manager",
)


class LocalExecutionBackend:
    """真实本机执行后端（Host 或已注入的 Docker 容器会话）。

    资源动态解析约定：
    - 构造只保存 ``owner`` 引用，**不触资源、不校验完整度**；轻量查询（只看
      capabilities / get_environment）不会创建任何执行资源。
    - ``owner`` 可在运行期整体替换（模块缓存重建、终端重建等场景）；每个属性/
      操作都从 ``self.owner`` 重新解析，不缓存资源对象本身。
    - 每个操作只检查它自己需要的资源：缺失时抛带资源名的 ``RuntimeError``，
      而不是“后端整体不可用”。
    - ``background`` 允许缺失（历史上存在没有后台管理器的精简 owner），
      缺位时该属性为 ``None``；需要后台能力时自行判空或调用
      ``run_command_background``（它会抛清晰错误）。
    """

    backend_name = "local"
    is_local = True
    supports_full_access = True

    #: 后端**声明的契约面**（静态值，不反映当前 owner 实际持有的资源）。
    #: 真实可用性请看 `get_environment()["capabilities"]`（逐项实测）。
    capabilities = {
        "run_command": True,
        "run_command_background": True,
        "cancel_background": True,
        "write_file": True,
        "edit_file": True,
        "terminals": True,
        "full_access": True,
    }

    def __init__(self, owner: Any) -> None:
        if owner is None:
            raise ValueError("LocalExecutionBackend 需要 owner（ExecutionResources 或终端实例）。")
        # 只保存引用：不校验资源完整性，不创建任何执行资源（轻量 query 也要能构造）。
        self.owner = owner

    # ------------------------------------------------------------------
    # 按需解析
    # ------------------------------------------------------------------

    def _resource(self, name: str) -> Any:
        """从当前 owner 取一个资源；缺失时抛带资源名的清晰错误。"""
        owner = self.owner
        value = getattr(owner, name, None)
        if value is None:
            raise RuntimeError(
                f"LocalExecutionBackend 需要 owner.{name}，但当前 owner "
                f"({type(owner).__name__}) 未提供该资源。"
            )
        return value

    def _optional(self, name: str) -> Any:
        return getattr(self.owner, name, None)

    # ------------------------------------------------------------------
    # 资源视图
    # ------------------------------------------------------------------

    @property
    def project_path(self) -> str:
        return str(self._resource("project_path"))

    @property
    def workspace_root(self) -> Path:
        return Path(self.project_path).expanduser().resolve()

    @property
    def execution_mode(self) -> str:
        value = getattr(self.owner, "execution_mode", None)
        if value is None:
            getter = getattr(self.owner, "get_execution_mode", None)
            value = getter() if callable(getter) else "sandbox"
        return str(value or "sandbox").strip().lower()

    @property
    def network_permission(self) -> str:
        getter = getattr(self.owner, "get_network_permission", None)
        if callable(getter):
            return str(getter() or "restricted")
        return str(getattr(self.owner, "network_permission", None) or "restricted")

    @property
    def container_session(self) -> Any:
        return getattr(self.owner, "container_session", None)

    @property
    def files(self) -> Any:
        """每次访问都基于当前 owner.file_manager 新建 LocalFileSystem（无跨请求状态）。

        files 子协议已确定存在：缺失 file_manager 时抛清晰错误，不做任何回退。
        """
        from modules.execution_plane.files import LocalFileSystem

        return LocalFileSystem(self._resource("file_manager"))

    @property
    def terminals(self) -> TerminalSessions:
        """当前 owner 的持久终端视图；owner.terminal_manager 缺失时抛清晰错误。"""
        manager = self._resource("terminal_manager")
        sessions = getattr(self, "_terminals_view", None)
        if sessions is None or getattr(sessions, "manager", None) is not manager:
            sessions = TerminalSessions(manager)
            self._terminals_view = sessions
        return sessions

    @property
    def background(self) -> Optional[BackgroundProcesses]:
        """后台命令视图；owner 无 background_command_manager 时为 ``None``。"""
        manager = self._optional("background_command_manager")
        if manager is None:
            return None
        view = getattr(self, "_background_view", None)
        if view is None or getattr(view, "manager", None) is not manager:
            view = BackgroundProcesses(manager)
            self._background_view = view
        return view

    # ------------------------------------------------------------------
    # E1 前台命令
    # ------------------------------------------------------------------

    async def run_command(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        sandbox_write_access: bool = True,
        network_permission: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """前台执行命令（真子进程 / 沙箱），返回既有结构。

        取消：调用方取消本协程即可——`TerminalOperator` 的
        CancelledError 路径会 killpg(SIGINT→SIGKILL) 并返回
        `status="cancelled"`（保留既有 kill 行为，不做静默降级）。

        注意：`timeout=None` 不会被替换成默认值，仍走原有
        「timeout_required」错误分支；超时上限钳制属编排层职责。
        """
        return await self._resource("terminal_ops").run_command(
            command,
            working_dir=working_dir,
            timeout=timeout,
            sandbox_write_access=bool(sandbox_write_access),
            network_permission=network_permission or self.network_permission,
        )

    # ------------------------------------------------------------------
    # E2 后台命令
    # ------------------------------------------------------------------

    async def run_command_background(
        self,
        command: str,
        *,
        timeout: Optional[float] = None,
        conversation_id: Optional[str] = None,
        wait_seconds: float = 5.0,
        network_permission: Optional[str] = None,
        sandbox_write_access: bool = True,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """登记后台命令（异步，不阻塞事件循环）。

        走 `modules.background_command_start.start_background_command`：
        它负责「取消发生在 command_id 分配前」的握手——取消事件已置位时，
        创建完成的瞬间补发 `cancel_command`，避免取消丢失或孤儿进程。
        不要退回直接调用同步 `create_background_command` 的旧写法。
        """
        from modules.background_command_start import start_background_command

        manager = self._resource("background_command_manager")
        return await start_background_command(
            manager,
            terminal_ops=self._resource("terminal_ops"),
            command=command,
            working_dir=working_dir,
            timeout=timeout,
            conversation_id=conversation_id,
            wait_seconds=float(wait_seconds if wait_seconds is not None else 5.0),
            network_permission=network_permission or self.network_permission,
            sandbox_write_access=bool(sandbox_write_access),
        )

    def cancel_background(self, command_id: str) -> Dict[str, Any]:
        """按 command_id 取消后台命令（pid/进程组级）。

        等价于 `backend.background.cancel_command(...)`；保留此便捷入口是为了
        让取消语义在契约层可见，而不是只能通过资源视图到达。
        """
        return self._resource("background_command_manager").cancel_command(command_id)

    # ------------------------------------------------------------------
    # E3/E4 文件写（兼容旧四方法，实际代理文件子协议）
    # ------------------------------------------------------------------

    def write_file(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]:
        """E3：严格经文件子协议写入（不回退 raw file_manager）。"""
        return self.files.write_file(path, content, mode=mode)

    def edit_file(self, path: str, replacements: List[Dict[str, Any]]) -> Dict[str, Any]:
        """E4：严格经文件子协议做精确替换（不回退 raw file_manager）。"""
        return self.files.replace_many_in_file(path, replacements)

    # ------------------------------------------------------------------
    # 编辑前备份（浅版本）：执行端捕获，控制端不读文件
    # ------------------------------------------------------------------

    def track_file_edit(
        self,
        path: str,
        *,
        conversation_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        """在真实写入前把文件当前内容纳入对话级浅版本备份。

        语义：
        1. 经文件子协议 `inspect_path(path, access="read")` 做**执行端**授权检查；
        2. 再做 `inspect_path(path, access="write")` 写权限检查，两项都通过才备份；
        3. 用 `files.read_binary` 取**执行端权威字节**（Docker 下就是容器内内容，
           不是宿主镜像/代理缓存），交给
           `ShallowVersioningManager.track_edit_content`，快照文件写在控制端
           `data_dir`；并把 `files` 注入管理器，使 make_snapshot/rewind 同样从
           执行端读、向执行端写。

        边界：
        - 身份/数据目录/对话/消息任一缺失即 best-effort 跳过（不抛错）；
        - 权限不足、路径越界、文件子协议不可用、读取失败同样跳过；
        - 执行端确认文件不存在（`exists=False`）时记一个 None 版本，
          使 rewind 到本消息能删除该新文件（撤销创建）；
        - **不在控制端读文件**：读一律经 files 子协议，路径解析留在执行端；
        - 失败绝不阻断工具结果（与原 best-effort 行为一致）。
        """
        project_path = getattr(self.owner, "project_path", None)
        data_dir = getattr(self.owner, "data_dir", None)
        conversation = str(conversation_id or "").strip()
        message = str(message_id or "").strip()
        if not path or not project_path or not data_dir or not conversation or not message:
            return
        try:
            fs = self.files  # 严格经文件子协议（无回退）；不可用时下面统一 best-effort 跳过
            inspect = getattr(fs, "inspect_path", None)
            if not callable(inspect):
                return
            read_meta = None
            for access in ("read", "write"):
                meta = inspect(str(path), access)
                if not isinstance(meta, dict) or not meta.get("success"):
                    return
                if access == "read":
                    read_meta = meta
            target = str((read_meta or {}).get("path") or path)
            if (read_meta or {}).get("exists") is False:
                # 执行端确认文件不存在 = 合法新文件：记一个 None 版本，
                # 这样 rewind 到本消息时会删除它（“撤销创建”）。
                data: Optional[bytes] = None
            else:
                payload = fs.read_binary(target)
                if isinstance(payload, dict):
                    if not payload.get("success"):
                        return
                    raw = payload.get("content")
                    if raw is None:
                        data = b""
                    elif isinstance(raw, str):
                        data = raw.encode("utf-8")
                    else:
                        data = bytes(raw)
                elif isinstance(payload, (bytes, bytearray, memoryview)):
                    data = bytes(payload)
                else:
                    return
            from modules.shallow_versioning import ShallowVersioningManager

            manager = ShallowVersioningManager(
                project_path=project_path,
                data_dir=data_dir,
                conversation_id=conversation,
                filesystem=fs,
            )
            manager.track_edit_content(target, message, data)
        except Exception:
            # 浅版本是 best-effort：任何异常都不影响正在执行的写操作
            return

    # ------------------------------------------------------------------
    # 执行环境快照（实现细节在 environment.py，避免本文件膨胀）
    # ------------------------------------------------------------------

    def get_environment(self) -> Dict[str, Any]:
        """返回真实执行环境（JSON 可序列化）。

        诚实边界：
        - `host` 段只描述**本机** OS；docker 场景下容器内 OS 未探测，因此显式标注
          `container.os = "unknown"`，不把宿主 OS 冒充成容器 OS；
        - `capabilities` 检查当前 owner 是否提供资源和可调用实现；不验证具体路径
          可读性或 OS 隔离，不等于平台验收（静态声明见 `declared_capabilities`）；
        - `case_sensitive` 只在 Host 工作区只读探测；容器或无可靠样本时为 null，
          `case_sensitive_source = "unknown"`，不借宿主镜像推断；
        - Docker 环境不报告支持单次宿主完全访问。
        """
        from modules.execution_plane.environment import describe_environment

        return describe_environment(self)

    # ------------------------------------------------------------------
    # 插件（MCP / 自定义工具）执行入口
    # ------------------------------------------------------------------
    # 约定：本类**不**做插件权限判定、禁用规则或别名解析（控制端保留），
    # 也不克隆/缓存 MCP manager：owner 的 manager、cwd 与生命周期是唯一权威。
    # 集成函数在方法内局部导入，保证轻量执行节点不因插件栈连带导入控制端模块。

    async def execute_mcp_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Any:
        """严格转交 `integrations.execute_mcp_tool(owner, tool_name, arguments)`。"""
        from modules.execution_plane.integrations import execute_mcp_tool

        return await execute_mcp_tool(
            self.owner, tool_name, {} if arguments is None else arguments
        )

    async def execute_custom_tool(
        self, tool_name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> Any:
        """严格转交 `integrations.execute_custom_tool(owner, tool_name, arguments)`。"""
        from modules.execution_plane.integrations import execute_custom_tool

        return await execute_custom_tool(
            self.owner, tool_name, {} if arguments is None else arguments
        )

    async def list_mcp_servers(
        self, arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """严格转交 `integrations.list_mcp_servers(owner, arguments)`。"""
        from modules.execution_plane.integrations import list_mcp_servers

        return await list_mcp_servers(self.owner, {} if arguments is None else arguments)


__all__ = ["LocalExecutionBackend"]
