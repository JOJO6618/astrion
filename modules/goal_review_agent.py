"""目标审核智能体（Goal Review Agent）。

由 modules/approval_agent.py fork 而来。职责不同：
- approval_agent 判断"工具调用是否越权/危险"。
- goal_review_agent 判断"长期目标是否真正达成"。

两种审核模式：
- readonly：只给 report_goal_status 一个工具，不注入 run_command，system prompt 不含模式说明。
- active：额外注入只读 run_command 工具，system prompt 追加一句允许其取证。

返回结构：{"status": "done"|"continue", "message": str, "source": "goal_review_agent"}。
兜底（超轮/异常/未产出结论）保守返回 continue，避免误判完成。
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from config import PROMPTS_DIR, LOGS_DIR
from modules.goal_state_manager import REVIEW_MODE_ACTIVE, REVIEW_MODE_READONLY
from modules.review_agent_config import resolve_review_agent_config

PROMPT_NAME = "goal_review_agent.txt"
DEFAULT_MAX_ROUNDS = 3
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_COMMAND_TIMEOUT = 60
DEBUG_SAVE_GOAL_REVIEW_TRANSCRIPT = True
DEBUG_TRANSCRIPT_DIR = Path(LOGS_DIR) / "goal_review_agent"

# 兜底续命消息（审核智能体未能产出明确结论时使用）
FALLBACK_CONTINUE_MESSAGE = "审核未能给出明确结论。请重新对照目标核查当前进度，找出尚未完成的部分并继续推进。"


def load_goal_review_agent_config() -> Dict[str, Any]:
    """加载目标审核智能体配置：统一走个人空间「审核智能体」设置 + 子智能体模型库。"""
    cfg = resolve_review_agent_config("goal_review")
    cfg["name"] = "goal-review-agent"
    return cfg


class GoalReviewAgent:
    def __init__(self, *, web_terminal: Any):
        self.web_terminal = web_terminal
        self.cfg = load_goal_review_agent_config()

    @staticmethod
    def _system_prompt(review_mode: str) -> str:
        try:
            template = (Path(PROMPTS_DIR) / PROMPT_NAME).read_text(encoding="utf-8")
        except Exception:
            template = "你是目标审核智能体。你必须调用 report_goal_status 返回 done 或 continue。"

        active_start = "{{ACTIVE_REVIEW_ONLY}}"
        active_end = "{{/ACTIVE_REVIEW_ONLY}}"
        if review_mode == REVIEW_MODE_ACTIVE:
            return template.replace(active_start, "").replace(active_end, "").strip()

        while active_start in template and active_end in template:
            start = template.find(active_start)
            end = template.find(active_end, start)
            if start < 0 or end < 0:
                break
            template = template[:start] + template[end + len(active_end):]
        return template.strip()

    @staticmethod
    def _report_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "report_goal_status",
                "description": (
                    "提交目标审核结论并结束本次审核。done=目标已达成，结束循环；"
                    "continue=尚未达成，需主执行模型继续工作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["done", "continue"],
                            "description": "done=目标已达成；continue=尚未达成，继续下一轮。",
                        },
                        "message": {
                            "type": "string",
                            "description": (
                                "status=done 时写给用户的完成总结；"
                                "status=continue 时写给主执行模型的下一步指示，需具体可执行。"
                            ),
                        },
                    },
                    "required": ["status", "message"],
                },
            },
        }

    @staticmethod
    def _run_command_tool() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": (
                    "在终端中执行只读指令。可用于查看文件内容、搜索代码/文本、检查目录结构、"
                    "查看 git 状态与差异、运行测试以核实目标是否达成。"
                    "禁止用于写入、删除、改权限或其他修改性操作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "要执行的终端命令字符串。应优先使用只读命令，例如 ls、cat、grep、find、"
                                "rg、git status、git diff、pwd、stat、以及运行测试的命令等。"
                            ),
                        }
                    },
                    "required": ["command"],
                },
            },
        }

    def _build_tools(self, review_mode: str) -> List[Dict[str, Any]]:
        if review_mode == REVIEW_MODE_ACTIVE:
            return [self._run_command_tool(), self._report_tool()]
        return [self._report_tool()]

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
        review_mode: str = REVIEW_MODE_READONLY,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
        cancel_check: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        review_mode = review_mode if review_mode in (REVIEW_MODE_READONLY, REVIEW_MODE_ACTIVE) else REVIEW_MODE_READONLY
        debug_transcript: List[Dict[str, Any]] = []

        def _trace(event: str, payload: Dict[str, Any]) -> None:
            if not DEBUG_SAVE_GOAL_REVIEW_TRANSCRIPT:
                return
            debug_transcript.append({"ts": int(time.time() * 1000), "event": event, "payload": payload})

        def _flush_trace(final_result: Dict[str, Any]) -> None:
            if not DEBUG_SAVE_GOAL_REVIEW_TRANSCRIPT:
                return
            try:
                DEBUG_TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
                row = {
                    "created_at": int(time.time() * 1000),
                    "review_mode": review_mode,
                    "messages": messages,
                    "trace": debug_transcript,
                    "final_result": final_result,
                }
                file = DEBUG_TRANSCRIPT_DIR / f"goal_review_{int(time.time() * 1000)}.json"
                file.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

        def _continue(message: str) -> Dict[str, Any]:
            return {"status": "continue", "message": message or FALLBACK_CONTINUE_MESSAGE, "source": "goal_review_agent"}

        url = str(self.cfg.get("url") or "").strip()
        key = str(self.cfg.get("key") or "").strip()
        model = str(self.cfg.get("model") or "").strip()
        if not url or not key or not model:
            out = _continue("目标审核智能体配置缺失，无法判断完成情况，请继续推进目标。")
            _flush_trace(out)
            return out
        endpoint = f"{url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        timeout_seconds = int(self.cfg.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        max_rounds = max(1, int(self.cfg.get("max_rounds") or DEFAULT_MAX_ROUNDS))
        extra_params = dict(self.cfg.get("extra_params") or {})
        tools = self._build_tools(review_mode)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(review_mode)},
            {"role": "user", "content": payload_text},
        ]

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            rounds = 0
            forced_tool_retry_used = False
            while True:
                rounds += 1
                if rounds > max_rounds:
                    out = _continue(f"目标审核超过 {max_rounds} 轮未产出结论，请继续推进目标。")
                    _flush_trace(out)
                    return out
                if cancel_check:
                    external = cancel_check()
                    if external:
                        return external
                if progress_cb:
                    progress_cb({"stage": "model_call", "round": rounds, "message": f"审核轮次 {rounds}"})
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
                            out = _continue(f"目标审核请求失败({retry_resp.status_code})，请继续推进目标。")
                            _flush_trace(out)
                            return out
                    else:
                        out = _continue(
                            f"目标审核请求失败({exc.response.status_code if exc.response else 'unknown'})，请继续推进目标。"
                        )
                        _flush_trace(out)
                        return out
                except Exception as exc:
                    _trace("request_exception", {"round": rounds, "error": str(exc)})
                    out = _continue("目标审核请求异常，请继续推进目标。")
                    _flush_trace(out)
                    return out

                choice = ((resp.json().get("choices") or [{}])[0] or {}).get("message") or {}
                reasoning_content = (
                    choice.get("reasoning_content") or choice.get("reasoning") or choice.get("thinking") or ""
                )
                _trace("response", {"round": rounds, "message": choice, "reasoning_content": reasoning_content})
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
                    reminder = (
                        "禁止直接输出结论文本。必须调用工具：必要时先调用 run_command 核实，"
                        "再调用 report_goal_status 给出最终结论。"
                    )
                    if not forced_tool_retry_used:
                        forced_tool_retry_used = True
                        reminder = "你刚刚未通过工具给出明确结果。必须调用工具，禁止直接输出内容。请立即调用 report_goal_status 返回最终结论。"
                    messages.append({"role": "user", "content": reminder})
                    continue

                # 有 tool_calls：追加完整 assistant 消息（与主智能体结构对齐）
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
                    if fn == "report_goal_status":
                        status = str(args.get("status") or "").strip().lower()
                        message = str(args.get("message") or "").strip()
                        if status == "done":
                            out = {"status": "done", "message": message or "目标已达成。", "source": "goal_review_agent"}
                            _flush_trace(out)
                            return out
                        if status == "continue":
                            out = _continue(message)
                            _flush_trace(out)
                            return out
                        # 非法 status：保守续命
                        out = _continue(message or "审核返回了无法识别的状态，请继续推进目标。")
                        _flush_trace(out)
                        return out
                    if fn == "run_command":
                        cmd = str(args.get("command") or "").strip()
                        if progress_cb:
                            progress_cb({"stage": "run_command", "round": rounds, "command": cmd})
                        tool_result = await self._run_readonly_command(cmd) if cmd else {"success": False, "error": "command 不能为空"}
                        _trace("tool_result", {"round": rounds, "tool": "run_command", "command": cmd, "result": tool_result})
                        tool_content = json.dumps(tool_result, ensure_ascii=False)
                        messages.append(
                            {
                                "role": "tool",
                                "content": tool_content,
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
                                + f".{int((time.time() % 1) * 1000000):06d}",
                                "message_id": f"msg_{uuid.uuid4().hex}",
                                "tool_call_id": call.get("id"),
                                "name": "run_command",
                            }
                        )
