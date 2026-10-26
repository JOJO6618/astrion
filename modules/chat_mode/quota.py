"""Chat uses the same account tracker as Agent; neither provisions workspaces."""
from pathlib import Path
from modules.account_usage import account_tracker


def tracker_for(root: Path, role: str):
    return account_tracker(Path(root).resolve().parent, role)
