"""Re-run existing trial checks without changing the frozen trial directories."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
CHECKS = [
    ("art-north", "tools/check-core.cjs"),
    ("art-north", "tools/check-page.cjs"),
    ("art-south", "tests/check-painter.cjs"),
    ("art-south", "tests/check-html.cjs"),
    ("art-south", "tests/check-ui.cjs"),
    ("ui-east", "checks/verify.cjs"),
    ("ui-east", "checks/dom-smoke.cjs"),
    ("ui-west", "tests/verify-core.cjs"),
    ("ui-west", "tests/verify-static.py"),
]


def frozen_changes() -> dict:
    frozen = json.loads((ROOT / "frozen_results.json").read_text())
    changes = {}
    for name, record in frozen.items():
        folder = ROOT / "runs" / name
        current = {
            str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob("*") if path.is_file()
        }
        original = {key: value["sha256"] for key, value in record["files"].items()}
        added = current.keys() - original.keys()
        runtime_added = {key for key in added if key.startswith(".astrion/sub_agent_runtime/")}
        changes[name] = {
            "added": sorted(added - runtime_added),
            "runtime_cache_added": sorted(runtime_added),
            "removed": sorted(original.keys() - current.keys()),
            "modified": sorted(key for key in current.keys() & original.keys()
                               if current[key] != original[key]),
        }
    return changes


def artifacts_changed(changes: dict) -> bool:
    return any(record[key] for record in changes.values()
               for key in ("added", "removed", "modified"))


def run_check(item: tuple[str, str]) -> dict:
    name, relative = item
    folder = ROOT / "runs" / name
    runtime = ["/opt/homebrew/bin/python3.11", "-B"] if relative.endswith(".py") else ["node"]
    command = runtime + [str(folder / relative)]
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=folder, capture_output=True,
                                text=True, timeout=60)
        record = {"trial": name, "script": relative, "exit_code": result.returncode,
                  "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired as exc:
        record = {"trial": name, "script": relative, "exit_code": None,
                  "error": "60 second verification timeout",
                  "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or "")}
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    return record


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in ("script", "img", "source") and values.get("src"):
            self.paths.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet":
            self.paths.append(values["href"])


def check_http() -> list[dict]:
    base = "http://127.0.0.1:8766/"
    pages = ["compare/index.html"] + [f"runs/{name}/index.html" for name in
             ("art-north", "art-south", "ui-east", "ui-west")]
    urls = {urljoin(base, path) for path in pages}
    records = []
    for path in pages:
        parser = Assets()
        parser.feed((ROOT / path).read_text())
        urls.update(urljoin(urljoin(base, path), asset) for asset in parser.paths)
    for url in sorted(urls):
        try:
            with urlopen(url, timeout=5) as response:
                records.append({"url": url, "status": response.status,
                                "bytes": len(response.read())})
        except Exception as exc:
            records.append({"url": url, "error": str(exc)})
    return records


def main():
    before = frozen_changes()
    if artifacts_changed(before):
        raise SystemExit("Frozen artifacts changed before verification: " + json.dumps(before))
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(run_check, CHECKS))
    report = {
        "scope": "Re-runs of agent-authored tests, HTTP retrieval and SHA256 comparison; no browser or visual acceptance.",
        "before": before, "checks": checks, "http": check_http(),
        "after": frozen_changes(),
    }
    output = ROOT / "verification"
    output.mkdir(exist_ok=True)
    (output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for check in checks:
        print(check["trial"], check["script"], "exit", check["exit_code"])
    print("HTTP:", len(report["http"]), "resources; failures:",
          sum(item.get("status") != 200 for item in report["http"]))
    print("Frozen artifacts unchanged:", not artifacts_changed(report["after"]))
    print("New runtime cache files (separately recorded):",
          sum(len(record["runtime_cache_added"]) for record in report["after"].values()))


if __name__ == "__main__":
    main()
