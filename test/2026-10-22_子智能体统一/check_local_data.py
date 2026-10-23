"""本机真实数据副本验证，不触碰原记录。报告只输出数量与校验状态。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).parent))
from test_migration import merge


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*.json")}


def main():
    live = Path("/Users/jojo/.astrion/astrion/host")
    source, target = live / "mutiagents/conversations", live / "data/conversations"
    sandbox = Path(__file__).resolve().parents[2] / ".astrion/local_data_subagent_merge"
    sandbox.mkdir(exist_ok=False)
    before_source, before_target = fingerprints(source), fingerprints(target)
    shutil.copytree(source, sandbox / "old")
    shutil.copytree(target, sandbox / "main")
    originals = {str(p.relative_to(source)): json.loads(p.read_text()) for p in source.rglob("conv_*.json")}
    result = merge.migrate(sandbox / "old", sandbox / "main", apply=True, backup_dir=sandbox / "backup")
    for relative, original in originals.items():
        moved = json.loads((sandbox / "main" / relative).read_text())
        assert moved["id"] == original["id"]
        assert moved["messages"] == original["messages"]
        assert moved["todo_list"] == original["todo_list"]
        assert moved.get("token_statistics") == original.get("token_statistics")
        expected = {k: v for k, v in original["metadata"].items() if k not in merge.REMOVED_METADATA}
        assert moved["metadata"] == expected
        assert (sandbox / "backup/conversations" / relative).read_bytes() == (source / relative).read_bytes()
        index = json.loads((sandbox / "main" / relative).with_name("index.json").read_text())
        assert original["id"] in index
    for relative, digest in before_target.items():
        if Path(relative).name != "index.json":
            assert hashlib.sha256((sandbox / "main" / relative).read_bytes()).hexdigest() == digest
    assert fingerprints(source) == before_source
    assert fingerprints(target) == before_target
    report = {"migrated": result["conversation_count"], "workspaces": result["workspace_count"],
              "existing_json_files_checked": len(before_target), "originals_unchanged": True,
              "messages_todos_usage_preserved": True, "backup_bytes_verified": True,
              "remaining_source_conversations": len(list((sandbox / "old").rglob("conv_*.json")))}
    (sandbox / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
