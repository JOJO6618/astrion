"""Paths and identity of a registered workspace."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class UserWorkspace:
    username: str
    root: Path
    project_path: Path
    data_dir: Path
    logs_dir: Path
    uploads_dir: Path
    quarantine_dir: Path
    workspace_id: str = "default"
    label: str = ""
