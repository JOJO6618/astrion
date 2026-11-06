"""Build a reviewer-only history from immutable, producer-labelled records."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from modules.approval_context_text import (
    attachment_text, metadata, parse_arguments, result_text, status_text,
    text_content, tool_payload, user_text, value_text,
)

USER_WINDOW = 5
TOOL_WINDOW = 5


@dataclass(frozen=True)
class ReviewEntry:
    order: int
    kind: str
    body: str
    key: str


class ApprovalHistory:
    def __init__(self, history: list[dict], *, trigger_id: str = "", pending: list | None = None):
        self.messages = deepcopy(history)
        self.entries: list[ReviewEntry] = []
        self.trigger: ReviewEntry | None = None
        self.plans: list[ReviewEntry] = []
        calls: dict[str, list[dict]] = {}
        for index, message in enumerate(self.messages):
            if not isinstance(message, dict):
                continue
            identity = str(message.get("message_id") or f"position:{index}")
            if message.get("role") == "assistant":
                calls = {}
                for call in message.get("tool_calls") or []:
                    if isinstance(call, dict) and call.get("id"):
                        calls.setdefault(str(call["id"]), []).append(call)
                continue
            if message.get("role") == "user":
                original = user_text(message)
                if original is not None:
                    body = original
                    attachments = attachment_text(message)
                    if attachments:
                        body += "\n附件：\n" + attachments
                    entry = ReviewEntry(index, "用户", body, identity)
                    self.entries.append(entry)
                else:
                    entry = ReviewEntry(index, "触发通知（非用户授权）", text_content(message.get("content")), identity)
                if trigger_id and identity == trigger_id:
                    self.trigger = entry
                continue
            if message.get("role") != "tool":
                continue
            candidates = calls.get(str(message.get("tool_call_id") or "")) or []
            if not candidates:
                continue
            if len(candidates) != 1:
                details = []
                for candidate in candidates:
                    fn = candidate.get("function") or {}
                    details.append(str(fn.get("name") or "") + "\n" + value_text(parse_arguments(fn.get("arguments"))))
                body = "调用标识重复，参数与结果配对不明确，不可作为用户授权。\n"
                body += "\n候选调用：\n" + "\n".join(details) + "\n结果：\n" + result_text(message)
                self.entries.append(ReviewEntry(index, "工具", body, identity))
                continue
            function = candidates[0].get("function") or {}
            name = str(function.get("name") or "")
            # A response cannot impersonate another tool's verified interaction.
            if message.get("name") and message["name"] != name:
                continue
            arguments = parse_arguments(function.get("arguments"))
            payload = tool_payload(message) or {}
            verified_name = message.get("name") == name
            if (verified_name and name == "ask_user" and payload.get("status") == "answered"
                    and payload.get("success") is True and payload.get("answer_text")
                    and payload.get("answer_status", "answered") == "answered"):
                body = "问题：" + str(arguments.get("question") or "")
                if arguments.get("context"):
                    body += "\n背景：" + str(arguments["context"])
                if arguments.get("options"):
                    body += "\n选项：\n" + value_text(arguments["options"])
                body += "\n回复：" + str(payload.get("answer_text") or payload.get("message") or "")
                if "answer_status" not in payload:
                    body = "旧问答记录未保存实际回答状态，不能单独证明用户已回答或授权。\n" + body
                self.entries.append(ReviewEntry(index, "用户问答", body, identity))
                continue
            if (verified_name and name == "submit_plan" and payload.get("status") == "approved"
                    and payload.get("success") is True):
                plan = str(payload.get("plan_content") or "")
                body = "计划：" + str(payload.get("plan_file") or arguments.get("plan_file") or "")
                body += "\n批准内容：\n" + (plan or "原批准正文未保存；不能将现有文件视为当时批准的版本。")
                if payload.get("comment"):
                    body += "\n用户意见：" + str(payload["comment"])
                self.plans.append(ReviewEntry(index, "计划批准", body, identity))
                continue
            body = name + "\n" + value_text(arguments)
            body += "\n状态：" + status_text(message) + "\n结果：\n" + result_text(message)
            self.entries.append(ReviewEntry(index, "工具", body, identity))
        for offset, item in enumerate(pending or []):
            row = item if isinstance(item, dict) else {"text": str(item)}
            if str(row.get("source") or "guidance") not in {"guidance", "user", "presend"}:
                continue
            body = str(row.get("text") or "")
            attachments = attachment_text(row)
            if attachments:
                body += "\n附件：\n" + attachments
            self.entries.append(ReviewEntry(
                len(self.messages) + offset, "用户引导（尚未交主模型）", body,
                "pending:" + str(row.get("id") or offset),
            ))
        self.entries.sort(key=lambda entry: entry.order)
        self.trigger_missing = bool(trigger_id) and self.trigger is None
        if self.trigger is None and not trigger_id:
            # Older records may lack a task trigger ID. Never substitute a notice.
            self.trigger = next((row for row in reversed(self.entries) if row.kind == "用户"), None)

    def initial_text(self) -> str:
        users = [row for row in self.entries if row.kind != "工具"][-USER_WINDOW:]
        tools = [row for row in self.entries if row.kind == "工具"][-TOOL_WINDOW:]
        fixed = list(self.plans)
        if self.trigger is not None:
            fixed.append(self.trigger)
        selected = {row.key: row for row in [*users, *tools, *fixed]}
        rows = sorted(selected.values(), key=lambda row: row.order)
        lines = ["历史："]
        if self.trigger_missing:
            lines.append("本次工作触发原文未找到，不能用末条用户消息冒充。")
        if self.trigger is not None:
            # Refer to the existing ordinal rather than repeat the triggering text.
            ordinal = next(i for i, row in enumerate(rows, 1) if row.key == self.trigger.key)
            lines.append(f"本次工作由第{ordinal}项触发。")
        for index, row in enumerate(rows, 1):
            lines.append(self.render_entry(index, row))
        omitted = sum(row.key not in selected for row in self.entries)
        if omitted:
            lines.append(f"更早的{omitted}项未在首批显示，可用read_review_history补查。")
        if not rows:
            lines.append("（没有可用历史）")
        return "\n".join(lines)

    @staticmethod
    def render_entry(index: int, row: ReviewEntry) -> str:
        return f"{index} {row.kind}\n" + "\n".join("  " + line for line in row.body.split("\n"))

    def read(self, *, offset: int = 0, count: int = 10, query: str = "") -> str:
        """Read older immutable evidence, newest first; no filesystem identities exposed."""
        if offset < 0 or not 1 <= count <= 20:
            return "参数错误：offset不能为负，count须为1至20。"
        rows = [*self.entries, *self.plans]
        if self.trigger and all(row.key != self.trigger.key for row in rows):
            rows.append(self.trigger)
        rows.sort(key=lambda row: row.order, reverse=True)
        if query:
            rows = [row for row in rows if query.casefold() in row.body.casefold()]
        page = rows[offset:offset + count]
        lines = [f"历史补查：共{len(rows)}项，从{offset + 1}项起（由近及远）。"]
        lines.extend(self.render_entry(offset + index, row) for index, row in enumerate(page, 1))
        if offset + len(page) < len(rows):
            lines.append(f"下一页offset={offset + len(page)}")
        return "\n".join(lines)
