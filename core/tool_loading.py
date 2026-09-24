"""工具动态加载（Deferred Tool Loading）注册表与状态辅助。

设计文档：docs/dynamic_tool_loading_plan.md

核心语义（与 runtime_contract 默认值解析优先级对齐：对话元数据绑定 > 用户偏好快照）：
- 对话创建时把五元组快照写入 conversation metadata["tool_loading"]：
  enabled / deferred_set / initial_exposed / loaded / pending
- 快照创建即钉死，个人空间设置之后只影响新建对话；
- load_tools 成功后 loaded/pending 更新（同一写入点）；
- 对话压缩（深度+浅度）后 reset_state_after_compression 重置 loaded/pending；
- 读侧全部防御性解析：字段缺失/损坏一律视为未启用（回退全量工具，安全方向）。

注意：本模块不读 personalization、不读 Flask session。快照计算所需的用户配置
由调用方（适配层）读好后传入。
"""

from typing import Any, Dict, Iterable, List, Optional, Set

METADATA_KEY = "tool_loading"
FROZEN_PROMPT_KEY = "frozen_tool_loading_prompt"
LOAD_TOOLS_NAME = "load_tools"

# ---------------------------------------------------------------------------
# 可延迟工具注册表
# ---------------------------------------------------------------------------
# 类目 key -> {"label": 展示名, "when_to_use": prompt 目录里的「什么时候用」文案,
#              "tools": [工具名...]}
# 新增可延迟工具 checklist（详见 AGENTS.md 工具动态加载一节）：
#   1. 在此注册类目/工具；2. 评估是否进默认延迟集（DEFAULT_DEFERRED 由注册表全集
#      构成，默认即全延迟，无需另配）；3. 确认 formatter / 前端 renderer 已覆盖。
DEFERRABLE_REGISTRY: Dict[str, Dict[str, Any]] = {
    "workflow": {
        "label": "工作流",
        "when_to_use": "按既定流程执行任务、创建/调整工作流、推进或查询已激活的工作流时",
        "tools": [
            "activate_workflow",
            "report_workflow_stage",
            "choose_workflow_branch",
            "get_workflow_status",
            "deactivate_workflow",
            "list_workflows",
            "save_workflow",
        ],
    },
    "sub_agent": {
        "label": "子智能体",
        "when_to_use": "并行处理独立任务、批量或后台执行、查询/终止子智能体时",
        "tools": [
            "create_sub_agent",
            "get_sub_agent_status",
            "terminate_sub_agent",
        ],
    },
    "conversation": {
        "label": "对话回顾",
        "when_to_use": "查找或回顾本工作区的历史对话时",
        "tools": [
            "conversation_search",
            "conversation_review",
        ],
    },
    "memory_write": {
        "label": "记忆写入",
        "when_to_use": "记录用户偏好、项目约定或重要决策时",
        "tools": [
            "update_memory",
            "update_project_memory",
        ],
    },
    "personalization": {
        "label": "个性化",
        "when_to_use": "修改称呼、语气、主题等个性化配置时",
        "tools": [
            "manage_personalization",
        ],
    },
    "skill_create": {
        "label": "技能",
        "when_to_use": "把经验沉淀为可复用 skill 时",
        "tools": [
            "create_skill",
        ],
    },
    "mcp": {
        "label": "MCP",
        "when_to_use": "查看或刷新 MCP 服务与工具映射时",
        "tools": [
            "list_mcp_servers",
        ],
    },
    "misc": {
        "label": "彩蛋",
        "when_to_use": "触发隐藏彩蛋时",
        "tools": [
            "trigger_easter_egg",
        ],
    },
}


def deferrable_tool_names() -> List[str]:
    """注册表内全部可延迟工具名（保序）。"""
    names: List[str] = []
    for cat in DEFERRABLE_REGISTRY.values():
        names.extend(cat["tools"])
    return names


def category_of_tool(tool_name: str) -> Optional[str]:
    for cat_key, cat in DEFERRABLE_REGISTRY.items():
        if tool_name in cat["tools"]:
            return cat_key
    return None


def build_registry_payload() -> List[Dict[str, Any]]:
    """供个人空间 UI 使用的注册表载荷（类目标签由前端 i18n 解析）。"""
    return [
        {"key": cat_key, "tools": list(cat["tools"])}
        for cat_key, cat in DEFERRABLE_REGISTRY.items()
    ]


# ---------------------------------------------------------------------------
# 状态读写（全部防御性解析）
# ---------------------------------------------------------------------------

def _normalize_name_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result


def get_tool_loading_state(metadata: Any) -> Optional[Dict[str, Any]]:
    """从对话 metadata 读取并归一化 tool_loading 状态。

    返回 None 表示未启用（老对话无字段 / enabled 非真 / 结构损坏）。
    返回的 dict 保证含 enabled/deferred_set/initial_exposed/loaded/pending 五键，
    且 loaded/pending 相互一致（pending = deferred_set − loaded 重算为准）。
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get(METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is not True:
        return None
    deferred_set = [n for n in _normalize_name_list(raw.get("deferred_set"))
                    if category_of_tool(n) is not None]
    loaded = [n for n in _normalize_name_list(raw.get("loaded")) if n in deferred_set]
    initial_exposed = _normalize_name_list(raw.get("initial_exposed"))
    state = {
        "enabled": True,
        "deferred_set": deferred_set,
        "initial_exposed": initial_exposed,
        "loaded": loaded,
        "pending": [n for n in deferred_set if n not in loaded],
    }
    return state


def is_tool_loading_enabled(metadata: Any) -> bool:
    return get_tool_loading_state(metadata) is not None


def is_deferred_not_loaded(state: Optional[Dict[str, Any]], tool_name: str) -> bool:
    """守门判定：该工具处于「已延迟且尚未加载」状态（应拦截并引导 load_tools）。"""
    if not state:
        return False
    return tool_name in state["pending"]


# ---------------------------------------------------------------------------
# 快照与状态迁移
# ---------------------------------------------------------------------------

def build_snapshot(
    deferred_config: Optional[Iterable[str]],
    built_tool_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """计算创建对话时的 tool_loading 快照（五元组）。

    Args:
        deferred_config: 用户配置要延迟的工具名（None/非法 => 默认全集）。
        built_tool_names: 创建时实际暴露的工具名全集（用于记录 initial_exposed；
            不传则记录为空列表，由 define_tools 以「构建集 − deferred_set」语义
            动态过滤，不影响行为）。
    """
    registry_names = deferrable_tool_names()
    if deferred_config is None:
        deferred_set = list(registry_names)
    else:
        requested = set(_normalize_name_list(list(deferred_config)))
        deferred_set = [n for n in registry_names if n in requested]

    initial_exposed: List[str] = []
    if built_tool_names is not None:
        deferred_lookup = set(deferred_set)
        initial_exposed = [n for n in built_tool_names if n not in deferred_lookup]
        if deferred_set and LOAD_TOOLS_NAME not in initial_exposed:
            initial_exposed.append(LOAD_TOOLS_NAME)

    return {
        "enabled": True,
        "deferred_set": deferred_set,
        "initial_exposed": initial_exposed,
        "loaded": [],
        "pending": list(deferred_set),
    }


def snapshot_overrides_from_prefs(
    personalization_config: Any,
    multi_agent_mode: bool = False,
) -> Dict[str, Any]:
    """创建对话时合并进 metadata_overrides 的 tool_loading 部分（适配层调用）。

    返回 {} 表示本对话不启用（多智能体对话 v1 不启用 / 个人空间总开关关闭）。
    老对话（功能上线前创建）无此字段，读侧一律按未启用处理，不做迁移。

    Args:
        personalization_config: 调用方已加载的个人空间配置（本函数不做 I/O）。
        multi_agent_mode: 是否为多智能体对话。
    """
    if multi_agent_mode:
        return {}
    prefs = personalization_config if isinstance(personalization_config, dict) else {}
    if prefs.get("tool_loading_enabled", True) is not True:
        return {}
    return {METADATA_KEY: build_snapshot(prefs.get("tool_loading_deferred"))}


def mark_tools_loaded(state: Dict[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    """返回 loaded 追加指定工具后的新 state（pending 同步重算）。"""
    deferred_set = list(state.get("deferred_set") or [])
    loaded = list(state.get("loaded") or [])
    for name in names:
        if name in deferred_set and name not in loaded:
            loaded.append(name)
    return {
        **state,
        "loaded": loaded,
        "pending": [n for n in deferred_set if n not in loaded],
    }


def reset_state_after_compression(state: Dict[str, Any]) -> Dict[str, Any]:
    """对话压缩后的重置：loaded 清空、pending 回满 deferred_set。

    压缩会丢弃/摘要历史中的 load_tools 工具结果，模型上下文里已没有这些
    定义，状态必须随之前滚到「创建时」语义；initial_exposed 与 deferred_set
    不变，冻结 prompt 目录依然准确。
    """
    deferred_set = list(state.get("deferred_set") or [])
    return {
        **state,
        "loaded": [],
        "pending": list(deferred_set),
    }


# ---------------------------------------------------------------------------
# load_tools 工具定义
# ---------------------------------------------------------------------------

def build_load_tools_definition() -> Dict[str, Any]:
    """构建 load_tools 的工具定义（intent 由 define_tools 末尾统一注入）。"""
    return {
        "type": "function",
        "function": {
            "name": LOAD_TOOLS_NAME,
            "description": (
                "按名加载「按需加载的工具」目录中列出的未加载工具：在工具结果中返回这些工具的"
                "完整 JSON 定义，之后即可像普通工具一样直接调用。一次可传多个工具名；"
                "用户意图明确属于某个类目时，建议一次性加载该类目所需的全部工具。"
                "已加载的工具在本对话中持续可用；请勿重复传入已加载的工具，重复加载会被拒绝。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "要加载的工具名列表，必须来自 system prompt 中「按需加载的工具」"
                            "目录里列出的工具名，例如 [\"activate_workflow\", \"list_workflows\"]"
                        ),
                    }
                },
                "required": ["tool_names"],
            },
        },
    }


def build_tool_stub(tool_def: Dict[str, Any]) -> Dict[str, Any]:
    """把完整工具定义折叠为 stub：保留 name 与一句引导式描述，参数置空。

    stub 常驻 tools 数组的意义：模型只敢调用「出现在工具列表里」的工具
    （训练先验），纯靠 tool result 塞定义会导致模型反复 load 而不敢调。
    stub 内容只由完整定义决定（与加载状态无关），tools 数组每轮逐字节
    一致，前缀缓存不受影响。
    """
    fn = (tool_def or {}).get("function") or {}
    name = fn.get("name") or ""
    desc = (fn.get("description") or "").strip()
    # 取首句作为用途摘要（首行/首个中文句号截断，60 字符兑底），避免 stub
    # 携带整段说明。描述主要起「何时用」与「先 load」的引导作用。
    first = desc.split("\n", 1)[0].split("。", 1)[0].strip() if desc else ""
    if len(first) > 60:
        first = first[:60].rstrip() + "…"
    guide = "（调用前需先通过 load_tools 加载本工具的参数定义）"
    stub_desc = f"{first}。{guide}" if first else guide
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": stub_desc,
            "parameters": {"type": "object", "properties": {}},
        },
    }


# ---------------------------------------------------------------------------
# prompt 目录渲染
# ---------------------------------------------------------------------------

def render_catalog(
    deferred_set: Iterable[str],
    unavailable: Optional[Set[str]] = None,
) -> str:
    """按类目渲染 prompt 目录（仅含该对话实际延迟的工具；空类目整行省略）。

    Args:
        deferred_set: 该对话延迟集。
        unavailable: 当前不可用的工具（如分类被禁用），从目录中剔除。
    """
    deferred: Set[str] = set(deferred_set or [])
    skip: Set[str] = set(unavailable or set())
    lines: List[str] = []
    for cat in DEFERRABLE_REGISTRY.values():
        tools = [n for n in cat["tools"] if n in deferred and n not in skip]
        if not tools:
            continue
        lines.append(f"- {cat['label']}（{', '.join(tools)}）：{cat['when_to_use']}")
    return "\n".join(lines)
