"""Run the production Python review formatter without Flask or Host configuration."""

import ast
import json
from pathlib import Path
import runpy
import sys
from typing import List


ROOT = Path(__file__).resolve().parents[2]
source = ROOT / "server" / "utils_common.py"
module = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
functions = {"build_review_lines", "_sanitize_filename_component"}
selected = [
    node for node in module.body
    if isinstance(node, ast.FunctionDef) and node.name in functions
]
if {node.name for node in selected} != functions:
    raise RuntimeError("Could not find both Python review helpers")
scope = {"json": json, "List": List}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), scope)
messages = runpy.run_path(str(ROOT / "modules" / "i18n_messages" / "api_conversation.py"))["MESSAGES"]

fixture = json.load(sys.stdin)


def mode_of(raw):
    mode = str(raw or "dialogue").strip().lower()
    return mode if mode in {"dialogue", "full"} else "dialogue"


def limit_of(raw):
    try:
        return int(raw) or 20
    except (ValueError, TypeError):
        return 20


def preview(case):
    lines = scope["build_review_lines"](
        fixture["messages"], limit_of(case.get("limit")), mode_of(case.get("mode")),
    )
    return {"preview": lines, "count": len(lines)}


def export(requested_mode):
    mode = mode_of(requested_mode)
    lines = scope["build_review_lines"](fixture["messages"], content_mode=mode)
    content = "\n".join(lines) + "\n"
    annotation = messages[f"conversation.review_annotation_{mode}"][fixture["locale"]]
    return {"mode": mode, "markdown": f"> {annotation}\n\n{content}", "char_count": len(content)}


print(json.dumps({
    "previews": [preview(case) for case in fixture["previews"]],
    "exports": [export(mode) for mode in fixture["exports"]],
    "safe_title": scope["_sanitize_filename_component"](fixture["title"]),
    "blocked_error": messages["conversation.review_blocked_by_admin"][fixture["locale"]],
}, ensure_ascii=False))
