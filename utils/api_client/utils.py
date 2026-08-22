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


def _api_dump_enabled() -> bool:
    """API 请求体落盘开关，默认关闭。"""
    return str(os.environ.get("AGENT_API_DUMP_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}

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

def _api_dump_enabled() -> bool:
    """API 请求体落盘开关，默认关闭。"""
    return str(os.environ.get("AGENT_API_DUMP_ENABLED", "")).strip().lower() in {"1", "true", "yes", "on"}
