"""Generate a deterministic fixture with the actual Python workflow managers."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def generate(data_dir: Path) -> dict:
    os.environ["ASTRION_IGNORE_DOTENV"] = "1"
    os.environ["ASTRION_DATA_ROOT"] = str(data_dir)
    from modules.workflow_manager import (
        BUILTIN_WORKFLOWS_DIR,
        load_workflow,
        list_workflows,
        workflow_to_markdown,
    )
    from modules.workflow_state_manager import WorkflowStateManager

    definitions = {
        path.parent.name: load_workflow(path.parent.name, data_dir)
        for path in sorted(BUILTIN_WORKFLOWS_DIR.glob("*/WORKFLOW.md"))
    }
    workflow = definitions["research-report"].copy()
    workflow["updatedAt"] = "2026-11-13 16:30"
    markdown = workflow_to_markdown(workflow)
    manager = WorkflowStateManager(data_dir, "conv_fixture")
    with patch("modules.workflow_state_manager.time.time", return_value=1794587460.0):
        manager.activate(
            workflow_name=workflow["name"],
            definition_markdown=markdown,
            entry_node_id="collect",
            stage_start_msg_index=7,
        )
        manager.increment_stage_rounds()
        manager.record_review(node_id="first-review", name="初稿审核", decision="reject", message="补充出处")
        manager.increment_reject("first-review")
        manager.advance_to("analyze", summary="资料已核对", rounds=1, msg_index=9)
        manager.increment_stage_rounds()
        manager.push_notice(notice_type="workflow", message="保留 Python 队列中的通知")
    return {
        "definitions": definitions,
        "list": {"workflows": list_workflows(data_dir)},
        "state_markdown": markdown,
        "state": manager.state,
        "snapshot": manager.progress_snapshot(),
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="astrion-workflow-fixture-") as directory:
        fixture = generate(Path(directory))
    sys.stdout.write(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n")
