"""审核智能体统一配置解析。

三个审核智能体（自动审批 auto_approval / 目标审核 goal_review / 工作流审核 workflow_review）
的模型与运行参数统一来自个人空间设置（personalization.json 的 review_agents 键）：
- model / thinking：注册表模型 key 与思考模式；模型来源唯一 = 主注册表
  （custom_models + codex 动态发现，见 modules/aux_model_resolver.py），留空走自动规则；
- timeout_seconds / max_rounds / max_command_timeout：审核请求超时、最大轮次、只读命令超时。

历史上三个智能体各自读取独立的部署级 json 配置（auto_approval.json / goal_review.json /
workflow_review.json），该方式已彻底废弃；此前的「子智能体独立模型库
sub_agent_models.json（含库级 default_model）」同样已废弃，不做任何向后兼容。
"""

from typing import Any, Dict

from config import DATA_DIR
from modules.aux_model_resolver import resolve_aux_model_profile
from modules.personalization_manager import REVIEW_AGENT_KEYS

__all__ = ["resolve_review_agent_config", "REVIEW_AGENT_KEYS"]


def resolve_review_agent_config(agent_key: str) -> Dict[str, Any]:
    """解析指定审核智能体的完整运行配置。

    返回字段：name / profile / thinking / extra_params / timeout_seconds /
    max_rounds / max_command_timeout。
    - profile：APIClient.apply_profile 同形的模型配置（含 provider_type），
      模型未配置或注册表不可用时为 None，由各审核智能体走既有的
      「配置缺失」兜底行为；
    - extra_params：仅常规模型的裸 HTTP 路径使用（max_tokens 注入等），
      codex 路径忽略。
    """
    base: Dict[str, Any] = {
        "name": f"{agent_key}-agent",
        "profile": None,
        "thinking": False,
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
    base["thinking"] = bool(settings.get("thinking"))

    model_key = str(settings.get("model") or "").strip()
    profile = resolve_aux_model_profile(model_key)
    if not profile:
        return base
    base["profile"] = profile

    # 仅 chat 裸 HTTP 路径需要 max_tokens 注入；responses 路径由 profile 自带参数
    if str(profile.get("api_protocol") or "") != "responses":
        thinking = base["thinking"]
        segment = profile.get("thinking") if thinking else None
        if not segment:
            segment = profile.get("fast") or {}
        max_tokens = segment.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens > 0:
            base["extra_params"]["max_tokens"] = max_tokens
    return base
