"""Inspect trial deliverables without browser automation or modifying outputs."""
from __future__ import annotations

import hashlib
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
TRIALS = {
    "art-north": ("art", "algorithmic-art"),
    "art-south": ("art", None),
    "ui-east": ("ui", None),
    "ui-west": ("ui", "impeccable"),
}


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.inline_scripts = 0
        self.stylesheets = []
        self.scripts = []
        self.native_controls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            if values.get("src"):
                self.scripts.append(values["src"])
                self.assets.append(values["src"])
            else:
                self.inline_scripts += 1
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href", ""))
            self.assets.append(values.get("href", ""))
        if tag in ("img", "source") and values.get("src"):
            self.assets.append(values["src"])
        if tag == "select" or (tag == "input" and values.get("type") in ("range", "color", "checkbox", "radio")):
            self.native_controls.append({"tag": tag, "type": values.get("type"), "id": values.get("id")})


def inspect_trial(name, group, skill):
    folder = ROOT / "runs" / name
    required = ["index.html", "trial.json", "design.md" if group == "art" else "review.md"]
    missing = [name for name in required if not (folder / name).is_file()]
    result = {"required_missing": missing, "assets_missing": [], "external_assets": [],
              "syntax": [], "read_condition": "not established", "limitations": []}
    if (folder / "index.html").is_file():
        parser = Assets()
        parser.feed((folder / "index.html").read_text(encoding="utf-8"))
        result["external_stylesheet_count"] = len(parser.stylesheets)
        result["external_script_count"] = len(parser.scripts)
        result["inline_script_count"] = parser.inline_scripts
        result["native_controls_to_inspect"] = parser.native_controls
        for reference in parser.assets:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                if parsed.scheme not in ("data", "blob"):
                    result["external_assets"].append(reference)
                continue
            target = ROOT / unquote(parsed.path.lstrip("/")) if parsed.path.startswith("/") else folder / unquote(parsed.path)
            if not target.is_file():
                result["assets_missing"].append(reference)
    for script in sorted(folder.rglob("*.js")):
        if any(part in ("vendor", "node_modules") for part in script.relative_to(folder).parts):
            continue
        check = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True, timeout=30)
        result["syntax"].append({"file": str(script.relative_to(folder)), "exit": check.returncode,
                                 "detail": check.stderr.strip() if check.returncode else "passed"})
    if (folder / "trial.json").is_file():
        try:
            log = json.loads((folder / "trial.json").read_text(encoding="utf-8"))
            result["reported_skill_files"] = log.get("skill_files_read", [])
            result["reported_checks"] = log.get("checks", [])
            result["limitations"] = log.get("limitations", [])
            reads = result["reported_skill_files"]
            if skill is None:
                result["read_condition"] = "self-reported compliant" if reads == [] else "noncompliant report"
            else:
                root = (ROOT.parents[1] / "agentskills" / skill).resolve()
                paths = [Path(item).resolve() for item in reads]
                expected = root / "SKILL.md"
                result["read_condition"] = "self-reported compliant" if expected in paths and all(path.is_relative_to(root) for path in paths) else "incomplete or noncompliant report"
        except Exception as exc:
            result["trial_json_error"] = str(exc)
    result["files"] = {str(path.relative_to(folder)): {"bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                       for path in sorted(folder.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}
    return result


def main():
    original = json.loads((ROOT / "input_fingerprints.json").read_text())
    changed = [name for name, digest in original.items()
               if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    report = {"common_inputs_changed": changed,
              "trials": {name: inspect_trial(name, *condition) for name, condition in TRIALS.items()},
              "scope": "Non-visual static checks; read conditions are self-reported, not a complete tool transcript audit."}
    (ROOT / "inspection_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, trial in report["trials"].items():
        print(name, "required missing:", trial["required_missing"], "missing assets:", trial["assets_missing"],
              "syntax failures:", sum(item["exit"] != 0 for item in trial["syntax"]),
              "reading:", trial["read_condition"])
    print("Common inputs changed:", changed)


if __name__ == "__main__":
    main()
