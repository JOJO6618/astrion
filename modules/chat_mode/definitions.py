"""The entire Chat tool catalog. No execution or workspace tool is imported."""
from __future__ import annotations

from copy import deepcopy


def text(description=""):
    return {"type": "string", "description": description}


def tool(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {
            "intent": text("用不超过15个字说明操作目的"), **properties,
        }, "required": required},
    }}


CATALOG = [
    tool("web_search", "搜索外部信息。说明目的；结果含来源ID，引用使用【cite:src_xxx】。", {
        "query": text("查询内容"), "max_results": {"type": "integer"},
        "topic": {"type": "string", "enum": ["general", "news", "finance"]},
        "time_range": text("day/week/month/year"), "days": {"type": "integer"},
        "start_date": text("YYYY-MM-DD"), "end_date": text("YYYY-MM-DD"),
        "country": text(), "include_domains": {"type": "array", "items": text()},
    }, ["query"]),
    tool("extract_webpage", "搜索信息不够详细时提取指定网页，保留返回的来源ID。", {
        "url": text("要提取的http(s)网址"),
    }, ["url"]),
    tool("manage_personalization", "读取或更新共用偏好及Chat设置。不能修改Agent执行设置。", {
        "action": {"type": "string", "enum": ["read", "update"]},
        "field": text("偏好字段名"),
        "value": {"description": "字段的新值；字符串、布尔或null"},
    }, ["action"]),
    tool("update_memory", "更新Chat专属全局长期记忆，与Agent记忆隔离。", {
        "operation": {"type": "string", "enum": ["append", "replace", "delete"]},
        "content": text(), "index": {"type": "integer", "minimum": 1},
    }, ["operation"]),
    tool("search_chat_memory", "按关键词查找Chat专题记忆。命中后用read_chat_memory读取全文。", {
        "keywords": {"type": "array", "items": text(), "minItems": 1, "maxItems": 5},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
    }, ["keywords"]),
    tool("read_chat_memory", "读取Chat专题记忆全文。", {"name": text()}, ["name"]),
    tool("update_chat_memory", "保存Chat专题记忆，描述说明应在何时检索。", {
        "name": text("英文小写下划线名称"), "description": text("不超过100字"), "content": text(),
    }, ["name", "description", "content"]),
    tool("search_chat_history", "搜索当前账号Chat历史，仅搜索各对话最新分支。", {
        "query": text(), "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
    }, ["query"]),
    tool("read_chat_history", "读取当前账号指定Chat对话最新分支的纯净记录。", {
        "conversation_id": text(),
    }, ["conversation_id"]),
]
NAMES = frozenset(item["function"]["name"] for item in CATALOG)


def definitions():
    return deepcopy(CATALOG)
