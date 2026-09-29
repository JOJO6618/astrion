import unittest
from unittest.mock import patch

from utils.api_client.codex import models as codex_models


class _Response:
    def __init__(self, status_code, payload=None, etag=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"etag": etag} if etag else {}

    def json(self):
        return self._payload


class _Client:
    responses = []
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class _Auth:
    def _get_cached(self):
        return {"tokens": {"access_token": "test-token", "account_id": "test-account"}}

    def _is_fresh(self, access):
        return True


class CodexModelDiscoveryTests(unittest.TestCase):
    def tearDown(self):
        _Client.responses = []
        _Client.calls = []

    def test_discovery_accepts_semver_and_rejects_invalid_payload(self):
        _Client.responses = [_Response(200, {"version": "0.159.0"})]
        with patch.object(codex_models.httpx, "Client", _Client):
            self.assertEqual(codex_models._discover_latest_client_version(), "0.159.0")

        _Client.responses = [_Response(200, {"version": "latest"})]
        with patch.object(codex_models.httpx, "Client", _Client):
            self.assertIsNone(codex_models._discover_latest_client_version())

    def test_refresh_falls_back_to_cached_version_after_latest_rejection(self):
        current = {
            "models": [{"slug": "cached-model"}],
            "etag": "cached-etag",
            "client_version": "0.153.4",
        }
        saved = []
        _Client.responses = [
            _Response(400),
            _Response(304),
        ]
        manager = codex_models.CodexModelsManager(_Auth())
        with (
            patch.object(codex_models, "_discover_latest_client_version", return_value="0.159.0"),
            patch.object(codex_models, "load_codex_data", return_value=current),
            patch.object(codex_models, "get_client_version", return_value="0.153.4"),
            patch.object(codex_models, "save_codex_data", side_effect=saved.append),
            patch.object(codex_models.httpx, "Client", _Client),
        ):
            result = manager.refresh_sync()

        self.assertTrue(result["refreshed"])
        self.assertTrue(result["not_modified"])
        self.assertEqual(result["client_version"], "0.153.4")
        self.assertEqual(len(_Client.calls), 2)
        self.assertEqual(_Client.calls[0][1]["params"], {"client_version": "0.159.0"})
        self.assertEqual(_Client.calls[1][1]["params"], {"client_version": "0.153.4"})
        self.assertEqual(_Client.calls[1][1]["headers"]["If-None-Match"], "cached-etag")
        self.assertEqual(saved[0]["client_version"], "0.153.4")


if __name__ == "__main__":
    unittest.main()
