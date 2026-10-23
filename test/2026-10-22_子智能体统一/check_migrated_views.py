"""Exercise the production single-store reader on the migrated local data copy."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from utils.conversation_manager import ConversationManager

sandbox = root / ".astrion/local_data_subagent_merge"
backup = sandbox / "backup/conversations"
manager = ConversationManager(str(sandbox), read_only=True)
checked = 0
workspaces = set()
for directory in sorted({path.parent.relative_to(backup) for path in backup.rglob("conv_*.json")}):
    manager.conversations_dir = sandbox / "main" / directory
    manager.index_file = manager.conversations_dir / "index.json"
    listed = manager.get_conversation_list(limit=10000)["conversations"]
    identifiers = {item["id"] for item in listed}
    for original in (backup / directory).glob("conv_*.json"):
        expected = json.loads(original.read_text())
        loaded = manager.read_conversation(expected["id"])
        assert loaded is not None, original.name
        assert loaded["messages"] == expected["messages"], original.name
        assert loaded["id"] in identifiers, original.name
        assert "multi_agent_mode" not in loaded["metadata"]
        checked += 1
    workspaces.add(str(directory))
report = {"ordinary_store_loaded": checked, "workspaces": len(workspaces), "listed_and_history_preserved": True}
(sandbox / "view_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
