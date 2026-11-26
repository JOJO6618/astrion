"""Print the real Python `/api/codex/accounts` response for one fixture root.

The Rust contract creates an isolated DATA_DIR-like tree, then runs this script
with it. The Flask blueprint is mounted on a minimal app; authentication
wrappers are satisfied explicitly, while the route itself, CodexAccountStore,
and CodexModelsManager are the production implementations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

from server import auth_helpers, codex_auth  # noqa: E402
from utils.api_client.codex.accounts import CodexAccountStore  # noqa: E402
import utils.api_client.codex.accounts as accounts_module  # noqa: E402
from utils.api_client.codex.models import CodexModelsManager  # noqa: E402
import utils.api_client.codex.models as models_module  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    accounts_dir = root / "codex_accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)

    store = CodexAccountStore(
        legacy_auth_path=str(root / "codex_auth.json"),
        accounts_dir=str(accounts_dir),
        index_path=str(root / "codex_accounts.json"),
    )

    # Never let the legacy account fall back to the real user's ~/.codex cache.
    models_module.CODEX_CLI_MODELS_CACHE = str(root / "missing-cli-models.json")

    def models_manager(account_id=None):
        resolved = store.resolve_account_id(account_id)
        if resolved == "openai-codex":
            cache_path = root / "codex_models.json"
        else:
            cache_path = root / "codex_models" / f"{resolved}.json"
        return CodexModelsManager(
            auth_manager=store.get_auth_manager(resolved),
            account_id=resolved,
            cache_path=str(cache_path),
        )

    codex_auth.get_codex_account_store = lambda: store
    codex_auth.get_models_manager = models_manager
    accounts_module.get_codex_account_store = lambda: store

    # The decorators resolve these names from auth_helpers' globals.
    auth_helpers.is_logged_in = lambda: True
    auth_helpers.is_admin_user = lambda: True

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "codex-accounts-contract"
    app.register_blueprint(codex_auth.codex_auth_bp)
    response = app.test_client().get("/api/codex/accounts")
    if response.status_code != 200:
        raise SystemExit(f"Flask accounts route returned {response.status_code}: {response.data!r}")
    sys.stdout.write(json.dumps(response.get_json(), ensure_ascii=False))


if __name__ == "__main__":
    main()
