"""Auxiliary reasoning summaries; no writes to model messages or context."""
from __future__ import annotations

import asyncio
import json
import sys
import time
import unicodedata
from pathlib import Path
from uuid import uuid4

from .storage import save_summary

PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "thinking_summary.txt"


def log_failure(stage: str) -> None:
    from config import LOGS_DIR
    from utils.log_rotation import append_line
    try:
        error = sys.exc_info()[1]
        append_line(Path(LOGS_DIR) / "thinking_summaries.log", json.dumps({
            "time": time.time(), "stage": stage,
            "error_class": type(error).__name__ if error else None,
        }))
    except Exception:
        pass


def begin_work(terminal, original_input, inherited_anchor: str = "") -> None:
    previous = getattr(terminal, "_thinking_summary_work", None)
    if inherited_anchor and previous:
        return
    if previous:
        previous.cancel()
    if isinstance(original_input, dict):
        source = original_input
        if (source.get("metadata") or {}).get("starts_work") is False:
            history = getattr(terminal.context_manager, "conversation_history", [])
            source = next((item for item in reversed(history)
                           if item.get("role") == "user"
                           and (item.get("metadata") or {}).get("starts_work") is True), source)
        original_input = source.get("content", "")
    terminal._thinking_summary_work = SummaryWork(terminal, original_input)


async def finish_work(terminal) -> None:
    work = getattr(terminal, "_thinking_summary_work", None)
    if work:
        await work.finish()


async def dispose_work(terminal) -> None:
    work = getattr(terminal, "_thinking_summary_work", None)
    if work:
        work.cancel()
        tasks = [block.task for block in work.blocks if block.task and not block.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        terminal._thinking_summary_work = None


class SummaryWork:
    def __init__(self, terminal, original_input):
        self.terminal = terminal
        if isinstance(original_input, list):
            original_input = "\n".join(
                item.get("text", "") for item in original_input if isinstance(item, dict)
            )
        self.original_input = str(original_input or "")
        self.published: list[tuple[SummaryBlock, str]] = []
        self.blocks: list[SummaryBlock] = []
        self.profile = None
        self.interval_seconds = 20
        try:
            from modules.personalization_manager import load_personalization_config
            from modules.aux_model_resolver import resolve_aux_model_profile
            preferences = getattr(terminal, "preferences_dir", None) or terminal.data_dir
            config = load_personalization_config(preferences)
            self.interval_seconds = config["thinking_summary_interval_seconds"]
            model = str(config.get("thinking_summary_model") or "").strip()
            if config.get("thinking_summary_enabled", False) and model:
                self.profile = resolve_aux_model_profile(model)
        except Exception:
            log_failure("configure_model")

    @property
    def summaries(self):
        return [text for block, text in self.published if not block.cancelled]

    def block(self, response_id, conversation_id, sender):
        if not self.profile or getattr(self.terminal.api_client, "api_protocol", None) == "responses":
            return None
        try:
            manager = self.terminal.context_manager._get_conversation_manager_for_id(conversation_id)
            block = SummaryBlock(self, manager, conversation_id, response_id, sender)
            self.blocks.append(block)
            return block
        except Exception:
            log_failure("prepare_block")
            return None

    def cancel(self):
        for block in self.blocks:
            block.cancel()

    async def finish(self):
        for block in self.blocks:
            block.end()
        tasks = [block.task for block in self.blocks if block.task]
        if tasks:
            # Requests are bounded individually. No model/tool streaming waits here.
            await asyncio.gather(*tasks, return_exceptions=True)


class SummaryBlock:
    def __init__(self, work, manager, conversation_id, response_id, sender):
        self.work = work
        self.manager = manager
        self.conversation_id = conversation_id
        self.response_id = response_id
        self.sender = sender
        self.generation = uuid4().hex
        self.text = ""
        self.offset = 0
        self.revision = 0
        self.entries = []
        self.started_at = None
        self.last_request = None
        self.ended = False
        self.cancelled = False
        self.changed = asyncio.Event()
        self.task = None

    def feed(self, text):
        if self.cancelled:
            return
        self.text += text
        if self.started_at is None:
            self.started_at = asyncio.get_running_loop().time()
            self.task = asyncio.create_task(self.run())
        self.changed.set()

    def end(self):
        self.ended = True
        self.changed.set()

    def cancel(self):
        self.cancelled = True
        if self.task and not self.task.done() and not self.task.get_loop().is_closed():
            self.task.cancel()

    def clear_saved(self):
        if self.entries:
            try:
                save_summary(self.manager, self.conversation_id, self.response_id, {
                    "text": "", "revision": 0, "generation": "", "summaries": [],
                })
            except Exception:
                log_failure("clear_retry")

    def entry(self, text):
        return {"text": text, "revision": self.revision, "generation": self.generation,
                "summaries": list(self.entries)}

    async def run(self):
        try:
            loop = asyncio.get_running_loop()
            while not self.cancelled:
                self.changed.clear()
                threshold = 200 if self.last_request is None else 500
                interval = 3 if self.last_request is None else self.work.interval_seconds
                remaining = len(self.text) - self.offset
                if remaining < threshold:
                    if self.ended:
                        return
                    await self.changed.wait()
                    continue
                due = (self.started_at if self.last_request is None else self.last_request) + interval
                delay = due - loop.time()
                # The final incremental window can be summarized immediately;
                # first summary still obeys the agreed 3-second minimum.
                if delay > 0 and not (self.ended and self.last_request is not None):
                    try:
                        await asyncio.wait_for(self.changed.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    continue
                end = len(self.text)
                # Consume the entire pending window, but send only its most recent
                # 2000 characters. Older skipped text is never queued for replay.
                fragment = self.text[max(self.offset, end - 2000):end]
                self.last_request = loop.time()
                self.offset = end
                try:
                    summary = await asyncio.wait_for(self.generate(fragment), timeout=30)
                    if self.cancelled or not summary:
                        continue
                    self.revision += 1
                    self.entries.append(summary)
                    entry = self.entry(summary)
                    # Save every published revision before emitting its display event.
                    # Small atomic display file; keep cancellation and retry ordering
                    # on this loop rather than leaving a detached IO writer behind.
                    save_summary(self.manager, self.conversation_id, self.response_id, entry)
                    self.work.published.append((self, summary))
                    self.sender("thinking_summary", {
                        "response_message_id": self.response_id,
                        "generation": self.generation,
                        "summary": summary, "revision": self.revision,
                    })
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log_failure("generate_or_publish")
        except asyncio.CancelledError:
            return
        except Exception:
            log_failure("worker")

    async def generate(self, fragment):
        from utils.api_client import APIClient
        from modules.external_session import resolve_conversation_headers
        client = APIClient(thinking_mode=False, web_mode=True)
        terminal = self.work.terminal
        preferences = getattr(terminal, "preferences_dir", None) or terminal.data_dir
        client.extra_headers_resolver = lambda base_url: resolve_conversation_headers(
            base_url, self.conversation_id, manager=self.manager, base_dir=preferences
        )
        client.apply_profile(self.work.profile)
        previous = "\n".join(self.work.summaries) or "无"
        messages = [
            {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
            {"role": "user", "content": (
                "请为如下内容生成思考摘要\n\n"
                f"<用户原始输入>\n{self.work.original_input}\n</用户原始输入>\n\n"
                f"<此前摘要>\n{previous}\n</此前摘要>\n\n"
                f"<当前片段原文>\n{fragment}\n</当前片段原文>"
            )},
        ]
        from .billing import request_with_cost
        content = await request_with_cost(
            client, messages, terminal=terminal, conversation_id=self.conversation_id,
            model_key=self.work.profile["name"],
        )
        # Enforce a single plain phrase without cutting English to 15 characters.
        raw = " ".join(content.strip().split())
        clean = "".join(char for char in raw if not unicodedata.category(char).startswith(("P", "S"))).strip()
        if not clean:
            return None
        if any("\u4e00" <= char <= "\u9fff" for char in clean):
            return clean if len(clean) <= 15 else None
        # Other scripts keep their language's natural segmentation; Latin output
        # follows the agreed English word budget.
        if clean.isascii() and not 2 <= len(clean.split()) <= 4:
            return None
        return clean
