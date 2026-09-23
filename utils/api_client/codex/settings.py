"""Codex 订阅接入：共享小配置（路径 / 代理 / 常量）。

设计文档：docs/codex_integration_plan.md
- token 唯一事实源是 astrion 自己的 ``codex_auth.json``（opencode 模式：
  独立 OAuth 会话、独立存储，与 Codex CLI 的 ``~/.codex/auth.json`` 完全解耦，
  登出 = 删除自己的凭证文件，CLI 登录态不受影响）；
- 模型缓存与 proxy 配置放 astrion 运行态数据目录的 ``codex_models.json``（0600）；
- 代理解析优先级：环境变量 ``HTTPS_PROXY``/``https_proxy`` > 配置文件 ``proxy`` 键。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from config import DATA_DIR
except ImportError:  # pragma: no cover - 允许脱离项目根直接导入
    import sys

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from config import DATA_DIR

# ---------------------------------------------------------------------------
# 常量（实测值，见设计文档 §1）
# ---------------------------------------------------------------------------

# astrion 独立凭证文件路径在 DATA_DIR 引入后统一定义（见文件底部）
CODEX_CLI_MODELS_CACHE = str(Path.home() / ".codex" / "models_cache.json")

OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_LISTEN_PORT = 1455
OAUTH_SCOPE = "openid profile email offline_access"

# Device Code 授权流（OpenAI 官方无头登录，与 Codex CLI / opencode headless
# 同一套端点与 client_id；字段以 codex-rs login/src/device_code_auth.rs 官方
# 实现 + 2026-09-24 实测为准）：
# 1) POST deviceauth/usercode，JSON 仅 {client_id} →
#    {device_auth_id, user_code, interval(字符串秒), expires_at}
# 2) 用户在任意浏览器开固定验证页 OAUTH_DEVICE_VERIFY_URL 输 user_code 授权
# 3) 轮询 deviceauth/token，JSON {device_auth_id, user_code} → pending 为
#    403/404（官方）或 400+error.code=deviceauth_authorization_pending（实测），
#    成功 200 + {authorization_code, code_challenge, code_verifier}
#    ——PKCE 对由服务端随授权码下发，客户端不事先生成
# 4) 同一 token 端点交换（redirect_uri 换设备流专用 + 服务端下发的 verifier）
# 前提：ChatGPT 账号设置 → 安全里开启「Enable device code authentication for Codex」。
OAUTH_DEVICE_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
OAUTH_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
OAUTH_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
OAUTH_DEVICE_VERIFY_URL = "https://auth.openai.com/codex/device"

API_BASE = "https://chatgpt.com/backend-api/codex"
RESPONSES_URL = f"{API_BASE}/responses"
MODELS_URL = f"{API_BASE}/models"
# 订阅用量查询（CLI /status 的数据源）
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# 可储存限额重置（banked resets）：列表 / 兑换（非官方文档端点，来自 VS Code 扩展）
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"
RESET_CREDITS_CONSUME_URL = f"{RESET_CREDITS_URL}/consume"

# 拉取模型列表时自报的客户端版本（/models 强制要求，缺了 400）；
# 初始值跟随本机 Codex CLI 缓存的 0.153.4，必要时可在 codex_models.json
# 用 "client_version" 键覆盖。
DEFAULT_CLIENT_VERSION = "0.153.4"

# access_token 过期前多久触发刷新（秒）
REFRESH_SKEW_SECONDS = 300

# 模型缓存多久视为陈旧（秒），超过则下次访问时后台刷新
MODELS_TTL_SECONDS = 24 * 3600

# instructions 本地覆盖文件（存在即最高优先级）
INSTRUCTIONS_OVERRIDE_PATH = str(
    Path(DATA_DIR).parent / "config" / "codex_instructions.txt"
)

# astrion 侧 codex 数据文件（模型缓存 + proxy/client_version 配置）
CODEX_DATA_PATH = str(Path(DATA_DIR) / "codex_models.json")

# astrion 独立 OAuth 凭证文件（0600；结构与 CLI auth.json 对齐：
# auth_mode / OPENAI_API_KEY / tokens / last_refresh）
CODEX_AUTH_PATH = str(Path(DATA_DIR) / "codex_auth.json")


def load_codex_data() -> Dict[str, Any]:
    """读取 astrion 侧 codex 数据文件；不存在/损坏返回空 dict。"""
    try:
        with open(CODEX_DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_codex_data(data: Dict[str, Any]) -> None:
    """原子写入 codex 数据文件（0600）。失败抛异常由调用方决定取舍。"""
    path = Path(CODEX_DATA_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def get_config_value(key: str) -> Optional[str]:
    value = load_codex_data().get(key)
    return str(value).strip() if value else None


def set_config_value(key: str, value: Optional[str]) -> None:
    data = load_codex_data()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    save_codex_data(data)


def resolve_proxy() -> Optional[str]:
    """解析 codex 请求使用的代理 URL。

    优先级：环境变量 HTTPS_PROXY/https_proxy > codex_models.json 的 "proxy" 键。
    用户网络访问 chatgpt.com 必须走代理（实测），此返回值直接传给 httpx。
    """
    for env_name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()
    return get_config_value("proxy")


def get_client_version() -> str:
    return get_config_value("client_version") or DEFAULT_CLIENT_VERSION
