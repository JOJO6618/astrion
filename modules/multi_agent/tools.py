"""多智能体工具定义（OpenAI Function Calling 格式）。

主智能体侧（master）：替换原有的 4 个旧版子智能体工具，新增 9 个多智能体工具。
子智能体侧（sub_agent）：在现有 8 个基础工具之外新增 4 个通信工具。

工具处理函数（handler）不在这里实现，而是在 SubAgentManager/SubAgentTask 中
注册回调，工具执行入口仍走 SubAgentManager.execute_tool_for_sub_agent。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _inject_intent(properties: Dict[str, Any]) -> Dict[str, Any]:
    """为所有 properties 注入 intent 字段（与现有工具规范保持一致）。"""
    out: Dict[str, Any] = {}
    for k, v in properties.items():
        if k == "intent":
            out[k] = v
            continue
        out[k] = v
    # 在末尾追加 intent 字段
    if "intent" not in out:
        out["intent"] = {
            "type": "string",
            "description": "用不超过15个字向用户说明你要做什么，例如：派遣UI Operator设计配色。",
        }
    return out


# ----- 主智能体工具 -----
def _master_tool_create_sub_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_sub_agent",
            "description": (
                "创建一个属于多智能体团队的子智能体实例并启动。必须指定 role_id。"
                "实例会注入该角色的专属 prompt 与多智能体协作工具（ask_master 等）。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "role_id": {
                        "type": "string",
                        "description": "角色标识，例如 'ui-operator'/'full-stack-engineer'/'code-reviewer'/'researcher'。先用 list_agents 查看可用角色。",
                    },
                    "task": {
                        "type": "string",
                        "description": "要交给该子智能体执行的任务描述。要求包含：目标、范围、产出、注意事项。",
                    },
                    "agent_id": {
                        "type": "integer",
                        "description": "（可选）手动指定实例编号；不传时自动递增。",
                    },
                    "thinking_mode": {
                        "type": "string",
                        "enum": ["fast", "thinking"],
                        "description": "（可选）覆盖角色默认思考模式。不填使用角色配置。",
                    },
                }),
                "required": ["role_id", "task"],
            },
        },
    }


def _master_tool_stop_sub_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "stop_sub_agent",
            "description": "暂停指定子智能体实例，使其进入 idle 状态而不终结。暂停后可用 send_message_to_sub_agent 重新激活继续工作。",
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "agent_id": {"type": "integer", "description": "要暂停的子智能体编号。"},
                }),
                "required": ["agent_id"],
            },
        },
    }


def _master_tool_terminate_sub_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "terminate_sub_agent",
            "description": "强制终止指定子智能体实例。终止后无法恢复，已生成的文件保留。",
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "agent_id": {"type": "integer", "description": "要终止的子智能体编号。"},
                }),
                "required": ["agent_id"],
            },
        },
    }


def _master_tool_send_message_to_sub_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "send_message_to_sub_agent",
            "description": (
                "运行时引导/干预：向指定子智能体插入一条引导消息或新任务，立刻返回不等待回复。"
                "用于看到子智能体中间输出后立即纠正方向、追加要求。消息接收方根据当前状态"
                "（running/idle）自行决定是 inline 插入还是触发新一轮任务。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "agent_id": {"type": "integer", "description": "目标子智能体编号。"},
                    "message": {"type": "string", "description": "要插入的消息或新任务正文。"},
                }),
                "required": ["agent_id", "message"],
            },
        },
    }


def _master_tool_answer_sub_agent_question() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "answer_sub_agent_question",
            "description": (
                "回答子智能体通过 ask_master 工具提出的问题。回答内容会直接返回到子智能体"
                "ask_master 工具的 tool_call 结果里（不会以 user 消息插入子对话）。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "question_id": {"type": "string", "description": "提问消息里给出的 id，如 ask_xxx。"},
                    "answer": {"type": "string", "description": "给子智能体的回答正文。"},
                }),
                "required": ["question_id", "answer"],
            },
        },
    }


def _master_tool_create_custom_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "create_custom_agent",
            "description": (
                "创建/保存一个自定义角色到后端（~/.astrion/astrion/host/mutiagents/agents/）。"
                "后续可用 create_sub_agent 指定该角色。role_id 已存在时覆盖更新该角色设定；"
                "注意：覆盖只影响之后新创建的实例，已在运行的实例 prompt 已快照冻结，不受影响。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "role_id": {"type": "string", "description": "角色标识（英文小写下划线，如 api-designer）。"},
                    "name": {"type": "string", "description": "显示名（如 API Designer）。"},
                    "description": {"type": "string", "description": "一句话简述职责。"},
                    "body_prompt": {"type": "string", "description": "角色的自定义 prompt body（Markdown）。"},
                    "thinking_mode": {"type": "string", "enum": ["fast", "thinking"], "description": "默认思考模式。"},
                }),
                "required": ["role_id", "name", "body_prompt"],
            },
        },
    }


def _master_tool_list_agents() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": "列出所有可用的预置/自定义角色（role_id / name / 描述）。用于在 create_sub_agent 前选角色。",
            "parameters": {"type": "object", "properties": _inject_intent({})},
        },
    }


def _master_tool_list_active_sub_agents() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_active_sub_agents",
            "description": "列出当前多智能体会话中所有活跃/已创建的子智能体实例（agent_id/role/display_name/status）。",
            "parameters": {"type": "object", "properties": _inject_intent({})},
        },
    }


def _master_tool_get_sub_agent_status() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_sub_agent_status",
            "description": "查询一个或多个子智能体的详细状态。",
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "agent_ids": {"type": "array", "items": {"type": "integer"}, "description": "要查询的子智能体编号列表。"},
                }),
                "required": ["agent_ids"],
            },
        },
    }


MULTI_AGENT_MASTER_TOOLS: List[Dict[str, Any]] = [
    _master_tool_create_sub_agent(),
    _master_tool_stop_sub_agent(),
    _master_tool_terminate_sub_agent(),
    _master_tool_send_message_to_sub_agent(),
    _master_tool_answer_sub_agent_question(),
    _master_tool_create_custom_agent(),
    _master_tool_list_agents(),
    _master_tool_list_active_sub_agents(),
    _master_tool_get_sub_agent_status(),
]


# ----- 子智能体工具 -----
def _sub_tool_ask_master() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "ask_master",
            "description": (
                "向 Team Leader（主智能体）提问，工具调用会阻塞等待主智能体通过 answer_sub_agent_question 给出回答。"
                "用于需要主智能体决策的场合。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "question": {"type": "string", "description": "向 Team Leader 提问的内容。"},
                    "question_id": {"type": "string", "description": "（可选）问题 id；不传自动生成。"},
                }),
                "required": ["question"],
            },
        },
    }


def _sub_tool_ask_other_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "ask_other_agent",
            "description": (
                "向另一个子智能体提问，阻塞等待对方调用 answer_other_agent 回答。"
                "**注意**：调用此工具的同时，你必须在你的文本输出里向 Team Leader 输出一条汇报，"
                "说明你为何问、问谁、期望什么——不要偷偷沟通。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "target_agent_id": {"type": "integer", "description": "目标子智能体编号。"},
                    "question": {"type": "string", "description": "提问内容。"},
                    "question_id": {"type": "string", "description": "（可选）问题 id。"},
                }),
                "required": ["target_agent_id", "question"],
            },
        },
    }


def _sub_tool_answer_other_agent() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "answer_other_agent",
            "description": (
                "回答其他子智能体通过 ask_other_agent 提出的问题。"
                "answer 内容会直接返回到对方 ask_other_agent 工具结果中。"
            ),
            "parameters": {
                "type": "object",
                "properties": _inject_intent({
                    "source_agent_id": {"type": "integer", "description": "提问方 agent_id。"},
                    "question_id": {"type": "string", "description": "提问消息中的 id。"},
                    "answer": {"type": "string", "description": "回答内容。"},
                }),
                "required": ["source_agent_id", "question_id", "answer"],
            },
        },
    }


def _sub_tool_list_active_sub_agents() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "list_active_sub_agents",
            "description": "查询当前多智能体会话中所有活跃/已创建的子智能体。",
            "parameters": {"type": "object", "properties": _inject_intent({})},
        },
    }


MULTI_AGENT_SUB_AGENT_TOOLS: List[Dict[str, Any]] = [
    _sub_tool_ask_master(),
    _sub_tool_ask_other_agent(),
    _sub_tool_answer_other_agent(),
    _sub_tool_list_active_sub_agents(),
]


# ----- 构造函数（供现有 tools_definition 调用） -----
def build_master_tools_for_conversation() -> List[Dict[str, Any]]:
    """返回主智能体在多智能体模式下应额外提供的工具列表。"""
    return list(MULTI_AGENT_MASTER_TOOLS)


def build_sub_agent_tools_for_role() -> List[Dict[str, Any]]:
    """返回子智能体在多智能体模式下应额外提供的工具列表。"""
    return list(MULTI_AGENT_SUB_AGENT_TOOLS)