# ========== api_client.py ==========
# utils/api_client.py - OpenAI-compatible API 客户端（支持Web模式）

import httpx
import json
import asyncio
import base64
import mimetypes
import os
from typing import List, Dict, Optional, AsyncGenerator, Any
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Tuple
try:
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )
except ImportError:
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import (
        OUTPUT_FORMATS,
        DEFAULT_RESPONSE_MAX_TOKENS,
        LOGS_DIR,
    )

from utils.log_rotation import append_line, prune_dir



from utils.api_client.utils import _api_dump_enabled

class APIClientBaseMixin:
    def __init__(self, thinking_mode: bool = True, web_mode: bool = False):
        # 模型 API 配置（端点 / 密钥 / 模型 ID）由 apply_profile 从 custom_models 注入；
        # 这里仅初始化为空，未应用任何模型档案前不可直接发请求。
        self.fast_api_config = {
            "base_url": "",
            "api_key": "",
            "model_id": ""
        }
        self.thinking_api_config = {
            "base_url": "",
            "api_key": "",
            "model_id": ""
        }
        self.fast_max_tokens = None
        self.thinking_max_tokens = None
        self.fast_extra_params: Dict = {}
        self.thinking_extra_params: Dict = {}
        # 上下文预算（由上层在每次请求前设置）
        self.current_context_tokens: int = 0
        self.max_context_tokens: Optional[int] = None
        self.default_context_window: Optional[int] = None
        self.thinking_mode = thinking_mode  # True=思考模式（每次请求都用思考配置）, False=快速模式
        self.web_mode = web_mode  # Web模式标志，用于禁用print输出
        # 兼容旧代码路径
        self.api_base_url = self.fast_api_config["base_url"]
        self.api_key = self.fast_api_config["api_key"]
        self.model_id = self.fast_api_config["model_id"]
        self.model_key = None  # 由宿主终端注入，便于做模型兼容处理
        self.model_multimodal = "none"  # 由模型 profile 注入，model_key 不可用时用于能力兜底
        self.project_path: Optional[str] = None
        # 推理强度（reasoning effort）：None=默认（不传参）；由会话级设置注入
        self.reasoning_effort: Optional[str] = None
        self.supports_reasoning_effort = False  # 当前模型是否支持推理强度，由 apply_profile 注入
        # 最近一次API错误详情
        self.last_error_info: Optional[Dict[str, Any]] = None
        # 请求体落盘目录（跟随 LOGS_DIR，默认 ~/.astrion/<mode>/logs/api_requests）
        self.request_dump_dir = Path(LOGS_DIR) / "api_requests"
        self.debug_log_path = Path(LOGS_DIR) / "api_debug.log"

    def _resolve_env_value(self, name: str) -> Optional[str]:
        """从环境变量获取配置（已由 config/__init__.py 统一注入）。"""
        value = os.environ.get(name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def _print(self, message: str, end: str = "\n", flush: bool = False):
        """安全的打印函数，在Web模式下不输出"""
        if not self.web_mode:
            print(message, end=end, flush=flush)
