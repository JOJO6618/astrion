"""Inspect real Responses outgoing JSON with synthetic auth and transport."""
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from utils.api_client import APIClient
from utils.api_client.codex.models import _profile_from_model_info
from utils.api_client.codex.service_tier import supports_fast_mode, validate_fast_mode


class RequestTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, enabled, *, oauth=True, info=None, account="account-a"):
        info = info if info is not None else {
            "service_tiers": [{"id": "priority"}],
            "supported_reasoning_levels": [{"effort": "high"}],
        }
        profile = _profile_from_model_info("test-model", info, account, "Synthetic")
        if not oauth:
            profile.update(responses_auth="bearer", instructions_source="")
            profile["thinking"].update(base_url="https://synthetic.invalid/v1", api_key="synthetic")
        client = APIClient(thinking_mode=True)
        client.apply_profile(profile)
        client.chatgpt_fast_mode = enabled
        client.reasoning_effort = "high"
        models = Mock(ensure_fresh=AsyncMock(), get_model_info=Mock(return_value=info))
        auth = Mock(get_access_token=AsyncMock(return_value="synthetic"),
                    get_account_id=Mock(return_value=account))
        requests = []

        async def lines():
            yield "event: response.completed"
            yield 'data: {"type":"response.completed","response":{"status":"completed","output":[]}}'
            yield ""

        response = SimpleNamespace(status_code=200, aiter_lines=lines)

        @asynccontextmanager
        async def stream(method, url, **kwargs):
            requests.append({"method": method, "url": url, **kwargs})
            yield response

        transport = SimpleNamespace(stream=stream)

        @asynccontextmanager
        async def transport_context(*args, **kwargs):
            yield transport

        with patch.object(client, "_codex_models_manager", return_value=models), \
                patch.object(client, "_codex_auth_manager", return_value=auth), \
                patch.object(client, "_build_responses_instructions", return_value="Synthetic"), \
                patch("utils.api_client.responses.mixin.httpx.AsyncClient", transport_context), \
                patch("utils.api_client.codex.settings.resolve_proxy", return_value=None):
            chunks = [item async for item in client.chat_responses(
                [{"role": "user", "content": "test"}], tools=None,
            )]
        self.assertFalse(any("error" in item for item in chunks), chunks)
        self.assertEqual(len(requests), 1)
        return requests[0]

    async def test_enabled_oauth_sends_priority_without_changing_reasoning(self):
        request = await self.capture(True)
        self.assertEqual(request["json"]["service_tier"], "priority")
        self.assertEqual(request["json"]["reasoning"]["effort"], "high")
        self.assertEqual(request["headers"]["chatgpt-account-id"], "account-a")

    async def test_off_does_not_send_service_tier(self):
        self.assertNotIn("service_tier", (await self.capture(False))["json"])

    async def test_non_oauth_never_receives_subscription_fast_preference(self):
        self.assertNotIn("service_tier", (await self.capture(True, oauth=False))["json"])

    async def test_capability_is_taken_from_selected_account_and_model(self):
        request = await self.capture(True, account="account-b", info={"service_tiers": []})
        self.assertEqual(request["headers"]["chatgpt-account-id"], "account-b")
        self.assertNotIn("service_tier", request["json"])

    async def test_legacy_catalog_advertisement_is_supported(self):
        request = await self.capture(True, info={"additional_speed_tiers": ["fast"]})
        self.assertEqual(request["json"]["service_tier"], "priority")


class CapabilityTests(unittest.TestCase):
    def test_missing_and_other_tiers_do_not_enable_fast(self):
        for info in [None, {}, {"service_tiers": [{"id": "flex"}]},
                     {"additional_speed_tiers": ["ultrafast"]}]:
            self.assertFalse(supports_fast_mode(info))

    def test_model_profile_exports_catalog_capability(self):
        info = {"service_tiers": [{"id": "priority"}]}
        self.assertTrue(_profile_from_model_info("test", info)["supports_chatgpt_fast_mode"])
        self.assertFalse(_profile_from_model_info("test", {})["supports_chatgpt_fast_mode"])

    def test_boolean_is_strict(self):
        for value in [1, 0, "true", "false", None, [], {}]:
            with self.assertRaises(ValueError):
                validate_fast_mode(value)
        self.assertTrue(validate_fast_mode(True))
        self.assertFalse(validate_fast_mode(False))
