from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional


class UserQuestionManager:
    """In-memory manager for blocking model-to-user questions."""

    def __init__(self):
        self._items: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_options(options: Any) -> List[Dict[str, str]]:
        if not isinstance(options, list):
            return []
        normalized: List[Dict[str, str]] = []
        seen = set()
        for idx, raw in enumerate(options[:8], start=1):
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label") or "").strip()
            if not label:
                continue
            option_id = str(raw.get("id") or "").strip() or f"option_{idx}"
            base = option_id
            suffix = 2
            while option_id in seen:
                option_id = f"{base}_{suffix}"
                suffix += 1
            seen.add(option_id)
            item = {
                "id": option_id,
                "label": label[:120],
            }
            desc = str(raw.get("description") or "").strip()
            if desc:
                item["description"] = desc[:500]
            normalized.append(item)
        return normalized

    def create_question(
        self,
        *,
        username: str,
        conversation_id: Optional[str],
        task_id: Optional[str],
        tool_call_id: Optional[str],
        question: str,
        context: Optional[str] = None,
        options: Any = None,
        batch_id: Optional[str] = None,
        batch_index: int = 0,
        batch_total: int = 1,
    ) -> Dict[str, Any]:
        question_id = f"question_{uuid.uuid4().hex}"
        text = str(question or "").strip()
        item = {
            "question_id": question_id,
            "batch_id": batch_id,
            "batch_index": int(batch_index),
            "batch_total": int(batch_total),
            "username": username,
            "conversation_id": conversation_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "question": text,
            "context": str(context or "").strip(),
            "options": self._normalize_options(options),
            "status": "pending",
            "created_at": time.time(),
            "answered_at": None,
            "answer": None,
        }
        with self._lock:
            self._items[question_id] = item
        return dict(item)

    def get(self, question_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self._items.get(question_id)
            return dict(item) if item else None

    def list_pending(self, username: str, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            rows = []
            for item in self._items.values():
                if item.get("username") != username:
                    continue
                if item.get("status") != "pending":
                    continue
                if conversation_id and item.get("conversation_id") != conversation_id:
                    continue
                rows.append(dict(item))
            rows.sort(key=lambda x: (x.get("created_at", 0.0), x.get("batch_index", 0)))
            return rows

    def answer(
        self,
        *,
        question_id: str,
        username: str,
        selected_option_id: Optional[str] = None,
        text: Optional[str] = None,
        dismissed: bool = False,
    ) -> Dict[str, Any]:
        clean_option_id = str(selected_option_id or "").strip()
        clean_text = str(text or "").strip()
        if not dismissed and not clean_option_id and not clean_text:
            raise ValueError("回答不能为空")
        with self._lock:
            item = self._items.get(question_id)
            if not item:
                raise KeyError("问题不存在")
            if item.get("username") != username:
                raise PermissionError("无权限回答该问题")
            if item.get("status") != "pending":
                return dict(item)

            # 用户主动选择不回答：标记为 dismissed，工具结果会提示模型改为在对话中提问
            if dismissed:
                item["status"] = "answered"
                item["answered_at"] = time.time()
                item["answer"] = {"type": "dismissed", "text": ""}
                return dict(item)

            selected_option = None
            if clean_option_id:
                for opt in item.get("options") or []:
                    if str(opt.get("id") or "") == clean_option_id:
                        selected_option = dict(opt)
                        break
                if selected_option is None:
                    raise ValueError("选项不存在")

            answer_type = "option_with_text" if selected_option and clean_text else "option" if selected_option else "free_text"
            answer = {
                "type": answer_type,
                "text": clean_text,
            }
            if selected_option:
                answer["selected_option_id"] = selected_option.get("id")
                answer["selected_option_label"] = selected_option.get("label")
                if selected_option.get("description"):
                    answer["selected_option_description"] = selected_option.get("description")
                if not clean_text:
                    answer["text"] = selected_option.get("label") or ""

            item["status"] = "answered"
            item["answered_at"] = time.time()
            item["answer"] = answer
            return dict(item)


def format_user_question_answer(item: Dict[str, Any]) -> str:
    """Return the compact, model-facing result text for an answered question."""
    answer = item.get("answer") if isinstance(item, dict) else None
    if not isinstance(answer, dict):
        return "用户未回答。"
    if answer.get("type") == "dismissed":
        return "用户没有回答或不想回答这个问题，请直接输出内容向用户提问"
    lines: List[str] = []
    label = str(answer.get("selected_option_label") or "").strip()
    text = str(answer.get("text") or "").strip()
    if label:
        lines.append(f"用户选择：{label}")
        if text and text != label:
            lines.append(f"用户补充：{text}")
    elif text:
        lines.append(f"用户回答：{text}")
    return "\n".join(lines).strip() or "用户未回答。"
