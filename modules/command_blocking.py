"""个人指令拦截配置：与个人配置共享目录同级，不读取部署级旧词表。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from modules.i18n import tr
from utils.atomic_io import atomic_write_json

PathLike = Union[str, Path]
CONFIG_FILENAME = "command_blocking.json"
_RECOMMENDED_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "command_blocking_recommended.json"
)
_CONFIG_LOCK = threading.RLock()
MAX_RULES = 500
MAX_RULE_LENGTH = 1000


def get_command_blocking_path(data_dir: PathLike) -> Path:
    """沿 personalization.json 的共享链接定位当前用户配置，不替换链接。"""
    directory = Path(data_dir).expanduser()
    personalization = directory / "personalization.json"
    if personalization.is_symlink():
        directory = personalization.resolve().parent
    return directory / CONFIG_FILENAME


def _normalize_rules(value: Any) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_RULES:
        raise ValueError(tr("terminal.command_blocking_rules_invalid", limit=MAX_RULES))
    result: List[str] = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or len(item) > MAX_RULE_LENGTH:
            raise ValueError(tr("terminal.command_blocking_rule_invalid", limit=MAX_RULE_LENGTH))
        text = item.strip()
        if "\n" in text or "\r" in text:
            raise ValueError(tr("terminal.command_blocking_rule_single_line"))
        if text and text.lower() not in seen:
            result.append(text)
            seen.add(text.lower())
    return result


def _validate_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"enabled", "rules"}:
        raise ValueError(tr("terminal.command_blocking_payload_invalid"))
    result: Dict[str, Any] = {}
    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise ValueError(tr("terminal.command_blocking_enabled_invalid"))
        result["enabled"] = payload["enabled"]
    if "rules" in payload:
        result["rules"] = _normalize_rules(payload["rules"])
    return result


def load_command_blocking_config(data_dir: PathLike) -> Dict[str, Any]:
    """每次读取个人文件；仅文件不存在时使用默认开启、空规则。"""
    with _CONFIG_LOCK:
        path = get_command_blocking_path(data_dir)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": True, "rules": []}
        config = {"enabled": True, "rules": []}
        config.update(_validate_payload(payload))
        return config


def save_command_blocking_config(
    data_dir: PathLike, payload: Mapping[str, Any]
) -> Dict[str, Any]:
    """部分更新合并后原子保存；开关操作不会覆盖另一个入口保存的规则。"""
    patch = _validate_payload(payload)
    with _CONFIG_LOCK:
        config = load_command_blocking_config(data_dir)
        config.update(patch)
        atomic_write_json(get_command_blocking_path(data_dir), config)
        return config


def get_recommended_rules() -> List[str]:
    """仅供界面主动导入，命令执行校验绝不调用此函数。"""
    payload = json.loads(_RECOMMENDED_PATH.read_text(encoding="utf-8"))
    return _normalize_rules(payload["rules"])


def validate_command(command: str, data_dir: Optional[PathLike]) -> Tuple[bool, str]:
    """沿用不区分大小写的全文子串匹配；不按全局权限档位改变用户选择。"""
    if data_dir is None:
        # 独立执行器未绑定用户时没有个人规则，不得回退到全局用户目录。
        return True, ""
    try:
        config = load_command_blocking_config(data_dir)
    except (OSError, ValueError) as exc:
        return False, tr("terminal.command_blocking_unavailable", error=str(exc))
    if config["enabled"]:
        lowered = command.lower()
        for rule in config["rules"]:
            if rule.lower() in lowered:
                return False, tr("terminal.forbidden_command", pattern=rule)
    return True, ""
