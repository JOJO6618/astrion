"""错误分类与审核配置口径（2026-11-07）。"""

import unittest

from modules.retry_policy import RETRY_DELAY_SECONDS, RETRY_MAX_ATTEMPTS, classify_error, is_retryable_error
from modules.review_agent_config import resolve_review_agent_config


class RetryPolicyTest(unittest.TestCase):
    def test_retryable_cases(self):
        for payload in (
            {"status_code": 429},
            {"status_code": 500},
            {"status_code": 503, "message": "upstream error"},
            {"error_type": "connection_error"},
            {"message": "read timeout"},
            {"status_code": 408},
        ):
            self.assertEqual(classify_error(payload), "retryable", payload)
            self.assertTrue(is_retryable_error(payload), payload)

    def test_non_retryable_cases(self):
        self.assertEqual(classify_error({"status_code": 401}), "authentication")
        self.assertEqual(classify_error({"status_code": 403}), "authentication")
        self.assertEqual(classify_error({"status_code": 400, "message": "invalid_request"}), "configuration")
        self.assertEqual(classify_error({"status_code": 429, "message": "insufficient_quota"}), "quota_exhausted")
        self.assertEqual(classify_error({"message": "review model returned no response"}), "unknown_error")

    def test_retry_budget_matches_main_loop(self):
        self.assertEqual(RETRY_MAX_ATTEMPTS, 5)
        self.assertEqual(RETRY_DELAY_SECONDS, 10)


class ReviewAgentConfigTest(unittest.TestCase):
    def test_unknown_agent_returns_fixed_defaults(self):
        cfg = resolve_review_agent_config("__unknown_agent__")
        self.assertEqual(cfg["max_rounds"], 10)
        self.assertEqual(cfg["max_command_timeout"], 60)
        self.assertNotIn("timeout_seconds", cfg)


if __name__ == "__main__":
    unittest.main()
