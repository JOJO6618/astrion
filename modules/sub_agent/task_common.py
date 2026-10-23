from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

_QUESTION_ID_RE = re.compile(r"^id:\s*(\S+)", re.MULTILINE)

from modules.sub_agent.toolkit import (
    SUB_AGENT_TOOLS,
    _format_tool_result,
)
from modules.aux_model_resolver import resolve_locked_model_profile
from utils.api_client import APIClient
from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.sub_agent.manager import SubAgentManager
    from modules.multi_agent.state import MultiAgentState

from modules.multi_agent.debug_logger import ma_debug
from modules.i18n import tr

logger = setup_logger(__name__)


class SubAgentModelCallError(RuntimeError):
    """子智能体模型请求失败。

    received_any=False：请求阶段失败（未收到任何文本/思考/工具调用），可重试；
    received_any=True：已开始收到流内容后断开（输出期间断开）——
    网络类断流（is_network_error=True）同样可重试（清除重来，与主智能体
    run_streaming_attempts 的放宽判定一致）；非网络错误（4xx 等业务错误）
    有接收时不重试。
    calling_tools：断流时流中已推送「正在调用」进度事件的工具列表，
    重试前由 _run_loop 标记取消，避免前端进度弹窗永久卡在「调用中」。
    """

    def __init__(self, message: str, *, received_any: bool = False, is_network_error: bool = True, calling_tools: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.received_any = received_any
        self.is_network_error = is_network_error
        self.calling_tools = calling_tools or []


# 模型请求失败重试策略（与主智能体 max_api_retries=4 / retry_delay_seconds=10 对齐：
# 最多重试 4 次，即共 5 次尝试，重试间隔 10 秒）
_SUB_AGENT_MAX_API_RETRIES = 4
_SUB_AGENT_RETRY_DELAY_SECONDS = 10
