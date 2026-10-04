from __future__ import annotations

import unittest

from utils.token_usage import extract_usage_payload, normalize_usage_payload


class TokenUsageExtractorTest(unittest.TestCase):
    def test_normalizes_common_aliases(self):
        usage = normalize_usage_payload({"input_tokens": 10, "outputTokens": 5})
        self.assertEqual(usage, {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "current_context_tokens": 10,
        })

    def test_extracts_top_level_usage(self):
        usage = extract_usage_payload({"usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
        self.assertEqual(usage["total_tokens"], 10)

    def test_extracts_choice_nested_usage(self):
        usage = extract_usage_payload({
            "choices": [
                {"delta": {"usage": {"inputTokens": 9, "outputTokens": 4}}}
            ]
        })
        self.assertEqual(usage["prompt_tokens"], 9)
        self.assertEqual(usage["completion_tokens"], 4)
        self.assertEqual(usage["total_tokens"], 13)

    def test_extracts_response_metadata_usage(self):
        usage = extract_usage_payload({
            "response_metadata": {
                "token_usage": {"promptTokens": 11, "completionTokens": 6, "totalTokens": 17}
            }
        })
        self.assertEqual(usage["current_context_tokens"], 11)


if __name__ == "__main__":
    unittest.main()
