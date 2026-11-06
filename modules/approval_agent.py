from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import LOGS_DIR
from modules.review_agent_config import resolve_review_agent_config
from modules.review_model_caller import ReviewModelCallError, call_review_model, review_billing_for
from modules.approval_policy import APPROVAL_SYSTEM_PROMPT
from modules.i18n import tr


DEFAULT_MAX_ROUNDS = 3
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_COMMAND_TIMEOUT = 20
# 审批 transcript 含完整 messages / 命令 / 结果，最容易接触敏感动作与路径，
# 默认关闭（防 Secret Sink）；仅在调试审批链路时临时改回 True，用完恢复。
DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT = False
DEBUG_TRANSCRIPT_DIR = Path(LOGS_DIR) / "approval_agent"


def load_approval_agent_config() -> Dict[str, Any]:
    """加载审批智能体配置：统一走个人空间「审核智能体」设置 + 子智能体模型库。"""
    cfg = resolve_review_agent_config("auto_approval")
    cfg["name"] = "auto-approval-agent"
    return cfg


class ApprovalAgent:
    def __init__(self, *, web_terminal: Any, conversation_id: Optional[str] = None):
        self.web_terminal = web_terminal
        self.cfg = load_approval_agent_config()
        # 审核请求计入所属对话账本（kind=review_agent）；对话身份缺失时不计费
        self.billing = review_billing_for(web_terminal, conversation_id, "auto_approval")
        self.review_history = None

    @staticmethod
    def _system_prompt() -> str:
        return APPROVAL_SYSTEM_PROMPT

    async def _run_readonly_command(self, command: str) -> Dict[str, Any]:
        work_path = Path(getattr(self.web_terminal.context_manager, "project_path", "."))
        timeout = int(self.cfg.get("max_command_timeout") or DEFAULT_MAX_COMMAND_TIMEOUT)
        try:
            from modules.execution_scope import ExecutionScope, bind_execution_scope
            scope = ExecutionScope(
                executor_kind="review", actor_id="auto_approval",
                workspace_root=work_path.resolve(), access_level="sandbox_write",
            )
            with bind_execution_scope(scope):
                result = await self.web_terminal.terminal_ops.run_command(
                    command=command,
                    working_dir=str(work_path),
                    timeout=timeout,
                    sandbox_write_access=False,
                    network_permission="restricted",
                )
            return result if isinstance(result, dict) else {"success": False, "error": "run_command 返回格式异常"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def review(
        self,
        *,
        payload_text: str,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        debug_transcript: List[Dict[str, Any]] = []
        messages: List[Dict[str, Any]] = []

        def _error(code: str, reason: str) -> Dict[str, Any]:
            return {"decision": "error", "code": code, "reason": reason, "source": "approval_agent"}

        def _trace(event: str, payload: Dict[str, Any]) -> None:
            if not DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT:
                return
            debug_transcript.append({
                "ts": int(time.time() * 1000),
                "event": event,
                "payload": payload,
            })

        def _flush_trace(final_result: Dict[str, Any]) -> None:
            if not DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT:
                return
            try:
                DEBUG_TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
                row = {
                    "created_at": int(time.time() * 1000),
                    "messages": messages,
                    "trace": debug_transcript,
                    "final_result": final_result,
                }
                file = DEBUG_TRANSCRIPT_DIR / f"approval_{int(time.time() * 1000)}.json"
                file.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        profile = self.cfg.get("profile")
        if not isinstance(profile, dict):
            out = _error("review_config_missing", tr("approval_agent.config_missing"))
            _flush_trace(out)
            return out
        max_rounds = int(self.cfg.get("max_rounds") or DEFAULT_MAX_ROUNDS)
        timeout_seconds = int(self.cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": (
                        "在终端中执行只读指令。可用于查看文件内容、搜索代码/文本、检查目录结构、"
                        "查看 git 状态与差异、确认路径是否存在、收集审批判断所需证据。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": (
                                    "要执行的终端命令字符串。应优先使用只读命令，例如 ls、cat、grep、find、"
                                    "rg、git status、git diff、pwd、stat 等。"
                                ),
                            }
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "approve_decision",
                    "description": (
                        "提交最终审批结果并结束本次审批流程。只能返回 approved 或 rejected，"
                        "并提供简洁、可审计的理由。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["approved", "rejected"],
                                "description": "审批结论：approved 表示同意执行；rejected 表示拒绝执行。",
                            },
                            "reason": {
                                "type": "string",
                                "description": (
                                    "审批理由。应明确风险点或放行依据，例如是否涉及凭据访问、越权路径、"
                                    "破坏性命令、以及是否与用户输入意图一致。"
                                ),
                            },
                        },
                        "required": ["decision", "reason"],
                    },
                },
            },
        ]
        if self.review_history is not None:
            tools.append({"type": "function", "function": {
                "name": "read_review_history",
                "description": "只读补查本次审核冻结的同一对话历史；由近及远分页，可按原文关键词筛选。",
                "parameters": {"type": "object", "properties": {
                    "offset": {"type": "integer", "minimum": 0, "description": "跳过的记录数，默认0。"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 20, "description": "返回条数，默认10。"},
                    "query": {"type": "string", "description": "可选的原文关键词。"},
                }, "additionalProperties": False},
            }})
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": payload_text},
        ]
        rounds = 0
        forced_tool_retry_used = False
        while rounds < max_rounds + 2:
            rounds += 1
            if cancel_check:
                external = cancel_check()
                if external:
                    return external
            if progress_cb:
                progress_cb({"stage": "model_call", "round": rounds, "message": tr("approval_agent.round_progress", rounds=rounds)})
            _trace("request", {"round": rounds, "messages": messages, "tools": tools})
            for attempt in range(2):
                try:
                    choice = await call_review_model(self.cfg, messages, tools, timeout_seconds, billing=self.billing)
                    break
                except ReviewModelCallError as exc:
                    _trace("http_error", {"round": rounds, "attempt": attempt, "error": str(exc)})
                    details = getattr(exc, "details", {}) or {}
                    error_type = str(details.get("error_type") or "request_failed")
                    retryable = error_type in {"timeout", "network", "network_error", "connection_error", "server_error", "rate_limit"}
                    if attempt == 0 and retryable:
                        continue
                    out = _error("review_" + error_type, tr("approval_agent.request_failed", status=str(exc)))
                    _flush_trace(out)
                    return out
                except Exception as exc:
                    out = _error("review_request_failed", tr("approval_agent.request_failed", status=type(exc).__name__))
                    _flush_trace(out)
                    return out
            reasoning_content = str(choice.get("reasoning_content") or "")
            _trace(
                "response",
                {
                    "round": rounds,
                    "message": choice,
                    "reasoning_content": reasoning_content,
                },
            )
            tool_calls = choice.get("tool_calls") or []
            content = choice.get("content")
            if not tool_calls:
                if content or reasoning_content:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": str(content or ""),
                            "reasoning_content": str(reasoning_content or ""),
                        }
                    )
                if rounds <= max_rounds:
                    messages.append(
                        {
                            "role": "user",
                            "content": "禁止直接输出结论文本。必须调用工具：先按需调用 run_command，再调用 approve_decision 给出最终结论。"
                        }
                    )
                    continue
                if not forced_tool_retry_used:
                    forced_tool_retry_used = True
                    messages.append(
                        {
                            "role": "user",
                            "content": "你刚刚未通过工具给出明确结果。必须调用工具，禁止直接输出内容。请立即调用 approve_decision 返回最终结论。"
                        }
                    )
                    continue
                out = _error("review_no_decision", tr("approval_agent.no_decision"))
                _flush_trace(out)
                return out
            # 有 tool_calls 时追加一条完整 assistant 消息（与主智能体结构对齐）
            assistant_message = {
                "role": "assistant",
                "content": str(content or ""),
                "reasoning_content": str(reasoning_content or ""),
                "tool_calls": tool_calls,
            }
            messages.append(assistant_message)
            for call in tool_calls:
                fn = (call.get("function") or {}).get("name")
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args = None
                if not isinstance(args, dict):
                    out = _error("review_invalid_arguments", tr("approval_agent.invalid_decision"))
                    _flush_trace(out)
                    return out
                if fn == "approve_decision":
                    decision = str(args.get("decision") or "").strip().lower()
                    reason = str(args.get("reason") or "").strip() or tr("approval_agent.no_reason")
                    if decision in {"approved", "rejected"}:
                        out = {"decision": decision, "reason": reason, "source": "approval_agent"}
                        _flush_trace(out)
                        return out
                    out = _error("review_invalid_decision", tr("approval_agent.invalid_decision"))
                    _flush_trace(out)
                    return out
                if fn == "read_review_history" and self.review_history is not None:
                    try:
                        history_text = self.review_history.read(
                            offset=int(args.get("offset", 0)), count=int(args.get("count", 10)),
                            query=str(args.get("query") or ""),
                        )
                    except (ValueError, TypeError):
                        history_text = "参数错误：offset和count须为整数。"
                    messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                     "name": fn, "content": history_text})
                elif fn == "run_command":
                    cmd = str(args.get("command") or "").strip()
                    if progress_cb:
                        progress_cb({"stage": "run_command", "round": rounds, "command": cmd})
                    tool_result = await self._run_readonly_command(cmd) if cmd else {"success": False, "error": "command 不能为空"}
                    _trace("tool_result", {"round": rounds, "tool": "run_command", "command": cmd, "result": tool_result})
                    tool_content = json.dumps(tool_result, ensure_ascii=False)
                    messages.append({
                        "role": "tool",
                        "content": tool_content,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()) + f".{int((time.time()%1)*1000000):06d}",
                        "message_id": f"msg_{uuid.uuid4().hex}",
                        "tool_call_id": call.get("id"),
                        "name": "run_command",
                    })
                else:
                    messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                     "name": str(fn or "unknown"), "content": "该审核工具不可用。"})
        out = _error("review_budget_exhausted", tr("approval_agent.max_rounds"))
        _flush_trace(out)
        return out
