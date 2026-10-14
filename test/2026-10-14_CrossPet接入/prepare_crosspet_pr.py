"""Package only CrossPet changes, then apply them to a fresh ZIP baseline copy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "_experiments/crosspet-upstream"
SOURCE = ROOT / "_experiments/crosspet"
OUT = ROOT / "_experiments/crosspet-pr"
SHA = "cf0d37e21a5697c9fc39b2dcf525e5ab67f1d16b"
EXCLUDED = {".git", ".astrion", "__pycache__", "node_modules", "build", ".DS_Store"}


def files(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*")
            if path.is_file() and not any(part in EXCLUDED for part in path.relative_to(root).parts)
            and path.suffix != ".pyc"}


def run(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, check=False)


def main() -> None:
    old, new = files(BASE), files(SOURCE)
    changed = sorted(name for name in old.keys() | new.keys()
                     if name not in old or name not in new or old[name].read_bytes() != new[name].read_bytes())
    if not changed:
        raise RuntimeError("No CrossPet changes found")
    OUT.mkdir(exist_ok=True)
    patches = []
    for name in changed:
        before = "crosspet-upstream/" + name if name in old else "/dev/null"
        after = "crosspet/" + name if name in new else "/dev/null"
        result = run(["git", "-c", "core.quotePath=false", "diff", "--no-index", "--binary", "--no-renames",
                      "--", before, after], cwd=ROOT / "_experiments")
        if result.returncode != 1:
            raise RuntimeError(result.stderr.decode() or f"Unexpected diff result for {name}")
        lines = result.stdout.decode().splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.startswith(("diff --git ", "--- ", "+++ ")):
                for prefix in ("a/crosspet-upstream/", "a/crosspet/", "b/crosspet-upstream/", "b/crosspet/"):
                    line = line.replace(prefix, prefix[0] + "/")
                lines[index] = line
        patches.append("".join(lines))
    patch = OUT / "crosspet-astrion.patch"
    patch.write_text("".join(patches), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="apply-check-", dir=OUT) as temporary:
        target = Path(temporary)
        for name, source in old.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        init = run(["git", "init", "--quiet"], cwd=target)
        if init.returncode:
            raise RuntimeError(init.stderr.decode())
        for args in (["git", "apply", "--check", "--whitespace=error-all", str(patch)],
                     ["git", "apply", "--whitespace=error-all", str(patch)]):
            applied = run(args, cwd=target)
            if applied.returncode:
                raise RuntimeError(applied.stderr.decode())
        actual = files(target)
        if actual.keys() != new.keys():
            raise AssertionError("Applied tree file list differs from the modified source")
        for name, source in new.items():
            if actual[name].read_bytes() != source.read_bytes():
                raise AssertionError(f"Applied content differs: {name}")
    archive = OUT / "crosspet-astrion-changed-files.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in changed:
            if name in new:
                bundle.write(new[name], name)
    action_text = {"added": "新增", "deleted": "删除", "modified": "修改"}
    rows = []
    for name in changed:
        action = "added" if name not in old else "deleted" if name not in new else "modified"
        rows.append(f"| {action_text[action]} | `{name}` |")
    (OUT / "CHANGED_FILES.md").write_text(
        "# CrossPet Astrion 接入改动\n\n"
        f"基线：CrossPet 1.3.4（ZIP），commit `{SHA}`。\n\n"
        f"变更文件：{len(changed)} 个；已排除构建缓存、字节码与未改动素材。\n\n"
        "| 变更 | 仓库相对路径 |\n|---|---|\n" + "\n".join(rows) + "\n", encoding="utf-8")
    (OUT / "manifest.json").write_text(json.dumps({
        "baseline_commit": SHA, "baseline_version": "1.3.4", "changed_files": changed,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "patch_check": "passed", "applied_tree_content_match": "passed",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PASS git apply --check and complete applied-tree content match ({len(changed)} changed files)")
    print(f"Patch: {patch.relative_to(ROOT)} ({patch.stat().st_size} bytes)")
    print(f"Archive: {archive.relative_to(ROOT)} ({archive.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
