"""Simple performance log helper for debugging latency issues."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from config.paths import LOGS_DIR as _LOGS_DIR
    PERF_LOG_PATH = Path(_LOGS_DIR) / "perf_debug.log"
except Exception:
    PERF_LOG_PATH = Path.home() / ".astrion" / "astrion" / "logs" / "perf_debug.log"


def perf_log(label: str, elapsed_ms: Optional[float] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    """Append a timestamped performance entry to the perf log file."""
    try:
        PERF_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now().isoformat()
        parts = [now, label]
        if elapsed_ms is not None:
            parts.append(f"elapsed_ms={elapsed_ms:.2f}")
        if extra:
            parts.append(str(extra))
        line = " | ".join(parts) + "\n"
        with open(PERF_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


class PerfTimer:
    """Context manager / helper to measure and log elapsed time."""

    def __init__(self, label: str, extra: Optional[Dict[str, Any]] = None):
        self.label = label
        self.extra = extra or {}
        self.start: Optional[float] = None

    def __enter__(self) -> "PerfTimer":
        self.start = time.perf_counter()
        perf_log(f"START {self.label}", extra=self.extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        elapsed = (time.perf_counter() - self.start) * 1000 if self.start else 0.0
        perf_log(f"END {self.label}", elapsed_ms=elapsed, extra=self.extra)

    def mark(self, sub_label: str, extra: Optional[Dict[str, Any]] = None) -> None:
        elapsed = (time.perf_counter() - self.start) * 1000 if self.start else 0.0
        merged = {**self.extra, **(extra or {})}
        perf_log(f"MARK {self.label}::{sub_label}", elapsed_ms=elapsed, extra=merged)
