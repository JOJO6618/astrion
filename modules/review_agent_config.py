"""审核智能体统一配置解析。

三个审核智能体（自动审批 auto_approval / 目标审核 goal_review / 工作流审核 workflow_review）
的模型与运行参数统一来自个人空间设置（personalization.json 的 review_agents 键）：
- model / thinking：模型名与思考模式；模型条目复用子智能体模型库 sub_agent_models.json，
  模型名留空时使用模型库的 default_model；
- timeout_seconds / max_rounds / max_command_timeout：审核请求超时、最大轮次、只读命令超时。

历史上三个智能体各自读取独立的部署级 json 配置（auto_approval.json / goal_review.json /
workflow_review.json），该方式已彻底废弃，不再做任何向后兼容。
"""

import json
from pathlib import Path
from typing import Any, Dict

from config import DATA_DIR
from config.sub_agent import SUB_AGENT_MODELS_CONFIG_FILE
from modules.personalization_manager import REVIEW_AGENT_KEYS

__all__ = ["resolve_review_agent_config", "REVIEW_AGENT_KEYS"]


def _load_model_entry(model_name: str) -> Dict[str, Any]:
    """从子智能体模型库解析出指定模型的 profile；名称留空则用 default_model。

    返回 APIClient.apply_profile 格式的 profile（含 fast/thinking 两段），失败返回 None。
    """
    config_path = Path(SUB_AGENT_MODELS_CONFIG_FILE)
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    models = raw.get("models", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    default_key = str(raw.get("default_model", "")) if isinstance(raw, dict) else ""

    from modules.sub_agent.toolkit import _build_sub_agent_profile

    model_map: Dict[str, Dict[str, Any]] = {}
    for item in models:
        if not isinstance(item, dict):
            continue
        profile = _build_sub_agent_profile(item)
        if profile:
            model_map[profile["name"]] = profile

    chosen = model_name or default_key
    if chosen not in model_map and model_map:
        chosen = next(iter(model_map))
    return model_map.get(chosen)


def resolve_review_agent_config(agent_key: str) -> Dict[str, Any]:
    """解析指定审核智能体的完整运行配置。

    返回字段：name / url / key / model / extra_params / timeout_seconds / max_rounds /
    max_command_timeout。模型未配置或模型库不可用时 url/key/model 为空字符串，
    由各审核智能体走既有的「配置缺失」兜底行为。
    """
    base: Dict[str, Any] = {
        "name": f"{agent_key}-agent",
        "url": "",
        "key": "",
        "model": "",
        "extra_params": {},
        "timeout_seconds": 60,
        "max_rounds": 3,
        "max_command_timeout": 60,
    }
    if agent_key not in REVIEW_AGENT_KEYS:
        return base

    try:
        from modules.personalization_manager import load_personalization_config

        personal = load_personalization_config(DATA_DIR)
    except Exception:
        personal = {}
    settings = (personal.get("review_agents") or {}).get(agent_key)
    if not isinstance(settings, dict):
        return base

    base["timeout_seconds"] = int(settings.get("timeout_seconds") or base["timeout_seconds"])
    base["max_rounds"] = max(1, int(settings.get("max_rounds") or base["max_rounds"]))
    base["max_command_timeout"] = max(1, int(settings.get("max_command_timeout") or base["max_command_timeout"]))

    model_name = str(settings.get("model") or "").strip()
    profile = _load_model_entry(model_name)
    if not profile:
        return base

    # 按思考模式选段；模型不支持 thinking 时回落 fast 段
    thinking = bool(settings.get("thinking"))
    segment = profile.get("thinking") if thinking else None
    if not segment:
        segment = profile.get("fast") or {}
    base["url"] = str(segment.get("base_url") or "").strip()
    base["key"] = str(segment.get("api_key") or "").strip()
    base["model"] = str(segment.get("model_id") or "").strip()
    extra = segment.get("extra_params")
    base["extra_params"] = dict(extra) if isinstance(extra, dict) else {}
    max_tokens = segment.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0 and "max_tokens" not in base["extra_params"]:
        base["extra_params"]["max_tokens"] = max_tokens
    return base
