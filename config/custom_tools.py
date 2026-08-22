"""自定义工具相关的全局配置。"""

import os
from .paths import DATA_DIR

# 是否启用自定义工具功能（仅管理员可见）。
CUSTOM_TOOLS_ENABLED = os.environ.get("CUSTOM_TOOLS_ENABLED", "1") not in {"0", "false", "False"}

# 自定义工具根目录，每个工具一个子文件夹。
CUSTOM_TOOL_DIR = os.environ.get("CUSTOM_TOOL_DIR", f"{DATA_DIR}/custom_tools")

# 每个工具文件夹内的标准文件名
CUSTOM_TOOL_DEFINITION_FILE = "definition.json"  # 定义层
CUSTOM_TOOL_EXECUTION_FILE = "execution.py"      # 执行层（Python 模板/脚本）
CUSTOM_TOOL_RETURN_FILE = "return.json"          # 返回层配置
CUSTOM_TOOL_META_FILE = "meta.json"              # 备注/图标等额外信息

__all__ = [
    "CUSTOM_TOOLS_ENABLED",
    "CUSTOM_TOOL_DIR",
    "CUSTOM_TOOL_DEFINITION_FILE",
    "CUSTOM_TOOL_EXECUTION_FILE",
    "CUSTOM_TOOL_RETURN_FILE",
    "CUSTOM_TOOL_META_FILE",
]
