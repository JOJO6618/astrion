"""外部会话标识（x-opencode-session）管理。

OpenCode Go/Zen 自 2026-09-05 起要求发往其端点的请求携带
``x-opencode-session`` 请求头：同一对话使用稳定 ID，供其做会话亲和
路由与 prompt 缓存优化（https://opencode.ai/docs/go/）。

设计约定：
- 开关：个人空间「模型与思考」-> ``external_session_header``，默认开启（opt-out）。
- ID 取值：随机 uuid4 hex，不复用 conversation_id（避免向 vendor 暴露本地标识）。
- 生命周期：对话首次请求时惰性生成并存入对话 metadata；深压缩完成后重置
  （压缩重写上下文后，旧 session 的缓存亲和已失去意义，语义上等同新 session）。
- 下发范围：仅对 opencode.ai 域名下发，不向其他 provider 泄露对话标识。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from config.version import APP_VERSION

OPENCODE_HOST_MARKER = "opencode.ai"
SESSION_METADATA_KEY = "external_session_id"


def build_user_agent() -> str:
    """对外请求的 User-Agent（OpenCode 要求工具自标识，禁止宽泛 UA）。"""
    return f"Astrion/{APP_VERSION}"


def new_external_session_id() -> str:
    """生成新的随机外部会话 ID。"""
    return uuid.uuid4().hex


def is_opencode_endpoint(base_url: Optional[str]) -> bool:
    """判定 base_url 是否指向 opencode.ai（Go 与 Zen 同域名）。"""
    return bool(base_url) and OPENCODE_HOST_MARKER in str(base_url).lower()


def external_session_header_enabled(base_dir=None) -> bool:
    """读取个人空间开关 external_session_header（默认开启，opt-out）。

    无工作区上下文的调用方（如审核智能体）使用全局 DATA_DIR；
    有工作区上下文的调用方可传入 workspace.data_dir。
    任何异常均按关闭处理，不影响请求主链路。
    """
    try:
        from modules.personalization_manager import load_personalization_config

        if base_dir is None:
            from config import DATA_DIR

            base_dir = DATA_DIR
        config = load_personalization_config(base_dir)
        return bool(config.get("external_session_header"))
    except Exception:
        return False


def get_or_create_conversation_session_id(conversation_id: str, manager) -> Optional[str]:
    """读取对话 metadata 中的 external_session_id；缺失则生成并写回（惰性创建）。

    Args:
        conversation_id: 对话 ID
        manager: ConversationManager 实例（需具备 load_conversation /
            update_conversation_metadata 能力）

    Returns:
        session ID 字符串；读取或写入失败时返回 None（不影响请求主链路）。
    """
    if not conversation_id or manager is None:
        return None
    try:
        data = manager.load_conversation(conversation_id) or {}
        metadata = data.get("metadata") or {}
    except Exception:
        return None
    existing = metadata.get(SESSION_METADATA_KEY)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    new_id = new_external_session_id()
    try:
        manager.update_conversation_metadata(conversation_id, {SESSION_METADATA_KEY: new_id})
    except Exception:
        return None
    return new_id


def resolve_conversation_headers(
    base_url: Optional[str],
    conversation_id: Optional[str],
    manager=None,
    base_dir=None,
) -> Dict[str, str]:
    """主对话/标题生成用：开关 + 域名判定后返回该对话的稳定 session 头。

    不满足条件时返回空 dict。
    """
    if not conversation_id:
        return {}
    if not external_session_header_enabled(base_dir):
        return {}
    if not is_opencode_endpoint(base_url):
        return {}
    sid = get_or_create_conversation_session_id(conversation_id, manager)
    return {"x-opencode-session": sid} if sid else {}


def resolve_ephemeral_headers(base_url: Optional[str], base_dir=None) -> Dict[str, str]:
    """无对话连续性的调用（审核智能体等）用：每次生成一次性 session 头。

    不满足条件时返回空 dict。
    """
    if not external_session_header_enabled(base_dir):
        return {}
    if not is_opencode_endpoint(base_url):
        return {}
    return {"x-opencode-session": new_external_session_id()}
