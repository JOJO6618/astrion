"""审核调用器的重试口径（2026-11-07）。"""

import unittest
from unittest.mock import AsyncMock, patch

from modules import review_model_caller
from modules.review_model_caller import ReviewModelCallError, call_review_model

CFG = {
    "profile": {
        "name": "test-model",
        "thinking": {"base_url": "http://localhost/v1", "api_key": "test-key", "model_id": "test-model"},
    }
}


def _error(code, *, status_code=None, error_type=None):
    details = {"error_code": code}
    if status_code is not None:
        details["status_code"] = status_code
    if error_type is not None:
        details["error_type"] = error_type
    return ReviewModelCallError(code, details=details)


class ReviewRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_up_to_five_attempts(self):
        calls = []

        async def fake_once(*args, **kwargs):
            calls.append(1)
            raise _error("connection reset", error_type="connection_error")

        with patch.object(review_model_caller, "_call_review_once", fake_once), \
                patch.object(review_model_caller.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ReviewModelCallError):
                await call_review_model(CFG, [], [])
        self.assertEqual(len(calls), 5)

    async def test_non_retryable_fails_immediately(self):
        calls = []

        async def fake_once(*args, **kwargs):
            calls.append(1)
            raise _error("invalid api key", status_code=401)

        with patch.object(review_model_caller, "_call_review_once", fake_once), \
                patch.object(review_model_caller.asyncio, "sleep", AsyncMock()) as sleeper:
            with self.assertRaises(ReviewModelCallError):
                await call_review_model(CFG, [], [])
        self.assertEqual(len(calls), 1)
        sleeper.assert_not_called()

    async def test_succeeds_after_one_retry(self):
        calls = []

        async def fake_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise _error("rate limit", status_code=429)
            return {"content": "ok", "reasoning_content": "", "tool_calls": []}

        with patch.object(review_model_caller, "_call_review_once", fake_once), \
                patch.object(review_model_caller.asyncio, "sleep", AsyncMock()) as sleeper:
            result = await call_review_model(CFG, [], [])
        self.assertEqual(result["content"], "ok")
        self.assertEqual(len(calls), 2)
        sleeper.assert_awaited_once()

    async def test_quota_error_not_retried(self):
        calls = []

        async def fake_once(*args, **kwargs):
            calls.append(1)
            raise _error("insufficient_quota", status_code=429)

        with patch.object(review_model_caller, "_call_review_once", fake_once), \
                patch.object(review_model_caller.asyncio, "sleep", AsyncMock()):
            with self.assertRaises(ReviewModelCallError):
                await call_review_model(CFG, [], [])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
