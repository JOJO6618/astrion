"""Generate and read synthetic Python data for the Rust launch-entry tests."""
import json
import os
import secrets
import sys
from pathlib import Path

operation, root, workspace, *conversation_ids = sys.argv[1:]
root = Path(root).resolve()
workspace = str(Path(workspace).resolve())
repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repo))
os.environ.update(
    ASTRION_IGNORE_DOTENV="1",
    HOME=str(root),
    USERPROFILE=str(root),
    ASTRION_DATA_ROOT=str(root),
    DATA_DIR=str(root / "host/data"),
    LOGS_DIR=str(root / "host/logs"),
    DEPLOY_CONFIG_DIR=str(root / "config"),
    HOST_WORKSPACES_FILE=str(root / "config/host_workspaces.json"),
    TERMINAL_SANDBOX_MODE="host",
)

workspace_id = "python-workspace"
data = root / "host/data"
registry = root / "config/host_workspaces.json"
if operation == "seed":
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({
        "version": 1,
        "default_workspace_id": workspace_id,
        "workspaces": [{"workspace_id": workspace_id, "label": "Python fixture", "path": workspace}],
    }), encoding="utf-8")

from utils.conversation_manager import ConversationManager

manager = ConversationManager(base_dir=str(data), project_path=workspace, read_only=operation == "read")
assert manager.current_workspace_id == workspace_id
if operation == "seed":
    conversation_id = manager.create_conversation(
        workspace,
        initial_messages=[
            {"role": "user", "content": "From Python", "message_id": "python-user"},
            {"role": "assistant", "content": "Preserved Python response", "message_id": "python-assistant"},
        ],
        metadata_overrides={"frozen_system_prompt": "Persisted Python Agent system prompt for the offline fixture."},
    )
    token = secrets.token_hex(32)
    with os.fdopen(os.open(data / "host_api_token", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="ascii") as file:
        file.write(token + "\n")
    print(json.dumps({"id": conversation_id, "path": str(manager.conversations_dir / f"{conversation_id}.json")}))
elif operation == "read":
    document = manager.read_conversation(conversation_ids[0])
    assert document is not None
    print(json.dumps(document))
else:
    raise ValueError(f"Unknown operation {operation}")
