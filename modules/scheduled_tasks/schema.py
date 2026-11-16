"""Strict, versioned schedule definitions; no filesystem or execution side effects."""
from __future__ import annotations

import copy
import re
from typing import Any

import yaml

from .clock import integer, normalize_schedule

# 与 config/limits.py 的 REASONING_EFFORT_LEVELS 一致（本模块不导入项目配置，由测试校验同步）。
REASONING_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

MAX_CONFIG_BYTES = 256 * 1024


class DefinitionLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("配置不支持 YAML 引用")
        return super().compose_node(parent, index)


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("配置存在重复或非字符串字段")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


# YAML 1.2 boolean semantics: off/on/yes/no are ordinary strings.
DefinitionLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in rules if tag != 'tag:yaml.org,2002:bool']
    for key, rules in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
DefinitionLoader.add_implicit_resolver('tag:yaml.org,2002:bool', re.compile(r'^(?:true|false|True|False|TRUE|FALSE)$'), list('tTfF'))
DefinitionLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_definition(text: str) -> dict:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ValueError("配置文件不能超过 256 KB")
    try:
        result = yaml.load(text, Loader=DefinitionLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 格式错误：{exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("计划配置需要对象")
    return result


def known(value: Any, fields: set[str], path: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(f"{path}：需要对象")
    extra = set(value) - fields
    if extra:
        raise ValueError(f"{path}：未知字段 {', '.join(sorted(extra))}")
    return value


def choice(value: Any, options: set, path: str):
    if not isinstance(value, (str, type(None))) or value not in options:
        raise ValueError(f"{path}：无效选项")
    return value


def boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path}：应为 true 或 false")
    return value


def identifier(value: Any, path: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError(f"{path}：ID 格式无效")
    return value


def normalize_definition(value: dict, default_workspace: str | None, *, previous: dict | None = None) -> dict:
    known(value, {"schema_version", "id", "revision", "name", "enabled", "schedule", "target", "message", "configuration", "goal", "policies", "notifications"}, "plan")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("schema_version：只支持版本 1")
    name, message = value.get("name"), value.get("message")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("name：需要 1–80 字符的名称")
    if not isinstance(message, str) or not message.strip() or len(message) > 100000:
        raise ValueError("message：需要非空任务指令（最多 100000 字符）")
    target = known(value.get("target", {}), {"workspace_id", "conversation"}, "target")
    workspace = identifier(target.get("workspace_id") or default_workspace, "target.workspace_id")
    conversation = known(target.get("conversation", {"mode": "new"}), {"mode", "id"}, "target.conversation")
    mode = choice(conversation.get("mode"), {"new", "existing"}, "target.conversation.mode")
    conversation = {"mode": mode, **({"id": identifier(conversation.get("id"), "target.conversation.id")} if mode == "existing" else {})}
    if mode == "new" and target.get("conversation", {}).get("id") is not None:
        raise ValueError("新对话计划不能指定已有对话 ID")
    configuration = copy.deepcopy(known(value.get("configuration", {"mode": "inherit"}), {"mode", "model_key", "thinking", "reasoning_effort", "work_mode", "permission_mode", "execution_mode", "network_enabled", "ask_user", "worktree"}, "configuration"))
    config_mode = choice(configuration.get("mode"), {"inherit", "custom"}, "configuration.mode")
    if config_mode == "inherit" and set(configuration) != {"mode"}:
        raise ValueError("configuration：继承模式不能同时指定覆盖字段")
    if config_mode == "custom":
        if "model_key" in configuration and (not isinstance(configuration["model_key"], str) or not configuration["model_key"].strip()):
            raise ValueError("configuration.model_key：模型不能为空")
        for field in ("thinking", "network_enabled", "ask_user"):
            if field in configuration:
                boolean(configuration[field], f"configuration.{field}")
        for field, options in {
            "reasoning_effort": {None, *REASONING_EFFORT_LEVELS},
            "work_mode": {"execute", "ask", "plan"},
            "permission_mode": {"readonly", "approval", "auto_approval", "unrestricted"},
            "execution_mode": {"sandbox", "direct"},
        }.items():
            if field in configuration:
                choice(configuration[field], options, f"configuration.{field}")
        worktree = configuration.get("worktree", "main")
        if worktree != "main":
            known(worktree, {"branch_name"}, "configuration.worktree")
            if not isinstance(worktree.get("branch_name"), str) or not worktree["branch_name"].strip():
                raise ValueError("configuration.worktree.branch_name：需要已注册工作树分支")
        if configuration.get("work_mode") == "plan" and (configuration.get("permission_mode", "readonly") != "readonly" or configuration.get("execution_mode", "sandbox") != "sandbox"):
            raise ValueError("计划模式必须使用只读权限和沙箱")
        if configuration.get("permission_mode") == "readonly" and configuration.get("execution_mode", "sandbox") != "sandbox":
            raise ValueError("只读权限必须使用沙箱")
    goal = known(value.get("goal", {"mode": "off"}), {"mode", "max_turns", "max_tokens"}, "goal")
    goal_mode = choice(goal.get("mode"), {"off", "start", "continue"}, "goal.mode")
    if goal_mode == "continue" and mode != "existing":
        raise ValueError("继续目标需要指定已有对话")
    result_goal = {"mode": goal_mode}
    for key in ("max_turns", "max_tokens"):
        if key in goal:
            result_goal[key] = integer(goal[key], 1, 9007199254740991, f"goal.{key}")
    if goal_mode != "off" and configuration.get("work_mode", "execute") != "execute":
        raise ValueError("目标模式必须使用执行模式")
    policies = known(value.get("policies", {}), {"busy", "missed", "approval_timeout_seconds"}, "policies")
    policies = {
        "busy": choice(policies.get("busy", "skip"), {"skip", "merge"}, "policies.busy"),
        "missed": choice(policies.get("missed", "skip"), {"skip", "latest"}, "policies.missed"),
        "approval_timeout_seconds": integer(policies.get("approval_timeout_seconds", 3600), 1, 86400, "policies.approval_timeout_seconds"),
    }
    notifications = known(value.get("notifications", {"enabled": True}), {"enabled"}, "notifications")
    result = {
        "schema_version": 1, "name": name.strip(), "message": message,
        "enabled": boolean(value.get("enabled", False), "enabled"),
        "schedule": normalize_schedule(value.get("schedule"), previous=(previous or {}).get("schedule")),
        "target": {"workspace_id": workspace, "conversation": conversation},
        "configuration": configuration, "goal": result_goal, "policies": policies,
        "notifications": {"enabled": boolean(notifications.get("enabled", True), "notifications.enabled")},
    }
    if "id" in value:
        result["id"] = identifier(value["id"], "id")
    if "revision" in value:
        result["revision"] = integer(value["revision"], 1, 9007199254740991, "revision")
    return result


def dump_definition(value: dict) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120)
