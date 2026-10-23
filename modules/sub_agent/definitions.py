"""Public tools for named, resumable background agents."""
from __future__ import annotations

from typing import Any

ACCESS_LEVELS = ["working_dir_write", "workspace_write", "sandbox_write", "full_access"]
ACCESS_DESCRIPTION = (
    "创建后固定：working_dir_write仅子智能体工作目录可写；workspace_write仅当前工作区可写；"
    "sandbox_write当前工作区+沙箱授权路径可写；full_access宿主机完全访问。"
    "这些目录写限制适用于内置工具，MCP外置插件沿用服务自身权限。"
)


def tool(name: str, description: str, properties: dict[str, Any], required=()) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {
            **properties, "intent": {"type": "string", "description": "用不超过15个字说明操作目的。"},
        }, "required": list(required)},
    }}


NAME = {"type": "string", "description": "子智能体名称，同一主对话内唯一；使用创建时返回的名称。"}
TIMEOUT = {"type": "integer", "minimum": 1, "description": (
    "可选单轮工作时间预算（秒），不传表示本轮不限时。到期后完整处理当前模型响应及整批工具调用，"
    "在下一次模型请求前暂停并通知主智能体；保留上下文，可以再次发消息激活。"
)}


def build_status_tool() -> dict:
    return tool("get_sub_agent_status", (
        "查询当前主对话的子智能体状态、工作目录和最近一轮结果。"
        "省略names时返回全部已创建实例（包括idle和terminated）；传入names时仅查询指定名称。"
    ), {
        "names": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                  "description": "可选名称列表；省略查询全部，传入时不能为空。"},
    })


def build_master_tools() -> list[dict]:
    return [
        tool("create_sub_agent", (
            "创建一个由你命名、保留上下文的后台子智能体，立即返回。完成一轮后最后一条回复会作为user消息通知你。"
            "子智能体共享项目并拥有独立工作目录，支持后续追加任务和恢复。"
            "任务须明确目标、范围和必要产出，不能与其他子智能体争改同一文件。无需角色模板或强制交付文件。"
        ), {
            "name": NAME,
            "summary": {"type": "string", "description": "本次任务的简短描述。"},
            "task": {"type": "string", "description": "任务目标、要求、工作范围和需要的产出；纯分析可以直接回复。"},
            "intelligence": {"type": "string", "enum": ["same", "high", "medium", "low"],
                             "description": "模型选择：same跟随创建时主模型；high/medium/low使用用户配置，不可用明确报错。"},
            "working_dir": {"type": "string", "description": "可选项目内工作目录，可复用已有目录；不传则自动生成新目录。相对文件路径和默认命令cwd从此处解析。"},
            "access_level": {"type": "string", "enum": ACCESS_LEVELS, "description": ACCESS_DESCRIPTION},
            "timeout_seconds": TIMEOUT,
        }, ("name", "summary", "task", "intelligence", "access_level")),
        tool("send_message_to_sub_agent", "向已有子智能体发送任务或引导，立即返回。空闲时沿用上下文激活，必要时先从历史恢复；运行中消息在安全边界接收。", {
            "name": NAME, "message": {"type": "string", "description": "新任务或追加要求。"},
            "timeout_seconds": TIMEOUT,
        }, ("name", "message")),
        build_status_tool(),
        tool("stop_sub_agent", "立即请求取消子智能体当前响应或工具执行，不等待整批工具结束；缺失结果标为被主智能体取消。保留上下文并暂停，可发送消息继续。超时预算仍等完整批次结束。", {"name": NAME}, ("name",)),
        tool("terminate_sub_agent", "明确终结子智能体并释放其运行资源。终结后不能再次激活，文件和历史保留。", {"name": NAME}, ("name",)),
        tool("answer_sub_agent_question", "回答子智能体ask_master提出的问题，回答回填该工具结果。", {
            "question_id": {"type": "string"}, "answer": {"type": "string"},
        }, ("question_id", "answer")),
    ]


def build_sub_tools() -> list[dict]:
    return [
        tool("ask_master", "向主智能体提问，等待其回答；问题会立即交给主智能体。", {
            "question": {"type": "string"}, "question_id": {"type": "string"},
        }, ("question",)),
        tool("ask_other_agent", "向同一主对话的其他子智能体提问并等待回答，同时将提问汇报主智能体。", {
            "name": NAME, "question": {"type": "string"}, "question_id": {"type": "string"},
        }, ("name", "question")),
        tool("answer_other_agent", "回答其他子智能体的问题，回填对应ask_other_agent结果。", {
            "question_id": {"type": "string"}, "answer": {"type": "string"},
        }, ("question_id", "answer")),
        build_status_tool(),
    ]
