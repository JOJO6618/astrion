"""Isolated Python model discovery, directory, and request-budget regression."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask
import httpx


TEST_DIR = Path(__file__).resolve().parent
FIXTURE = json.loads((TEST_DIR / "catalog_model_limits.json").read_text(encoding="utf-8"))
FIELDS = ("context_window", "default_context_window", "max_context_window", "max_input_tokens", "max_output_tokens")


class CatalogModelLimitsTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="catalog_limits_", dir=TEST_DIR)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.dict(os.environ, {"ASTRION_IGNORE_DOTENV": "1", "ASTRION_DATA_ROOT": str(self.root)}))
        from config import model_profiles
        from modules import modelsdev_registry, provider_manager
        from server.model_catalog import model_catalog_bp
        from utils.api_client.codex import accounts, models as codex_models

        self.registry = modelsdev_registry
        self.provider = provider_manager
        self.model_profiles = model_profiles
        paths = {
            "DATA_DIR": self.root,
            "_SNAPSHOT_PATH": self.root / "modelsdev_snapshot.json",
            "_memory": None,
            "_source_sig": None,
        }
        for name, value in paths.items():
            self.enterContext(patch.object(modelsdev_registry, name, value))
        self.enterContext(patch.object(provider_manager, "DATA_DIR", self.root))
        self.enterContext(patch.object(provider_manager, "_CATALOG_PATH", self.root / "providers_catalog.json"))
        self.enterContext(patch.object(provider_manager, "_MANAGER", None))
        self.enterContext(patch.object(model_profiles, "resolve_deploy_config", self.root.joinpath))
        self.write("providers_catalog.json", FIXTURE["definitions"])
        self.write("providers.json", {"version": 2, "providers": {"fixture": {
            "catalog_id": "fixture-provider", "requested_id": "fixture-provider", "display_index": 0,
            "base_name": "Fixture Provider", "name": "Fixture Provider",
            "base_url": "https://provider.invalid/v1", "api_key": "secret-provider-key",
            "models": FIXTURE["stored_models"],
        }}})
        self.write("modelsdev_cache.json", FIXTURE["cache"])
        self.write("codex_auth.json", {"tokens": {"access_token": "secret-codex-key"}})
        self.write("codex_models.json", FIXTURE["codex"])
        codex = codex_models.CodexModelsManager(
            auth_manager=SimpleNamespace(auth_path=self.root / "codex_auth.json"),
            cache_path=str(self.root / "codex_models.json"),
        )
        account = {"id": "openai-codex", "connected": True}
        store = SimpleNamespace(list_accounts=lambda: [account], get_account=lambda _: account)
        self.enterContext(patch.object(accounts, "get_codex_account_store", return_value=store))
        self.enterContext(patch.object(codex_models, "get_models_manager", return_value=codex))
        app = Flask(__name__)
        app.register_blueprint(model_catalog_bp)
        self.client = app.test_client()

    def write(self, name, payload):
        (self.root / name).write_text(json.dumps(payload), encoding="utf-8")

    def test_discovery_and_api_keep_context_input_and_output_distinct(self):
        discovered = self.provider.parse_models_payload(FIXTURE["discovery"])
        self.assertEqual(discovered, FIXTURE["stored_models"][:-2])
        slim = self.registry.slim_full_dump(FIXTURE["upstream"], {"openai"})
        self.assertEqual(slim["version"], FIXTURE["cache"]["version"])
        self.assertEqual(slim["providers"], FIXTURE["cache"]["providers"])
        response = self.client.get("/api/v1/models")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
        self.assertEqual({item["model_key"]: [item[field] for field in FIELDS] for item in response.json["items"]}, FIXTURE["expected"])
        self.assertNotIn("secret-", response.get_data(as_text=True))

    def test_v2_cache_stays_readable_offline_and_upgrades_on_next_refresh(self):
        old = {"version": 2, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "providers": {
            "openai": {"models": {"gpt-5.4": {"context": 1050000, "max_output": 128000}}},
        }}
        self.write("modelsdev_cache.json", old)
        with patch.object(httpx, "get", side_effect=httpx.ConnectError("offline")):
            self.assertFalse(self.registry.refresh_cache({"openai"})[0])
        self.assertEqual(json.loads((self.root / "modelsdev_cache.json").read_text()), old)
        items = {item["model_key"]: item for item in self.client.get("/api/v1/models").json["items"]}
        self.assertEqual([items["fixture/gpt-5.4"][field] for field in FIELDS], [1050000, None, 1050000, None, 128000])
        with patch.object(httpx, "get", return_value=httpx.Response(200, json=FIXTURE["upstream"])) as fetch:
            self.assertEqual(self.registry.refresh_cache({"openai"}), (True, None))
            self.assertEqual(self.registry.refresh_cache({"openai"}), (True, None))
            fetch.assert_called_once()
        items = {item["model_key"]: item for item in self.client.get("/api/v1/models").json["items"]}
        self.assertEqual([items["fixture/gpt-5.4"][field] for field in FIELDS], FIXTURE["expected"]["fixture/gpt-5.4"])

    def test_chat_budget_uses_the_same_maximum_as_the_directory(self):
        from utils.api_client import APIClient

        key = "fixture/expanded"
        profile = self.model_profiles.get_model_profile(key)
        self.assertEqual(self.model_profiles.get_model_context_window(key), 1000000)
        self.assertEqual(profile["fast"]["context_window"], 1000000)
        self.assertEqual(profile["thinking"]["context_window"], 1000000)
        requests = []

        def respond(request):
            requests.append((request.url.path, json.loads(request.content), request.headers))
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: [DONE]\n\n")

        new_client = httpx.AsyncClient

        async def trigger(thinking):
            client = APIClient(thinking_mode=thinking)
            with patch.object(client, "_debug_log"), patch.object(client, "_dump_request_payload", return_value=None):
                client.apply_profile(profile)
                self.assertEqual(client.default_context_window, 1000000)
                client.update_context_budget(895000, self.model_profiles.get_model_context_window(key))
                async for _ in client.chat([{"role": "user", "content": "fixture"}]):
                    pass

        with patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: new_client(transport=httpx.MockTransport(respond), **kwargs)):
            asyncio.run(trigger(False))
            asyncio.run(trigger(True))
        self.assertEqual(len(requests), 2)
        for path, body, headers in requests:
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(body["max_tokens"], 64000)
            self.assertNotIn("context_window", body)
            self.assertNotIn("anthropic-beta", headers)


if __name__ == "__main__":
    unittest.main()
