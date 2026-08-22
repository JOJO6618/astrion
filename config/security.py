"""安全与确认策略配置。"""

from pathlib import Path
import json

from .paths import resolve_deploy_config


def _load_forbidden_commands() -> list[str]:
    """从独立 JSON 文件加载命令拦截关键词。

    属于部署级配置（部署者可调宽松/严苛），优先读 ~/.astrion/<mode>/config，
    回退源码树种子。
    """
    fallback = [
        "rm -rf /",
        "rm -rf ~",
        "format",
        "shutdown",
        "reboot",
        "kill -9",
        "dd if=",
    ]
    cfg_path = Path(resolve_deploy_config("forbidden_commands.json"))
    if not cfg_path.exists():
        return fallback
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    if isinstance(payload, dict):
        raw_items = payload.get("forbidden_commands")
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = None
    if not isinstance(raw_items, list):
        return fallback
    items: list[str] = []
    for item in raw_items:
        text = str(item or "").strip().lower()
        if text:
            items.append(text)
    return items or fallback


FORBIDDEN_COMMANDS = _load_forbidden_commands()

FORBIDDEN_PATHS = [
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/tmp",
    "/Applications",
    "/Library",
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
]

FORBIDDEN_ROOT_PATHS = [
    "/",
    "C:\\",
    "~",
]

NEED_CONFIRMATION = [
    "delete_file",
    "delete_folder",
    "clear_file",
    "execute_terminal",
    "batch_delete",
]

__all__ = [
    "FORBIDDEN_COMMANDS",
    "FORBIDDEN_PATHS",
    "FORBIDDEN_ROOT_PATHS",
    "NEED_CONFIRMATION",
]
