"""Config package initializer，保持对旧 `from config import ...` 的兼容。"""

import json
import os
from pathlib import Path


def _load_dotenv():
    """加载配置：.env（仓库根）+ settings.json（数据根）。

    1. 先读仓库根目录 .env（若存在），不覆盖已有的环境变量；
    2. 再读 {data_root}/settings.json，将其中的 env_vars 注入 os.environ，
       并将非 env_vars 的顶级字段也注入为 AGENT_CFG_<KEY> 环境变量（供 config/auth.py 等使用）；
    3. settings.json 的加载依赖 data_root 路径，若 settings.json 不存在则静默跳过。
    """
    import sys
    pre_existing_keys = set(os.environ.keys())

    # 1) 仓库根 .env（开发便利，不覆盖已有的环境变量，但 ASTRION_DATA_ROOT
    #    作为项目数据根目录必须优先以 .env 为准，避免外部 shell 误指到 clone）
    if getattr(sys, 'frozen', False):
        env_path = Path.home() / '.astrion' / 'astrion' / '.env'
    else:
        env_path = Path(__file__).resolve().parents[1] / '.env'
    env_from_file: dict = {}
    if env_path.exists():
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not key:
                    continue
                env_from_file[key] = value
                if key in pre_existing_keys:
                    continue
                os.environ[key] = value
        except Exception:
            pass
    if "ASTRION_DATA_ROOT" in env_from_file:
        os.environ["ASTRION_DATA_ROOT"] = str(Path(env_from_file["ASTRION_DATA_ROOT"]).expanduser())

    # 2) settings.json（数据根下的统一配置）
    data_root = os.environ.get("ASTRION_DATA_ROOT", str(Path.home() / ".astrion" / "astrion"))
    settings_path = Path(data_root).expanduser() / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            # 注入 env_vars（API key 等）
            env_vars = settings.get("env_vars", {})
            if isinstance(env_vars, dict):
                for k, v in env_vars.items():
                    if k not in pre_existing_keys and v:
                        os.environ[k] = str(v)

            # 映射 settings.json 字段 → 旧版环境变量名（兼容现有 config/*.py 模块）
            # 注意：terminal.macos_writable_paths 已于 2026-08-30 移除——
            # 沙箱路径授权只读两个来源：config/host_sandbox_policy.json（前端
            # 「路径授权」UI）与真·环境变量；settings.json 中的历史值一次性失效。
            _LEGACY_MAP = {
                "terminal.sandbox_mode":             "TERMINAL_SANDBOX_MODE",
                "terminal.execution_mode_default":   "HOST_EXECUTION_MODE_DEFAULT",
                "terminal.max_active_containers":     "MAX_ACTIVE_USER_CONTAINERS",
                "terminal.project_max_storage_mb":    "PROJECT_MAX_STORAGE_MB",
                "server.port":                        "WEB_SERVER_PORT",
                "server.host":                        "WEB_SERVER_HOST",
                "admin.username":                     "AGENT_ADMIN_USERNAME",
                "admin.password_hash":                "AGENT_ADMIN_PASSWORD_HASH",
                "admin.secondary_password_hash":      "ADMIN_SECONDARY_PASSWORD_HASH",
                "secrets.web_secret_key":             "WEB_SECRET_KEY",
                "secrets.api_token_secret":           "API_TOKEN_SECRET",
                "search.tavily_api_key":              "AGENT_TAVILY_API_KEY",
                "search.tavily_api_key_2":            "AGENT_TAVILY_API_KEY_2",
                "models.default_response_max_tokens": "AGENT_DEFAULT_RESPONSE_MAX_TOKENS",
            }
            for dotted_key, env_name in _LEGACY_MAP.items():
                if env_name in pre_existing_keys:
                    continue
                parts = dotted_key.split(".")
                val = settings
                for p in parts:
                    val = val.get(p) if isinstance(val, dict) else None
                    if val is None:
                        break
                if val is not None:
                    os.environ[env_name] = str(val)

            # 注入非 env_vars 的顶级配置为 AGENT_CFG_* 环境变量（供 config/auth.py 等使用）
            for k, v in settings.items():
                if k == "env_vars":
                    continue
                env_key = f"AGENT_CFG_{k.upper()}"
                if isinstance(v, dict):
                    for sub_k, sub_v in v.items():
                        flat_key = f"AGENT_CFG_{k.upper()}_{sub_k.upper()}"
                        if flat_key not in pre_existing_keys:
                            os.environ[flat_key] = str(sub_v)
                elif env_key not in pre_existing_keys:
                    os.environ[env_key] = str(v)
        except Exception:
            pass


_load_dotenv()

from . import api as _api
from . import search as _search
from . import paths as _paths
from . import limits as _limits
from . import terminal as _terminal
from . import conversation as _conversation
from . import security as _security
from . import ui as _ui
from . import memory as _memory
from . import ocr as _ocr
from . import todo as _todo
from . import auth as _auth
from . import uploads as _uploads
from . import sub_agent as _sub_agent
from . import custom_tools as _custom_tools
from . import mcp as _mcp
from . import server as _server

from .api import *
from .search import *
from .paths import *
from .limits import *
from .terminal import *
from .conversation import *
from .security import *
from .ui import *
from .memory import *
from .ocr import *
from .todo import *
from .auth import *
from .uploads import *
from .sub_agent import *
from .custom_tools import *
from .mcp import *
from .server import *

__all__ = []
for module in (_api, _search, _paths, _limits, _terminal, _conversation, _security, _ui, _memory, _ocr, _todo, _auth, _uploads, _sub_agent, _custom_tools, _mcp, _server):
    __all__ += getattr(module, "__all__", [])

del _api, _search, _paths, _limits, _terminal, _conversation, _security, _ui, _memory, _ocr, _todo, _auth, _uploads, _sub_agent, _custom_tools, _mcp, _server
