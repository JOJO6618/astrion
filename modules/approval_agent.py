from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from config import LOGS_DIR
from modules.review_agent_config import resolve_review_agent_config


DEFAULT_MAX_ROUNDS = 3
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_COMMAND_TIMEOUT = 20
DEBUG_SAVE_APPROVAL_AGENT_TRANSCRIPT = True
DEBUG_TRANSCRIPT_DIR = Path(LOGS_DIR) / "approval_agent"


def load_approval_agent_config() -> Dict[str, Any]:
    """加载审批智能体配置：统一走个人空间「审核智能体」设置 + 子智能体模型库。"""
    cfg = resolve_review_agent_config("auto_approval")
    cfg["name"] = "auto-approval-agent"
    return cfg


class ApprovalAgent:
    def __init__(self, *, web_terminal: Any):
        self.web_terminal = web_terminal
        self.cfg = load_approval_agent_config()

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是审批智能体，只负责风险与越权判断，不判断任务必要性。"
            "审批模式允许正常文件修改，因此“写入/删除”不能一概拒绝，应结合用户输入意图判断。"
            "若操作涉及凭据读取（如 .env、密钥文件、token）、越权访问（工作区外敏感目录）、"
            "危险破坏（如 rm -rf、权限变更、系统级配置修改、可疑网络外传），"
            "且用户输入并未明确要求这些行为，则应拒绝。"
            "需要特别注意：提权命令、删除系统文件、批量破坏性 git 操作、访问 SSH/GPG/AWS 凭据目录。"
            "你可最多使用少量只读检查（尽量在3轮内完成），然后必须调用 approve_decision 给出 approved/rejected + reason。"
            "严禁直接在普通文本中输出“允许/禁止/同意/拒绝”这类最终结论；"
            "最终结论只能通过工具调用 approve_decision 返回。"
        )

    async def _run_readonly_command(self, command: str) -> Dict[str, Any]:
        work_path = Path(getattr(self.web_terminal.context_manager, "project_path", "."))
        timeout = int(self.cfg.get("max_command_timeout") or DEFAULT_MAX_COMMAND_TIMEOUT)
        try:
            result = await self.web_terminal.terminal_ops.run_command(
                command=command,
                working_dir=str(work_path),
                timeout=timeout,
                sandbox_write_access=False,
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

        url = str(self.cfg.get("url") or "").strip()
        key = str(self.cfg.get("key") or "").strip()
        model = str(self.cfg.get("model") or "").strip()
        if not url or not key or not model:
            out = {"decision": "rejected", "reason": "审批智能体配置缺失", "source": "approval_agent"}
            _flush_trace(out)
            return out
        endpoint = f"{url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        max_rounds = int(self.cfg.get("max_rounds") or DEFAULT_MAX_ROUNDS)
        timeout_seconds = int(self.cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        extra_params = dict(self.cfg.get("extra_params") or {})
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": (
                        "在终端中执行只读指令。可用于查看文件内容、搜索代码/文本、检查目录结构、"
                        "查看 git 状态与差异、确认路径是否存在、收集审批判断所需证据。"
                        "禁止用于写入、删除、改权限或其他修改性操作。"
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
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": payload_text},
        ]
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            rounds = 0
            forced_tool_retry_used = False
            while rounds < max_rounds + 2:
                rounds += 1
                if cancel_check:
                    external = cancel_check()
                    if external:
                        return external
                if progress_cb:
                    progress_cb({"stage": "model_call", "round": rounds, "message": f"审批轮次 {rounds}"})
                req = {
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0.0,
                    **extra_params,
                }
                _trace("request", {"round": rounds, "request": req})
                try:
                    resp = await client.post(endpoint, headers=headers, json=req)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    body = ""
                    try:
                        body = exc.response.text
                    except Exception:
                        body = str(exc)
                    _trace("http_error", {"round": rounds, "status_code": exc.response.status_code if exc.response else None, "body": body})
                    # 兼容某些模型/网关不接受额外参数：自动降级重试一次（去掉 extra_params）
                    if extra_params:
                        retry_req = {
                            "model": model,
                            "messages": messages,
                            "tools": tools,
                            "tool_choice": "auto",
                            "temperature": 0.0,
                        }
                        _trace("retry_request_without_extra_params", {"round": rounds, "request": retry_req})
                        retry_resp = await client.post(endpoint, headers=headers, json=retry_req)
                        try:
                            retry_resp.raise_for_status()
                            resp = retry_resp
                        except httpx.HTTPStatusError:
                            out = {
                                "decision": "rejected",
                                "reason": f"审批智能体请求失败({retry_resp.status_code})",
                                "source": "approval_agent",
                            }
                            _flush_trace(out)
                            return out
                    else:
                        out = {
                            "decision": "rejected",
                            "reason": f"审批智能体请求失败({exc.response.status_code if exc.response else 'unknown'})",
                            "source": "approval_agent",
                        }
                        _flush_trace(out)
                        return out
                choice = ((resp.json().get("choices") or [{}])[0] or {}).get("message") or {}
                reasoning_content = (
                    choice.get("reasoning_content")
                    or choice.get("reasoning")
                    or choice.get("thinking")
                    or ""
                )
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
                    out = {"decision": "rejected", "reason": "审批智能体未产出可执行决策", "source": "approval_agent"}
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
                        args = {}
                    if fn == "approve_decision":
                        decision = str(args.get("decision") or "").strip().lower()
                        reason = str(args.get("reason") or "").strip() or "无理由"
                        if decision in {"approved", "rejected"}:
                            out = {"decision": decision, "reason": reason, "source": "approval_agent"}
                            _flush_trace(out)
                            return out
                        out = {"decision": "rejected", "reason": "审批智能体返回非法决策", "source": "approval_agent"}
                        _flush_trace(out)
                        return out
                    if fn == "run_command":
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
        out = {"decision": "rejected", "reason": "审批智能体超出最大轮数", "source": "approval_agent"}
        _flush_trace(out)
        return out
