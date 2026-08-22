"""工具类别配置。

提供前端和终端公用的工具分组定义，方便按类别控制启用状态。
"""

from typing import Dict, List


class ToolCategory:
    """工具类别的结构化定义。"""

    def __init__(
        self,
        label: str,
        tools: List[str],
        default_enabled: bool = True,
        silent_when_disabled: bool = False,
    ):
        self.label = label
        self.tools = tools
        self.default_enabled = default_enabled
        self.silent_when_disabled = silent_when_disabled


TOOL_CATEGORIES: Dict[str, ToolCategory] = {
    "network": ToolCategory(
        label="网络检索",
        tools=["web_search", "extract_webpage", "save_webpage"],
    ),
    "mcp": ToolCategory(
        label="MCP 工具",
        tools=["list_mcp_servers"],
    ),
    "file_edit": ToolCategory(
        label="文件编辑",
        tools=[
            "create_file",
            "write_file",
            "edit_file",
            "delete_file",
            "rename_file",
            "create_folder",
        ],
    ),
    "read_focus": ToolCategory(
        label="阅读聚焦",
        tools=[
            "read_file",
            "vlm_analyze",
            "ocr_image",
            "view_image",
        ],
    ),
    "skills": ToolCategory(
        label="Skills",
        tools=["read_skill", "create_skill"],
    ),
    "terminal_realtime": ToolCategory(
        label="实时终端",
        tools=[
            "terminal_session",
            "terminal_input",
            "terminal_snapshot",
            "sleep",
        ],
    ),
    "terminal_command": ToolCategory(
        label="终端指令",
        tools=["run_command"],
    ),
    "memory": ToolCategory(
        label="记忆",
        tools=["update_memory", "conversation_search", "conversation_review"],
    ),
    "todo": ToolCategory(
        label="待办事项",
        tools=["todo_create", "todo_update_task"],
    ),
    "sub_agent": ToolCategory(
        label="子智能体",
        tools=["create_sub_agent", "close_sub_agent"],
    ),
    "easter_egg": ToolCategory(
        label="彩蛋实验",
        tools=["trigger_easter_egg"],
        default_enabled=False,
        silent_when_disabled=True,
    ),
    "personalization": ToolCategory(
        label="个性化设置",
        tools=["manage_personalization"],
        default_enabled=True,
    ),
    "communication": ToolCategory(
        label="用户沟通",
        tools=["ask_user"],
        default_enabled=True,
    ),
}
