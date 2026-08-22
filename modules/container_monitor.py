"""Collect resource metrics for Docker-based user containers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Dict, Optional, Tuple

SIZE_UNITS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000 ** 2,
    "mib": 1024 ** 2,
    "gb": 1000 ** 3,
    "gib": 1024 ** 3,
    "tb": 1000 ** 4,
    "tib": 1024 ** 4,
}


def _parse_size(value: str) -> Optional[int]:
    if not value:
        return None
    value = value.strip()
    if value in {"", "0"}:
        return 0
    match = re.match(r"([\d\.]+)\s*([a-zA-Z]+)", value)
    if not match:
        try:
            return int(float(value))
        except ValueError:
            return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = SIZE_UNITS.get(unit, 1)
    return int(number * multiplier)


def _parse_pair(value: str) -> Tuple[Optional[int], Optional[int]]:
    if not value:
        return (None, None)
    parts = [part.strip() for part in value.split("/", 1)]
    if len(parts) == 1:
        return _parse_size(parts[0]), None
    return _parse_size(parts[0]), _parse_size(parts[1])


def _parse_percent(value: str) -> Optional[float]:
    if not value:
        return None
    value = value.strip().rstrip("%")
    try:
        return float(value)
    except ValueError:
        return None


def collect_stats(container_name: str, docker_bin: Optional[str] = None) -> Optional[Dict]:
    """Return docker stats metrics for a running container."""
    docker_bin = docker_bin or shutil.which("docker")
    if not docker_bin:
        return None
    cmd = [
        docker_bin,
        "stats",
        container_name,
        "--no-stream",
        "--format",
        "{{json .}}",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw_output = (result.stdout or "").strip()
    if not raw_output:
        return None
    try:
        data = json.loads(raw_output.splitlines()[-1])
    except json.JSONDecodeError:
        return None

    mem_used, mem_limit = _parse_pair(data.get("MemUsage"))
    net_rx, net_tx = _parse_pair(data.get("NetIO"))
    block_read, block_write = _parse_pair(data.get("BlockIO"))

    return {
        "timestamp": time.time(),
        "cpu_percent": _parse_percent(data.get("CPUPerc")),
        "memory": {
            "used_bytes": mem_used,
            "limit_bytes": mem_limit,
            "percent": _parse_percent(data.get("MemPerc")),
            "raw": data.get("MemUsage"),
        },
        "net_io": {
            "rx_bytes": net_rx,
            "tx_bytes": net_tx,
            "raw": data.get("NetIO"),
        },
        "block_io": {
            "read_bytes": block_read,
            "write_bytes": block_write,
            "raw": data.get("BlockIO"),
        },
        "pids": int(data.get("PIDs", 0)) if data.get("PIDs") else None,
        "raw": data,
    }


def inspect_state(container_name: str, docker_bin: Optional[str] = None) -> Optional[Dict]:
    docker_bin = docker_bin or shutil.which("docker")
    if not docker_bin:
        return None
    cmd = [
        docker_bin,
        "inspect",
        "-f",
        "{{json .State}}",
        container_name,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return {
        "status": state.get("Status"),
        "running": state.get("Running"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "pid": state.get("Pid"),
        "error": state.get("Error"),
        "exit_code": state.get("ExitCode"),
    }
