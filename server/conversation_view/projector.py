"""Incremental, IO-free projection of one task's ordered runtime event stream.

Only stdlib imports: loading this file never imports server.app/config. Indexed
 events must arrive in increasing order; a scalar watermark ignores redelivery.
No event log/window is retained. Snapshot callers own a deep copy of UI state.
"""
from copy import deepcopy
from functools import cmp_to_key
import re


_SHOW_HTML = re.compile(r"<show_html\b[\s\S]*?</show_html>", re.I)
_TERMINAL = {"task_complete", "task_stopped", "error", "quota_exceeded"}
_RESPONSE = {"thinking_start", "thinking_chunk", "text_start", "text_chunk",
             "tool_preparing", "tool_start"}


class RuntimeProjection:
    """Project ``{idx, type, data, ts}`` events without external state or IO."""

    def __init__(self, task_id, conversation_id, model_key=None):
        self.task_id = task_id
        self.conversation_id = conversation_id
        self.model_key = model_key
        self.messages = []
        self.state = {
            "api_request_pending": False, "goal_progress": None,
            "goal_running": False, "summary_tool_batch_id": None,
            "summary_tool_batch_sequence": 0, "streaming": False,
            "pending_tool_approvals": [], "resolved_tool_approval_ids": [],
            "pending_user_questions": [], "pending_plan_approvals": [],
            "approval_review_records": [],
        }
        self._assistant = None
        self._next_idx = 0
        self._watermark = -1
        self._idx = 0
        self._timestamp = None
        self._action_timestamp = None
        self._attempt = 0
        self._action_attempts = {}
        self._tools = {}
        self._split_html = set()

    def snapshot(self):
        """Return a detached frontend view, never references to mutable state."""
        return deepcopy({"messages": self.messages, "state": self.state})

    def _id(self, kind):
        return f"{self.task_id}:{self._idx}:{kind}"

    def _new_assistant(self, data=None):
        data = data or {}
        msg = {
            "id": self._id("assistant"), "role": "assistant", "actions": [],
            "streamingThinking": "", "streamingText": "",
            "currentStreamingType": None, "activeThinkingId": None,
            "awaitingFirstContent": True, "generatingLabel": "",
            "timestamp": self._timestamp, "taskId": self.task_id,
            "streamAttemptStart": 0,
            "streamAttemptBatchId": self.state["summary_tool_batch_id"] or "",
        }
        if "metadata" in data:
            msg["metadata"] = deepcopy(data["metadata"])
        self.messages.append(msg)
        self._assistant = msg
        self.state["streaming"] = True
        return msg

    def _ensure_assistant(self):
        return self._assistant if self._assistant is not None else self._new_assistant()

    def _action(self, kind, **fields):
        msg = self._ensure_assistant()
        msg["awaitingFirstContent"] = False
        msg["generatingLabel"] = ""
        action = {"id": self._id(kind), "type": kind,
                  "timestamp": self._action_timestamp, **fields}
        msg["actions"].append(action)
        self._action_attempts[action["id"]] = self._attempt
        return action

    @staticmethod
    def _clear_message(msg):
        msg.update(streamingThinking="", streamingText="",
                   currentStreamingType=None, activeThinkingId=None,
                   awaitingFirstContent=False, generatingLabel="")
        for action in msg["actions"]:
            if action["type"] in {"text", "thinking"}:
                action["streaming"] = False

    def _close_streams(self):
        for msg in self.messages:
            if msg["role"] == "assistant":
                self._clear_message(msg)
        self._split_html.clear()
        self.state["streaming"] = False
        self.state["api_request_pending"] = False

    def _user(self, data):
        # Content equality is not identity: repeated prompts stay separate.
        msg = deepcopy(data)
        metadata = deepcopy(data.get("metadata", {}))
        media_metadata = metadata if isinstance(metadata, dict) else {}
        message_id = data.get("message_id")
        msg.update(id=message_id if message_id is not None else self._id("user"),
                   role="user", content=data.get("message", data.get("content", "")),
                   metadata=metadata)
        for field in ("images", "videos", "media_refs"):
            fallback = data.get("mediaRefs", []) if field == "media_refs" else []
            msg[field] = deepcopy(data.get(field, media_metadata.get(field, fallback)))
        msg.setdefault("timestamp", self._timestamp)
        msg.setdefault("created_at", self._timestamp)
        previous = next((item for item in self.messages
                         if item.get("role") == "user" and message_id
                         and item.get("message_id") == message_id), None)
        if previous is not None:
            previous.update(msg)
            return
        self._close_streams()
        self.messages.append(msg)
        self._assistant = None

    def _system(self, data):
        content = str(data.get("content", data.get("message", "")) or "").strip()
        sub_agent = re.match(r"^(?:✅\s*)?(?:子智能体|Sub-agent)\s*#?\s*(\d+)\s*(?:任务摘要|task summary)[:：]", content)
        background = re.match(r"^\[(?:后台\s*run_command\s*完成|Background\s*run_command\s*finished)\]", content)
        if not background and not (sub_agent and re.search(r"已完成|Completed\.", content)):
            return
        if self._assistant is not None and not self._assistant["actions"]:
            self.messages.remove(self._assistant)
        self._close_streams()
        self._new_assistant()
        self._action("system", content=content, variant="sub_agent_done")
        self._clear_message(self._assistant)

    def _active_thinking(self, msg):
        active_id = msg["activeThinkingId"]
        return next((a for a in reversed(msg["actions"])
                     if a["id"] == active_id), None) if active_id else None

    def _start_content(self, kind):
        msg = self._ensure_assistant()
        action = self._active_thinking(msg) if kind == "thinking" else (
            msg["actions"][-1] if msg["actions"] else None)
        if action and action["type"] == kind and action.get("streaming"):
            return action
        field = "streamingThinking" if kind == "thinking" else "streamingText"
        msg[field] = ""
        msg["currentStreamingType"] = kind
        action = self._action(kind, content="", streaming=True)
        if kind == "thinking":
            action.update(blockId=action["id"], modelKey=self.model_key)
            msg["activeThinkingId"] = action["id"]
        else:
            self._split_html.discard(msg["id"])
        self.state["streaming"] = True
        return action

    def _content_event(self, event_type, data):
        kind, phase = event_type.split("_", 1)
        msg = self._ensure_assistant()
        if phase == "start":
            self._start_content(kind)
            return
        if kind == "thinking":
            action = self._active_thinking(msg)
        else:
            action = msg["actions"][-1] if msg["actions"] else None
            if not action or action["type"] != "text" or not action.get("streaming"):
                action = None
        if phase == "chunk":
            content = data.get("content", "")
            if not isinstance(content, str) or not content:
                return
            if action is None:
                if kind == "text" and msg["id"] in self._split_html:
                    action = self._action("text", content="", streaming=True, continuation=True)
                    msg["currentStreamingType"] = "text"
                else:
                    action = self._start_content(kind)
            field = "streamingThinking" if kind == "thinking" else "streamingText"
            msg[field] += content
            action["content"] += content
            self.state["streaming"] = True
            # Only a chunk containing '>' can complete an HTML closing tag.
            if kind == "text" and ">" in content and _SHOW_HTML.search(action["content"]):
                action.update(streaming=False, frozenByShowHtml=True)
                self._split_html.add(msg["id"])
            return
        if kind == "thinking":
            if action is None and data.get("full_content"):
                action = self._start_content(kind)
            if action:
                if isinstance(data.get("full_content"), str):
                    action["content"] = data["full_content"]
                action["streaming"] = False
                action["collapsed"] = True
            msg["streamingThinking"] = ""
            msg["activeThinkingId"] = None
        else:
            split = msg["id"] in self._split_html
            if action:
                action["streaming"] = False
                if not split and data.get("full_content"):
                    action["content"] = data["full_content"]
            elif not split and isinstance(data.get("full_content"), str):
                previous = next((a for a in reversed(msg["actions"])
                                 if a["type"] == "text"), None)
                if previous:
                    previous["content"] = data["full_content"]
                elif data["full_content"]:
                    self._action("text", content=data["full_content"], streaming=False)
            msg["streamingText"] = ""
            self._split_html.discard(msg["id"])
        msg["currentStreamingType"] = None

    def _reset(self, data):
        removed = {key for key, attempt in self._action_attempts.items()
                   if attempt == self._attempt}
        for msg in self.messages:
            if msg["role"] != "assistant":
                continue
            msg["actions"][:] = [a for a in msg["actions"] if a["id"] not in removed]
        self._action_attempts = {k: v for k, v in self._action_attempts.items() if k not in removed}
        self._tools = {k: a for k, a in self._tools.items() if a["id"] not in removed}
        if self._assistant is not None:
            msg = self._assistant
            self._clear_message(msg)
            msg["streamAttemptStart"] = len(msg["actions"])
            msg["streamAttemptBatchId"] = self.state["summary_tool_batch_id"] or ""
            msg["awaitingFirstContent"] = not msg["actions"]
            if msg["awaitingFirstContent"]:
                msg["generatingLabel"] = data.get("message", "")
        self._split_html.clear()
        self.state["streaming"] = True
        self.state["api_request_pending"] = False
        self._attempt += 1

    @staticmethod
    def _aliases(data):
        return [str(data[k]) for k in ("id", "preparing_id", "execution_id", "tool_call_id")
                if data.get(k) is not None and data[k] != ""]

    @staticmethod
    def _label(args):
        if not isinstance(args, dict):
            return ""
        for key in ("command", "path", "target_path", "query", "question", "name"):
            if args.get(key):
                value = str(args[key])
                return value.rsplit("/", 1)[-1] if key in {"path", "target_path"} else (
                    f'"{value}"' if key == "query" else value)
        return ""  # Locale-dependent duration labels are derived in the UI.

    def _tool_event(self, event_type, data):
        aliases = self._aliases(data)
        action = next((self._tools[k] for k in aliases if k in self._tools), None)
        if action is None:
            if event_type not in {"tool_preparing", "tool_start"}:
                return
            tool = {"id": data.get("id"), "name": data.get("name", ""),
                    "arguments": {}, "argumentSnapshot": None, "argumentLabel": "",
                    "status": "preparing", "result": None, "message": "",
                    "intent_full": "", "intent_rendered": "", "intent_complete": False}
            action = self._action("tool", tool=tool,
                                  toolBatchId=self.state["summary_tool_batch_id"] or self._id("batch"))
        for key in aliases:
            self._tools[key] = action
        tool = action["tool"]
        preparing_id = data.get("preparing_id")
        if preparing_id is None and event_type == "tool_preparing":
            preparing_id = data.get("id")
        if preparing_id is not None:
            tool["preparingId"] = preparing_id
        if event_type == "tool_preparing":
            if tool["status"] != "preparing":
                return
            tool["name"] = data.get("name", tool["name"])
        elif event_type == "tool_start":
            tool.update(status="running", id=data.get("id", tool["id"]),
                        executionId=data.get("execution_id", data.get("id")),
                        name=data.get("name", tool["name"]), message=None,
                        arguments=deepcopy(data.get("arguments", {})), intent_complete=True)
            if isinstance(tool["arguments"], dict) and isinstance(tool["arguments"].get("intent"), str):
                tool["intent_full"] = tool["intent_rendered"] = tool["arguments"]["intent"]
        if "intent" in data and data["intent"] is not None:
            tool["intent_full"] = tool["intent_rendered"] = deepcopy(data["intent"])
            if isinstance(tool["arguments"], dict):
                tool["arguments"]["intent"] = deepcopy(data["intent"])
        tool["intent_complete"] = tool["intent_complete"] or data.get("intent_complete") is True
        if event_type in {"tool_update_action", "update_action"}:
            for key, value in data.items():
                if key not in {"id", "preparing_id", "execution_id", "tool_call_id",
                               "conversation_id", "task_id", "task_type", "workspace_id"}:
                    tool[key] = deepcopy(value)
        for key in ("preparing_id", "monitor_snapshot", "monitor_snapshot_after"):
            if key in data:
                tool[key] = deepcopy(data[key])
        if event_type != "tool_preparing":
            tool["argumentSnapshot"] = deepcopy(tool["arguments"])
            tool["argumentLabel"] = self._label(tool["arguments"])

    def _pending_event(self, event_type, data):
        questions = event_type.startswith("user_question")
        plan = event_type.startswith("plan_approval")
        key = "pending_user_questions" if questions else (
            "pending_plan_approvals" if plan else "pending_tool_approvals")
        id_key = "question_id" if questions else "approval_id"
        pending = self.state[key]
        if event_type.endswith("required"):
            rows = data.get("questions", [data.get("question")]) if questions else [data.get("approval")]
            for row in rows or []:
                if not isinstance(row, dict) or not row.get(id_key):
                    continue
                if not questions and not plan and row[id_key] in self.state["resolved_tool_approval_ids"]:
                    continue
                previous = next((r for r in pending if r[id_key] == row[id_key]), None)
                if previous is not None:
                    previous.update(deepcopy(row))
                else:
                    pending.append(deepcopy(row))
            if questions:
                def compare(a, b):
                    field = "created_at" if (a.get("batch_id") and b.get("batch_id")
                            and a["batch_id"] != b["batch_id"]) else "batch_index"
                    av, bv = a.get(field) or 0, b.get(field) or 0
                    return (av > bv) - (av < bv)
                pending.sort(key=cmp_to_key(compare))
            elif plan:
                pending.sort(key=lambda r: r.get("created_at", 0))
        else:
            ids = data.get("question_ids", [data.get(id_key)]) if questions else [data.get(id_key)]
            ids = {str(i) for i in ids or [] if i is not None}
            if not questions and not plan:
                if data.get("decision") not in {"approved", "rejected", "expired", "cancelled", "timeout"}:
                    return
                for row in pending:
                    if str(row[id_key]) in ids:
                        records = self.state["approval_review_records"]
                        previous = next((r for r in records if r.get("approval_id") == row[id_key]), {})
                        records[:] = [r for r in records if r.get("approval_id") != row[id_key]]
                        records.append({**previous,
                            "id": previous.get("id", f"auto-{row[id_key]}"), "kind": "auto",
                            "approval_id": row[id_key], "tool_name": row.get("tool_name"),
                            "progress": previous.get("progress", row.get("auto_review_progress", [])),
                            "final_decision": data.get("decision"), "reason": data.get("reason", ""),
                            "approval": {**deepcopy(row), "status": data.get("decision")}})
                        records[:] = records[-30:]
                resolved = self.state["resolved_tool_approval_ids"]
                for value in ids:
                    if value not in resolved:
                        resolved.append(value)
            pending[:] = [row for row in pending if str(row[id_key]) not in ids]

    def _review(self, event_type, data):
        kind = "auto" if event_type == "auto_approval_progress" else "goal"
        pending = self.state["pending_tool_approvals"]
        approval_id = data.get("approval_id")
        if kind == "auto" and not approval_id and pending:
            approval_id = pending[0]["approval_id"]
        if kind == "auto" and approval_id in self.state["resolved_tool_approval_ids"]:
            return
        progress = deepcopy(data.get("progress", data))
        if not isinstance(progress, dict):
            return
        records = self.state["approval_review_records"]
        record = next((r for r in reversed(records) if r.get("kind") == kind
                       and r.get("approval_id") == approval_id), None)
        if record is None or (progress.get("stage") == "start" and not approval_id):
            record = {"id": self._id(kind), "kind": kind, "approval_id": approval_id, "progress": []}
            records.append(record)
        record["progress"].append(progress)
        record["progress"] = record["progress"][-100:]
        if progress.get("decision") in {"approved", "rejected"}:
            record["decision"] = progress["decision"]
        records[:] = records[-30:]
        if kind == "auto":
            for approval in pending:
                if approval["approval_id"] != approval_id:
                    continue
                status = progress.get("decision", progress.get("stage"))
                previous = approval.get("auto_review_status")
                if status not in {"approved", "rejected"}:
                    status = previous if previous in {"approved", "rejected"} else (
                        "pending" if status == "pending" else "reviewing")
                approval.update(auto_review_required=True, auto_review_status=status)
                approval["auto_review_progress"] = [*approval.get("auto_review_progress", []), deepcopy(progress)][-100:]

    def _goal(self, event_type, data):
        if event_type == "goal_stopped" and str(data.get("stopped_reason", "")).lower() == "user_cancel":
            self.state.update(goal_running=False, goal_progress=None)
            return
        status = {"goal_completed": "done", "goal_stopped": "stopped"}.get(
            event_type, str(data.get("status") or "running").lower())
        progress = deepcopy(data)
        progress["status"] = status
        if event_type == "goal_progress":
            previous = self.state["goal_progress"] or {}
            progress["goal"] = data.get("goal") or previous.get("goal", "")
            for key in ("turn_count", "tokens_used", "tool_calls", "duration_seconds"):
                progress.setdefault(key, 0)
        self.state.update(goal_progress=progress, goal_running=status == "running")

    def apply(self, event):
        """Consume one ordered event; duplicate indexed delivery is a no-op."""
        if not isinstance(event, dict):
            return
        data = deepcopy(event.get("data") or {})
        if not isinstance(data, dict):
            return
        if data.get("task_id") and data["task_id"] != self.task_id:
            return
        if data.get("conversation_id") and self.conversation_id and data["conversation_id"] != self.conversation_id:
            return
        idx = event.get("idx", self._next_idx)
        if not isinstance(idx, int) or idx <= self._watermark:
            return
        self._idx = self._watermark = idx
        self._next_idx = idx + 1
        self._timestamp = next((value for value in (
            data.get("timestamp"), data.get("created_at"), data.get("createdAt"),
            event.get("timestamp"), event.get("ts")) if value is not None), None)
        # Frontend action timestamps use Date.now() milliseconds. Event ts is
        # server epoch seconds; message ISO timestamps remain unchanged.
        action_ts = event.get("ts", event.get("timestamp", self._timestamp))
        self._action_timestamp = (action_ts * 1000 if isinstance(action_ts, (int, float))
                                  and abs(action_ts) < 100_000_000_000 else action_ts)
        event_type = event.get("type", "")
        if event_type == "error" and (data.get("retry") or data.get("error_type") == "parameter_format_error"):
            self.state["api_request_pending"] = False
            return
        if event_type in _RESPONSE or event_type in _TERMINAL:
            self.state["api_request_pending"] = False
        if event_type == "user_message":
            self._user(data)
        elif event_type == "ai_message_start":
            self._close_streams()
            self._attempt += 1
            self._new_assistant(data)
        elif event_type == "api_request_start":
            self._attempt += 1
            sequence = self.state["summary_tool_batch_sequence"] + 1
            self.state.update(api_request_pending=True, streaming=True,
                              summary_tool_batch_sequence=sequence,
                              summary_tool_batch_id=f"request-{sequence}")
            if self._assistant is not None:
                self._assistant["streamAttemptStart"] = len(self._assistant["actions"])
                self._assistant["streamAttemptBatchId"] = f"request-{sequence}"
        elif event_type in {"thinking_start", "thinking_chunk", "thinking_end",
                            "text_start", "text_chunk", "text_end"}:
            self._content_event(event_type, data)
        elif event_type == "stream_reset":
            self._reset(data)
        elif event_type in {"tool_preparing", "tool_start", "tool_intent", "tool_update_action", "update_action"}:
            self._tool_event(event_type, data)
        elif event_type in {"tool_approval_required", "tool_approval_resolved",
                            "user_question_required", "user_questions_required",
                            "user_question_resolved", "user_questions_resolved",
                            "plan_approval_required", "plan_approval_resolved"}:
            self._pending_event(event_type, data)
        elif event_type in {"auto_approval_progress", "goal_review_progress"}:
            self._review(event_type, data)
        elif event_type in {"goal_progress", "goal_completed", "goal_stopped"}:
            self._goal(event_type, data)
        elif event_type == "system_message":
            self._system(data)
        elif event_type in _TERMINAL:
            if event_type == "task_complete" and self._assistant is not None:
                if isinstance(data.get("citations"), list):
                    self._assistant.setdefault("metadata", {})["citations"] = deepcopy(data["citations"])
            self._close_streams()
            background = any(data.get(key) for key in (
                "has_running_sub_agents", "has_running_background_commands", "has_running_multi_agent"))
            if event_type in {"error", "quota_exceeded"} or not background:
                for message in self.messages:
                    for action in message.get("actions", []):
                        tool = action.get("tool")
                        if tool and tool.get("status") in {"preparing", "running", "pending", "queued", "stale", "awaiting_user_answer"}:
                            tool.update(status="cancelled", message=tool.get("message") or "")
