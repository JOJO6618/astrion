"""Optional local snapshot sink. All configuration, probes and IO run off task locks."""
from __future__ import annotations

from collections import deque
import json
import logging
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Callable

from modules.crosspet.state import EVENTS, SessionState, character_for_model, session_key
from modules.crosspet.settings import SETTINGS_LOCK, control_path, read_enabled
from utils.atomic_io import atomic_write_json

LOGGER = logging.getLogger(__name__)


def integration_path() -> Path:
    override = os.environ.get("ASTRION_CROSSPET_CONFIG")
    if override:
        return Path(override).expanduser()
    support = os.environ.get("CROSSPET_DATA_DIR")
    if not support:
        support = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "CrossPet") if os.name == "nt" else str(Path.home() / "Library/Application Support/CrossPet")
    return Path(support) / "astrion.json"


def default_state_dir(platform: str | None = None) -> Path:
    import sys
    if (platform or sys.platform) == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
        return Path(local) / "CrossPet/state"
    return Path("/tmp/crosspet")


def read_destination(config_path: Path) -> Path | None:
    """CrossPet's optional config supplies a directory, never the export switch."""
    override = os.environ.get("CROSSPET_STATE_DIR")
    if override:
        root = Path(override).expanduser()
    elif config_path.is_symlink():
        return None
    elif config_path.exists():
        if not config_path.is_file() or config_path.stat().st_size > 8192:
            return None
        value = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        root = Path(str(value.get("state_dir") or default_state_dir())).expanduser()
    else:
        root = default_state_dir()
    return root / "astrion-state.json" if root.is_absolute() else None


class CrossPetObserver:
    def __init__(self, *, config_path: Path | None = None, settings_path: Path | None = None,
                 model_resolver: Callable = str, quota_source=None):
        self.config_path = config_path or integration_path()
        self.settings_path = settings_path or control_path()
        self.model_resolver = model_resolver
        self.quota_source = quota_source
        self.quota: dict = {}
        self.instance = uuid.uuid4().hex
        self.sessions: dict[str, SessionState] = {}
        self.bindings: dict[str, tuple[str, str, Callable]] = {}
        self.foreground: dict = {}
        self.foreground_loader: Callable | None = None
        self.queue = deque(maxlen=2048)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.destination: Path | None = None
        self.sequence = 0
        self.last_write = 0.0
        self.last_error = 0.0
        self.last_signature = None

    def ensure_started(self) -> None:
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="crosspet-state")
                self.thread.start()
        self.wake.set()

    def enqueue(self, item: tuple) -> None:
        with self.lock:
            self.queue.append(item)
        self.ensure_started()

    def event(self, identity: tuple[str, str, str], task_id: str, model_key: str, kind: str, data: dict) -> None:
        if kind not in EVENTS:
            return
        # Only metadata reaches the worker. Never enqueue arguments, results or message text.
        safe = {key: data[key] for key in ("name", "id", "status", "retry", "in_progress")
                if key in data and isinstance(data[key], (str, bool, int, float))}
        if kind == "task_complete":
            safe["error"] = bool(data.get("error"))
        self.enqueue(("event", session_key(*identity), task_id, model_key, kind, safe, time.time()))

    def bind(self, identity: tuple[str, str, str], task_id: str, model_key: str, probe: Callable) -> None:
        self.enqueue(("bind", session_key(*identity), task_id, model_key, probe))

    def focus(self, identity: tuple[str, str, str] | None, model_key: str | Callable = "") -> None:
        self.enqueue(("focus", session_key(*identity) if identity else "", model_key))

    def _model(self, key: str) -> str:
        try:
            return str(self.model_resolver(key) or key)[:160]
        except Exception:
            return str(key)[:160]

    def drain(self, now: float, *, probe_activity: bool = True) -> None:
        with self.lock:
            pending = list(self.queue)
            self.queue.clear()
        for item in pending:
            if item[0] == "bind":
                _, sid, task, model, probe = item
                self.bindings[sid] = (task, model, probe)
                continue
            if item[0] == "focus":
                _, sid, model = item
                self.foreground_loader = model if callable(model) else None
                self.foreground = {"session": sid, "model": "" if callable(model) else self._model(model), "ts": now}
                continue
            _, sid, task, model, kind, data, stamp = item
            # Compression may replace the conversation ID while the same task runs.
            old_sid = next((key for key, value in self.bindings.items()
                            if value[0] == task and key != sid), None)
            if old_sid is not None:
                previous_binding = self.bindings.pop(old_sid)
                self.bindings.setdefault(sid, previous_binding)
                if old_sid in self.sessions:
                    self.sessions[sid] = self.sessions.pop(old_sid)
                if self.foreground.get("session") == old_sid:
                    self.foreground["session"] = sid
            binding = self.bindings.get(sid)
            if binding and binding[0] != task:
                continue  # An old task cannot overwrite its successor's state.
            state = self.sessions.setdefault(sid, SessionState())
            state.apply(kind, data, task_id=task, model=self._model(model), now=stamp)
        if probe_activity and self.foreground_loader is not None:
            try:
                self.foreground["model"] = self._model(self.foreground_loader())
                self.foreground_loader = None
            except Exception:
                pass  # Retry on the worker; navigation remains independent.
        for sid, (task, model, probe) in (list(self.bindings.items()) if probe_activity else []):
            state = self.sessions.setdefault(sid, SessionState(model=self._model(model), task_id=task))
            if state.task_id == task and model:
                state.model = self._model(model)
            try:
                status, background = probe()
            except Exception:
                continue  # Unknown is not idle; keep the last confirmed state.
            if state.task_id != task:
                continue
            if background is not None:
                state.activity(background, now)
            if status in {"succeeded", "failed", "stopped", "canceled"} and state.finished != status:
                state.finish(status, now)
        for sid, state in list(self.sessions.items()):
            if state.finished and not state.background and now - state.ts > 60:
                self.sessions.pop(sid, None)
                self.bindings.pop(sid, None)
        if len(self.sessions) > 256:
            for sid, state in sorted(self.sessions.items(), key=lambda item: item[1].ts):
                if state.finished and not state.background:
                    self.sessions.pop(sid, None)
                    self.bindings.pop(sid, None)
                    if len(self.sessions) <= 256:
                        break

    def snapshot(self, now: float) -> dict:
        focus = dict(self.foreground)
        focused = self.sessions.get(focus.get("session"))
        if focused and (not focused.finished or focused.background or not focus.get("model")):
            focus["model"] = focused.model
        focus["character"] = character_for_model(str(focus.get("model") or ""))
        focus["ts"] = now
        selected = sorted(self.sessions.items(), key=lambda pair: (
            pair[0] == focus.get("session"),
            not pair[1].finished or pair[1].background,
            bool(pair[1].waits), pair[1].ts), reverse=True)[:256]
        return {"version": 1, "source": "astrion", "instance": self.instance,
                "sequence": self.sequence, "ts": now, "foreground": focus,
                "sessions": {sid: state.payload(now) for sid, state in selected},
                "quota": {"gpt": self.quota} if self.quota_source is not None else {}}

    def tick(self, now: float | None = None) -> None:
        # Saving the switch and writing a snapshot share this lock: once disabling
        # returns, an already-running worker cannot write using the old preference.
        with SETTINGS_LOCK:
            self._tick(time.time() if now is None else now)

    def _tick(self, now: float) -> None:
        enabled = read_enabled(self.settings_path)
        destination = read_destination(self.config_path) if enabled else None
        self.drain(now, probe_activity=destination is not None)
        if self.destination != destination:
            self.last_signature = None
        self.destination = destination
        if self.quota_source is not None:
            busy = any(not state.finished or state.background for state in self.sessions.values())
            self.quota = self.quota_source.snapshot(destination is not None, busy)
        if not destination:
            return
        signature = (tuple(sorted((sid, s.task_id, s.model, s.revision, s.background)
                                  for sid, s in self.sessions.items())),
                     tuple(sorted(self.foreground.items())),
                     json.dumps(self.quota, sort_keys=True))
        if signature == self.last_signature and now - self.last_write < 10:
            return
        self._check_targets(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.sequence += 1
        atomic_write_json(destination, self.snapshot(now))
        # Existing native foreground detection reads this small host record.
        atomic_write_json(destination.parent / "astrion-host.json", self.snapshot(now)["foreground"])
        (destination.parent / "seen-astrion").write_text(str(int(now)), encoding="utf-8")
        self.last_write = now
        self.last_signature = signature

    @staticmethod
    def _check_targets(destination: Path) -> None:
        targets = (destination, destination.parent / "astrion-host.json",
                   destination.parent / "seen-astrion")
        if destination.parent.is_symlink() or any(path.is_symlink() for path in targets):
            raise ValueError("CrossPet destination must not be a symbolic link")

    def _run(self) -> None:
        while True:
            self.wake.wait(2)
            self.wake.clear()
            # Coalesce short bursts; the task thread never waits for this worker.
            time.sleep(0.15)
            try:
                self.tick()
            except Exception as exc:
                now = time.time()
                if now - self.last_error >= 60:
                    LOGGER.warning("CrossPet state export unavailable (%s)", type(exc).__name__)
                    self.last_error = now
