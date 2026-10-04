from __future__ import annotations

import unittest

from utils.api_client import APIClient


class APIClientMultimodalSanitizeTest(unittest.TestCase):
    def test_text_only_model_adds_notice_when_only_media_parts(self):
        client = APIClient(web_mode=True)
        client.model_key = "DeepSeek-V4-Pro High"  # custom_models.json: multimodal=none

        messages = [
            {
                "role": "tool",
                "name": "mcp__playwright__browser_take_screenshot",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        sanitized = client._sanitize_messages_for_model_capability(messages)
        self.assertEqual(len(sanitized), 1)
        self.assertEqual(
            sanitized[0].get("content"),
            "当前模型无查看图片/视频能力，无法返回结果",
            sanitized[0],
        )

    def test_text_only_model_strips_image_parts_to_text(self):
        client = APIClient(web_mode=True)
        client.model_key = "DeepSeek-V4-Pro High"  # custom_models.json: multimodal=none

        messages = [
            {
                "role": "tool",
                "name": "mcp__playwright__browser_take_screenshot",
                "content": [
                    {"type": "text", "text": "截图成功"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        sanitized = client._sanitize_messages_for_model_capability(messages)
        self.assertEqual(len(sanitized), 1)
        self.assertIsInstance(sanitized[0].get("content"), str, sanitized[0])
        self.assertIn("截图成功", sanitized[0].get("content") or "", sanitized[0])
        self.assertIn(
            "当前模型无查看图片/视频能力，无法返回结果",
            sanitized[0].get("content") or "",
            sanitized[0],
        )

    def test_profile_text_only_fallback_strips_media_parts(self):
        client = APIClient(web_mode=True)
        client.model_key = ""
        client.model_multimodal = "none"

        messages = [
            {
                "role": "tool",
                "name": "view_image",
                "content": [
                    {"type": "text", "text": "已尝试查看图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        sanitized = client._sanitize_messages_for_model_capability(messages)
        self.assertEqual(len(sanitized), 1)
        self.assertIsInstance(sanitized[0].get("content"), str, sanitized[0])
        self.assertIn(
            "当前模型无查看图片/视频能力，无法返回结果",
            sanitized[0].get("content") or "",
            sanitized[0],
        )

    def test_profile_multimodal_model_keeps_image_parts(self):
        client = APIClient(web_mode=True)
        client.model_key = ""
        client.model_multimodal = "image,video"

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]

        sanitized = client._sanitize_messages_for_model_capability(messages)
        self.assertEqual(len(sanitized), 1)
        self.assertIsInstance(sanitized[0].get("content"), list, sanitized[0])
        self.assertEqual(
            [part.get("type") for part in sanitized[0].get("content") or []],
            ["text", "image_url"],
            sanitized[0],
        )

    def test_profile_multimodal_fallback_works_when_model_key_missing(self):
        client = APIClient(web_mode=True)
        client.model_key = ""  # 模拟未知/缺失 key，仅走 profile 兜底
        client.model_multimodal = "none"

        messages = [
            {
                "role": "tool",
                "content": [
                    {"type": "text", "text": "测试"},
                    {"type": "video_url", "video_url": {"url": "data:video/mp4;base64,abc"}},
                ],
            }
        ]

        sanitized = client._sanitize_messages_for_model_capability(messages)
        self.assertEqual(len(sanitized), 1)
        self.assertIsInstance(sanitized[0].get("content"), str, sanitized[0])
        self.assertIn(
            "当前模型无查看图片/视频能力，无法返回结果",
            sanitized[0].get("content") or "",
            sanitized[0],
        )


if __name__ == "__main__":
    unittest.main()
