"""Trusted schedule adapter for the existing Agent runtime; never calls an LLM directly."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import asdict

from .service import BusyError

# 执行/计划模式下未允许询问时，随本次运行附带给模型的隐藏说明（前端不显示）。
ASK_USER_NOTICE = (
    "【定时任务】本条消息由定时任务自动发送，用户此刻不在场，无法回答提问。"
    "不要调用 ask_user 工具；遇到不确定的细节时，在当前权限内自行做出合理判断，"
    "并在最终回复中说明你所做的假设。"
)


class AgentRuntimeAdapter:
    @staticmethod
    def _scope(principal: dict, workspace_id: str):
        """Resolve the Host execution scope; historical tenant principals are rejected."""
        from config import IS_HOST_MODE
        from server.context import RuntimeIdentity
        from server.context.data import get_workspace_data
        username = principal["username"]
        if not (IS_HOST_MODE and principal.get("host_mode") and username == "host"):
            # 旧普通用户／API 用户计划只保留可识别的主体元数据，绝不隐式映射为 Host。
            raise PermissionError("计划所属身份不受支持，仅允许宿主机身份")
        identity = RuntimeIdentity(host_mode=True, host_workspace_id=workspace_id,
                                   is_api_user=False, role="admin")
        data = get_workspace_data(username, workspace_id, identity=identity)
        return data, identity

    @staticmethod
    def _read_conversation(data, conversation_id):
        document = data.manager_for(conversation_id).read_conversation(conversation_id)
        if document is None:
            raise ValueError("目标对话不存在或不属于所选工作区")
        metadata = document.get("metadata") or {}
        if metadata.get("conversation_kind") == "chat":
            raise ValueError("定时任务仅支持 Agent 对话")
        # Host conversations share a data directory; file existence alone is not ownership.
        project = metadata.get("project_path")
        if project:
            from pathlib import Path
            if Path(project).expanduser().resolve() != Path(data.workspace.project_path).resolve():
                relative = metadata.get("project_relative_path")
                expected = data.manager()._prepare_project_path_metadata(str(data.workspace.project_path))
                if not relative or relative != expected.get("project_relative_path"):
                    raise ValueError("目标对话不属于所选工作区")
        return document

    @staticmethod
    def _defaults(data):
        from modules.personalization_manager import load_personalization_config
        from modules.conversation_modes import resolve_conversation_modes
        from server.context.new_conversation import _default_model_key
        prefs = load_personalization_config(data.workspace.data_dir)
        modes = resolve_conversation_modes({"work_mode": prefs.get("default_work_mode", "plan"),
                                            "permission_mode": prefs.get("default_permission_mode", "approval"),
                                            "execution_mode": "sandbox", "network_permission": "restricted"},
                                           host_mode=data.identity.host_mode)
        run_mode = prefs.get("default_run_mode") or "fast"
        return {**modes, "model_key": _default_model_key(prefs),
                "run_mode": run_mode, "thinking_mode": run_mode == "thinking",
                "reasoning_effort": prefs.get("default_reasoning_effort"), "chatgpt_fast_mode": False}

    def _settings(self, definition, principal, data):
        target = definition["target"]["conversation"]
        settings = copy.deepcopy(principal.get("configuration_snapshot") or self._defaults(data))
        old_metadata = None
        if target["mode"] == "existing":
            old_metadata = self._read_conversation(data, target["id"]).get("metadata") or {}
            for key in ("model_key", "run_mode", "thinking_mode", "reasoning_effort", "work_mode", "permission_mode", "execution_mode", "network_permission", "chatgpt_fast_mode"):
                if key in old_metadata:
                    settings[key] = old_metadata[key]
        configuration = definition["configuration"]
        if configuration["mode"] == "custom":
            for key in ("model_key", "reasoning_effort", "work_mode", "permission_mode", "execution_mode"):
                if key in configuration:
                    settings[key] = configuration[key]
            if "thinking" in configuration:
                settings["thinking_mode"] = configuration["thinking"]
                settings["run_mode"] = "thinking" if configuration["thinking"] else "fast"
            if "network_enabled" in configuration:
                settings["network_permission"] = "full" if configuration["network_enabled"] else "none"
        if settings.get('run_mode') == 'deep':
            settings['run_mode'] = 'thinking'
        settings["thinking_mode"] = settings.get("run_mode") == "thinking"
        if definition["goal"]["mode"] != "off":
            settings["work_mode"] = "execute"
            if settings["permission_mode"] not in {"auto_approval", "unrestricted"}:
                settings["permission_mode"] = "auto_approval"
        from modules.conversation_modes import resolve_conversation_modes
        resolved = resolve_conversation_modes(settings, host_mode=data.identity.host_mode)
        for key in ('work_mode', 'permission_mode', 'execution_mode'):
            if key in configuration and settings[key] != resolved[key]:
                raise ValueError(f'显式 {key} 与工作模式或权限规则冲突')
        settings.update(resolved)
        return settings, old_metadata

    def validate(self, definition, principal):
        from server.context.new_conversation import validate_creation_settings
        data, identity = self._scope(principal, definition["target"]["workspace_id"])
        settings, old = self._settings(definition, principal, data)
        validate_creation_settings(settings, host_mode=identity.host_mode, can_manage=identity.role == "admin")
        worktree = definition['configuration'].get('worktree')
        if isinstance(worktree, dict):
            if old is not None and old.get('git_worktree_enabled') is not True:
                raise ValueError('目标对话未启用工作树，请选择已启用的对话或新建对话')
            from modules.git_worktree.query import view
            trees = view(data.workspace, old or {'git_worktree_enabled': True})['worktrees']
            target = next((tree for tree in trees if tree['branch'] == worktree['branch_name']), None)
            if target is None or target['is_main'] or target['unavailable']:
                raise ValueError('请选择已注册且可用的非主目录工作树分支')
            if not target['authorized'] and settings['execution_mode'] != 'direct':
                raise PermissionError('目标工作树不在当前路径授权范围内')
        return self._validated_principal(principal, data, identity, settings,
                                         freeze=definition["target"]["conversation"]["mode"] == "new")

    @staticmethod
    def _validated_principal(principal, data, identity, settings, *, freeze=False):
        from modules.admin_policy_manager import get_effective_policy
        if not identity.host_mode:
            raise PermissionError("计划所属身份不受支持，仅允许宿主机身份")
        # Host 没有普通用户记录或邀请码，策略按其显式 Host 角色解析。
        policy = get_effective_policy(principal["username"], identity.role, None)
        disabled = set(policy.get("disabled_models") or [])
        if settings["model_key"] in disabled:
            raise PermissionError("此模型已被管理员禁用")
        result = {"username": principal["username"], **asdict(identity)}
        if freeze:
            result["configuration_snapshot"] = settings
        return result

    @staticmethod
    def _busy(data, conversation_id):
        from server.tasks import task_manager
        from server.context.background import background_status
        from modules.goal_state_manager import GoalStateManager, ACTIVE_STATUSES
        records = task_manager.list_tasks(data.username, data.workspace_id)
        if any(record.conversation_id == conversation_id and record.status in {"pending", "running", "cancel_requested"} for record in records):
            raise BusyError("conversation_busy")
        if any(background_status(data, conversation_id).values()):
            raise BusyError("conversation_background_busy")
        if GoalStateManager(data.workspace.data_dir, conversation_id).state["status"] in ACTIVE_STATUSES:
            raise BusyError("conversation_goal_busy")

    def start(self, run):
        from server.runtime import TrustedPrincipal, TaskParams, RuntimeContext, InternalDirectives, runtime_service
        from server.context import get_user_resources, ensure_conversation_loaded
        from server.main_task_gate import try_acquire_main_task_gate, release_main_task_gate
        from utils.conversation_manager import ConversationManager
        from core.tool_loading import snapshot_overrides_from_prefs
        from modules.personalization_manager import load_personalization_config
        definition = run["definition"]
        principal = self.validate(definition, run["principal"])
        data, identity = self._scope(principal, definition["target"]["workspace_id"])
        settings, _ = self._settings(definition, principal, data)
        target = definition["target"]["conversation"]
        created = target["mode"] == "new"
        if created:
            prefs = load_personalization_config(data.workspace.data_dir)
            manager = ConversationManager(base_dir=str(data.workspace.data_dir), project_path=str(data.workspace.project_path))
            metadata = {**settings, **snapshot_overrides_from_prefs(prefs),
                        "schedule_id": run["schedule_id"], "schedule_run_id": run["id"],
                        "browser_tools_enabled": prefs.get("browser_tools_default") is True,
                        "skip_auto_title_generation": True}
            if definition["configuration"].get("worktree") not in (None, "main"):
                metadata["git_worktree_enabled"] = True
            conversation_id = manager.create_conversation(str(data.workspace.project_path), model_key=settings["model_key"],
                                                         run_mode=settings["run_mode"], thinking_mode=settings["thinking_mode"],
                                                         metadata_overrides=metadata)
            manager.update_conversation_title(conversation_id, definition["name"])
        else:
            conversation_id = target["id"]
            self._busy(data, conversation_id)
        terminal, workspace = get_user_resources(data.username, data.workspace_id, conversation_id=conversation_id,
                                                 identity=identity, update_session=False)
        gate = try_acquire_main_task_gate(terminal)
        if gate is None:
            raise BusyError("conversation_gate_busy")
        handed_off = False
        from modules.git_worktree import current_state
        from modules.git_worktree.binding import granted_root
        from modules.git_worktree.grants import bind_extra_roots
        root_context = None
        try:
            metadata = self._read_conversation(data, conversation_id).get('metadata') or {}
            external_root = granted_root(current_state(metadata))
            root_context = bind_extra_roots([external_root] if external_root else [])
            root_context.__enter__()
            if not created:
                self._busy(data, conversation_id)
            ensure_conversation_loaded(terminal, conversation_id, workspace=workspace, update_session=False)
            if created or definition["configuration"]["mode"] == "custom":
                self._apply_settings(terminal, conversation_id, settings)
            worktree = definition["configuration"].get("worktree")
            if worktree and worktree != "main":
                from modules.git_worktree.service import Worktrees
                result = asyncio.run(Worktrees(terminal).change({"action": "enter", "branch_name": worktree["branch_name"]}, origin="model"))
                if not result.get("success"):
                    raise ValueError(result.get("error") or "工作树绑定失败")
            elif worktree == "main":
                from modules.git_worktree import current_state
                if (current_state(terminal.context_manager.conversation_metadata) or {}).get("active"):
                    from modules.git_worktree.service import Worktrees
                    result = asyncio.run(Worktrees(terminal).change(None, exit=True, origin="model"))
                    if not result.get("success"):
                        raise ValueError(result.get("error") or "工作树退出失败")
            p = TrustedPrincipal(username=data.username, workspace_id=data.workspace_id, role=identity.role,
                                 host_mode=identity.host_mode, host_workspace_id=identity.host_workspace_id,
                                 is_api_user=identity.is_api_user, preferred_model_key=settings["model_key"],
                                 preferred_run_mode=settings["run_mode"], preferred_thinking_mode=settings["thinking_mode"])
            goal = definition["goal"]
            params = TaskParams(message=definition["message"], conversation_id=conversation_id,
                                model_key=settings["model_key"], run_mode=settings["run_mode"],
                                goal_action=goal["mode"] if goal["mode"] != "off" else None,
                                goal_options={k: goal[k] for k in ("max_turns", "max_tokens") if k in goal},
                                message_source="schedule", approval_timeout_seconds=definition["policies"]["approval_timeout_seconds"])
            directives = InternalDirectives(main_task_gate_token=gate,
                                            hidden_user_notice=self._ask_user_notice(definition, settings))
            record = runtime_service.create_task(RuntimeContext(p, params, directives))
            handed_off = True
            return {"task_id": record.task_id, "conversation_id": conversation_id}
        finally:
            if root_context is not None:
                root_context.__exit__(None, None, None)
            if not handed_off:
                release_main_task_gate(terminal, gate)

    @staticmethod
    def _ask_user_notice(definition, settings):
        """自定义配置下执行/计划模式默认不让模型询问用户；询问模式由后端自行禁用 ask_user，
        目标模式的注入语已禁止交互，沿用对话配置则不附加任何约束。"""
        configuration = definition["configuration"]
        if configuration["mode"] != "custom" or configuration.get("ask_user") is True:
            return None
        if definition["goal"]["mode"] != "off" or settings.get("work_mode") not in {"execute", "plan"}:
            return None
        return ASK_USER_NOTICE

    @staticmethod
    def _apply_settings(terminal, conversation_id, settings):
        terminal.set_model(settings["model_key"])
        terminal.set_run_mode(settings["run_mode"])
        terminal.set_reasoning_effort(settings["reasoning_effort"])
        terminal.switch_work_mode(settings["work_mode"], persist=True, conversation_id=conversation_id)
        terminal.set_permission_mode(settings["permission_mode"], persist=True, conversation_id=conversation_id)
        result = terminal.set_execution_mode(settings["execution_mode"])
        if isinstance(result, dict) and result.get('success') is False:
            raise ValueError(result.get('error') or '执行环境切换失败')
        terminal.set_network_permission(settings["network_permission"])
        pending = dict.fromkeys(('pending_permission_mode', 'pending_execution_mode', 'pending_network_permission'))
        for key in pending:
            setattr(terminal, key, None)
        updates = {**settings, **pending}
        manager = terminal.context_manager._get_conversation_manager_for_id(conversation_id)
        if not manager.update_conversation_metadata(conversation_id, updates):
            raise RuntimeError("对话配置保存失败")
        terminal.context_manager.conversation_metadata.update(updates)

    def observe(self, run):
        from server.runtime import runtime_service
        from server.tasks import task_manager
        from modules.goal_state_manager import GoalStateManager, ACTIVE_STATUSES
        data, _ = self._scope(run["principal"], run["definition"]["target"]["workspace_id"])
        record = runtime_service.get_task(run["username"], run["task_id"])
        if record is None:
            return {"state": "interrupted", "reason": "task_unavailable"}
        if run["definition"]["goal"]["mode"] != "off":
            goal = GoalStateManager(data.workspace.data_dir, run["conversation_id"]).state
            if goal["status"] in ACTIVE_STATUSES:
                if goal.get("task_id") and goal["task_id"] != record.task_id:
                    return {"state": "running", "task_id": goal["task_id"]}
                return {"state": "running"}
            if goal["status"] in {"blocked", "paused"} and record.status not in {"running", "pending", "cancel_requested"}:
                return {"state": "stopped" if run["stop_requested"] else "failed", "reason": goal.get("last_message") or goal.get("stopped_reason")}
        pending = runtime_service.list_pending_approvals(run["username"], run["conversation_id"])
        if any(pending.values()) and record.status in {"running", "pending", "cancel_requested"}:
            return {"state": "waiting"}
        if record.status in {"pending", "running", "cancel_requested"}:
            return {"state": "running"}
        from server.context.background import background_status
        if any(background_status(data, run["conversation_id"]).values()):
            return {"state": "running"}
        # Notifications may have created a new main run after the initial response.
        following = [item for item in task_manager.list_tasks(run["username"], data.workspace_id)
                     if item.conversation_id == run["conversation_id"] and item.created_at >= record.created_at
                     and item.task_type == "notice" and item.status in {"running", "pending", "cancel_requested"}]
        if following:
            return {"state": "running", "task_id": max(following, key=lambda item: item.created_at).task_id}
        document = self._read_conversation(data, run["conversation_id"])
        summary = ""
        for message in reversed(document.get("messages") or []):
            if message.get("role") == "assistant" and message.get("content"):
                content = message["content"]
                summary = content if isinstance(content, str) else "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text")
                if summary:
                    break
        return {"state": {"succeeded": "success", "failed": "failed", "stopped": "stopped", "canceled": "stopped"}.get(record.status, "failed"),
                "reason": record.error, "summary": summary[:12000]}

    @staticmethod
    def cancel(run):
        if run.get("task_id"):
            from server.runtime import runtime_service
            runtime_service.cancel_task(run["username"], run["task_id"])

    @staticmethod
    def notify(run):
        return "available"
