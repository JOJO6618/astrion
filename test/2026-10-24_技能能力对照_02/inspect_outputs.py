"""Reuse the first round's non-visual inspection with isolated round-two paths."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREVIOUS = ROOT.parent / "2026-10-24_技能能力对照" / "inspect_outputs.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true",
                        help="Only use after both implementation rounds have ended.")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("previous_trial_inspector", PREVIOUS)
    inspector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inspector)
    inspector.ROOT = ROOT
    inspector.TRIALS = {"ui-copper": ("ui", "impeccable"), "ui-silver": ("ui", None)}
    inspector.main()
    if args.freeze:
        output = ROOT / "frozen_results.json"
        if output.exists():
            raise SystemExit("Freeze already exists; refusing to overwrite it")
        report = {"scope": "Trial deliverables; .astrion runtime caches excluded explicitly.", "trials": {}}
        for name in inspector.TRIALS:
            folder = ROOT / "runs" / name
            report["trials"][name] = {
                str(path.relative_to(folder)): {
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                }
                for path in sorted(folder.rglob("*"))
                if path.is_file() and ".astrion" not in path.relative_to(folder).parts
                and "__pycache__" not in path.relative_to(folder).parts
            }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("Frozen deliverable files:", sum(len(files) for files in report["trials"].values()))


if __name__ == "__main__":
    main()
