"""Read-only catalog, resource and verbatim-upstream checks for built-in skills."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "agentskills"


def hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def actual_catalog_parser():
    # Exercise the real parser without loading config/.env, logging or a server.
    path = ROOT / "modules/skills_manager.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == "_parse_frontmatter"]
    scope = {"Dict": dict}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)
    return scope["_parse_frontmatter"]


def audit() -> dict:
    failures = []
    skills = []
    parser = actual_catalog_parser()
    for skill_file in sorted(LIBRARY.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        skill = skill_file.parent.name
        try:
            metadata = yaml.safe_load(text.split("---", 2)[1])
            catalog = parser(text)
            if metadata.get("name") != skill:
                failures.append(f"{skill}: name does not match folder")
            if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
                failures.append(f"{skill}: missing description")
            if catalog.get("description") in (None, "", "|", ">", "|-", ">-"):
                failures.append(f"{skill}: actual catalog cannot display description")
        except Exception as exc:
            failures.append(f"{skill}: frontmatter {exc}")
        skills.append(skill)

    syntax_count = 0
    for script in sorted(LIBRARY.rglob("*.py")):
        try:
            ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
            syntax_count += 1
        except Exception as exc:
            failures.append(f"{script.relative_to(ROOT)}: syntax {exc}")

    forbidden = [*LIBRARY.rglob("UPSTREAM_SKILL.md"), *LIBRARY.rglob("astrion-runtime.md")]
    if forbidden:
        failures.append(f"Unexpected wrapper files: {forbidden}")
    for entry in LIBRARY.glob("*/SKILL.md"):
        if "references/astrion-runtime.md" in entry.read_text(encoding="utf-8"):
            failures.append(f"Shared requirements reference: {entry}")

    verbatim = {}
    lock = json.loads((LIBRARY / "UPSTREAM_LOCK.json").read_text())
    for entry in lock["skills"]:
        expected = dict(entry["upstream_files"])
        adaptation = entry.get("adaptation", {})
        expected.update(adaptation.get("modified", {}))
        expected.update(adaptation.get("added", {}))
        for removed in adaptation.get("removed", []):
            expected.pop(removed, None)
        if expected != hashes(LIBRARY / entry["skill"]):
            failures.append(f"{entry['skill']}: local adaptation fingerprints are stale")
    for name, entry in lock.get("local_skills", {}).items():
        if entry["files"] != hashes(LIBRARY / name):
            failures.append(f"{name}: local skill fingerprints are stale")
    for name in ("xlsx", "pdf"):
        expected = next(item["upstream_files"] for item in lock["skills"] if item["skill"] == name)
        actual = hashes(LIBRARY / name)
        added = sorted(actual.keys() - expected.keys())
        missing = sorted(expected.keys() - actual.keys())
        modified = sorted(key for key in actual.keys() & expected.keys() if actual[key] != expected[key])
        verbatim[name] = {"files": len(actual), "added": added, "missing": missing, "modified": modified}
        if added or missing or modified:
            failures.append(f"{name}: differs from pinned upstream")

    # Check the rewritten standalone viewer has real local assets and no inline application code.
    viewer = LIBRARY / "algorithmic-art/templates/viewer.html"
    markup = viewer.read_text()
    viewer_assets = re.findall(r'(?:src|href)="([^"]+)"', markup)
    for relative in viewer_assets:
        if not (viewer.parent / relative).is_file():
            failures.append(f"Viewer missing asset: {relative}")
    if re.search(r"<style\b|\bon(?:click|input|change)=", markup):
        failures.append("Viewer has inline application styles/handlers")

    return {"skills": skills, "skill_count": len(skills), "python_syntax_passed": syntax_count,
            "verbatim": verbatim, "viewer_assets": viewer_assets, "failures": failures}


if __name__ == "__main__":
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--report", type=Path)
    args = arguments.parse_args()
    report = audit()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(bool(report["failures"]))
