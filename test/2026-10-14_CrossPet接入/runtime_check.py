"""Real TaskManager -> bridge -> file -> CrossPet projection, entirely offline."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
    base = Path(directory)
    os.environ.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(base / "runtime"),
                      TERMINAL_SANDBOX_MODE="host", AGENT_API_DUMP_ENABLED="0")
    from server.tasks.models import TaskManager, TaskRecord
    from server.runtime.context import RuntimeContext, TrustedPrincipal, TaskParams
    from modules.crosspet.observer import CrossPetObserver
    from server import crosspet

    class Manual(CrossPetObserver):
        def enqueue(self, item):
            self.queue.append(item)

    config = base / "astrion.json"  # Deliberately absent: no CrossPet-side setup.
    output = base / "state"
    os.environ["CROSSPET_STATE_DIR"] = str(output)
    from modules.crosspet.settings import save_enabled, control_path
    save_enabled(control_path(), True)
    observer = Manual(config_path=config, model_resolver=lambda key: "gpt-5")
    manager = TaskManager()
    principal = TrustedPrincipal(username="test-user", workspace_id="test-workspace", role="host")
    ctx = RuntimeContext(principal=principal, params=TaskParams(conversation_id="conv_test", model_key="fake/model"))
    rec = TaskRecord("test-task", ctx, "conv_test")
    rec.status = "running"
    rec.display_valid = False
    manager._tasks[rec.task_id] = rec

    with patch.object(crosspet, "OBSERVER", observer):
        crosspet.bind_task(manager, rec)
        manager._append_event(rec, "tool_start", {"id": "tool-1", "name": "write_file", "arguments": "secret"})
        manager._append_event(rec, "user_questions_required", {"questions": [{"text": "private"}]})
        observer.tick()
        snapshot = json.loads((output / "astrion-state.json").read_text())
        assert next(iter(snapshot["sessions"].values()))["pose"] == "asking"
        assert "secret" not in json.dumps(snapshot) and "private" not in json.dumps(snapshot)
        assert [event["idx"] for event in rec.events] == [0, 1]
        manager._append_event(rec, "user_questions_resolved", {})
        observer.tick()
        assert next(iter(observer.sessions.values())).pose == "thinking"
        manager.finish_display_task(rec, "succeeded")
        crosspet.finish_task(rec)
        observer.tick()
        snapshot = json.loads((output / "astrion-state.json").read_text())
        assert next(iter(snapshot["sessions"].values()))["pose"] == "happy"
        assert rec.next_event_idx == 3

    # Consume the exact backend output with CrossPet's independently maintained reader.
    reader = ROOT / "_experiments/crosspet/app/web/astrion.js"
    code = "const fs=require('fs');const p=require(process.argv[1]);const s=JSON.parse(fs.readFileSync(process.argv[2]));const g=p.project(s,['gpt'],'gpt',s.ts);if(Object.values(g.gpt)[0].pose!=='happy')process.exit(1);console.log('PASS real TaskManager -> CrossPet snapshot -> JS projection')"
    subprocess.run(["node", "-e", code, str(reader), str(output / "astrion-state.json")], check=True)
