"""子智能体相关配置。"""

import os

from .paths import (
    _resolve_repo_path,
    DATA_DIR,
    DEFAULT_PROJECT_PATH,
)

# 子智能体服务
SUB_AGENT_SERVICE_BASE_URL = os.environ.get("SUB_AGENT_SERVICE_URL", "http://127.0.0.1:8092")
SUB_AGENT_DEFAULT_TIMEOUT = int(os.environ.get("SUB_AGENT_DEFAULT_TIMEOUT", "180"))  # 秒
SUB_AGENT_STATUS_POLL_INTERVAL = float(os.environ.get("SUB_AGENT_STATUS_POLL_INTERVAL", "2.0"))

# 存储与并发限制
# 子智能体任务目录与状态文件属于运行态数据，跟随 DATA_DIR（默认 ~/.astrion/<mode>/data）。
SUB_AGENT_TASKS_BASE_DIR = _resolve_repo_path(os.environ.get("SUB_AGENT_TASKS_BASE_DIR", ""), f"{DATA_DIR}/sub_agent_tasks")
SUB_AGENT_STATE_FILE = _resolve_repo_path(os.environ.get("SUB_AGENT_STATE_FILE", ""), f"{DATA_DIR}/sub_agents.json")
# 子智能体产出结果有意落在工作区内，跟随 DEFAULT_PROJECT_PATH，不迁入运行态根目录。
SUB_AGENT_PROJECT_RESULTS_DIR = _resolve_repo_path(os.environ.get("SUB_AGENT_PROJECT_RESULTS_DIR", ""), f"{DEFAULT_PROJECT_PATH}/sub_agent_results")
SUB_AGENT_MAX_ACTIVE = int(os.environ.get("SUB_AGENT_MAX_ACTIVE", "5"))

# 注：子智能体独立模型库（sub_agent_models.json）已于 2026-09 废弃，
# 模型来源唯一 = 主注册表（见 modules/aux_model_resolver.py）。

__all__ = [
    "SUB_AGENT_SERVICE_BASE_URL",
    "SUB_AGENT_DEFAULT_TIMEOUT",
    "SUB_AGENT_STATUS_POLL_INTERVAL",
    "SUB_AGENT_TASKS_BASE_DIR",
    "SUB_AGENT_PROJECT_RESULTS_DIR",
    "SUB_AGENT_STATE_FILE",
    "SUB_AGENT_MAX_ACTIVE",
]
