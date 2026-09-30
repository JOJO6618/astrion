"""Content preference persistence and independent prompt selection checks."""
import tempfile
import unittest
from pathlib import Path

from config.paths import PROMPTS_DIR
from modules.personalization_manager import (
    build_personalization_prompt,
    load_personalization_config,
    sanitize_personalization_payload,
    save_personalization_config,
)


class ContentPreferenceTests(unittest.TestCase):
    def test_defaults_and_invalid_values(self):
        for payload in ({}, {"morality_level": "none", "adult_content_restriction": "invalid"}):
            config = sanitize_personalization_payload(payload)
            self.assertEqual(config["morality_level"], "medium")
            self.assertEqual(config["adult_content_restriction"], "medium")

    def test_all_combinations_select_only_their_own_content(self):
        root = Path(PROMPTS_DIR) / "personalization"
        for morality in ("low", "medium", "high"):
            for adult in ("none", "low", "medium", "high"):
                with self.subTest(morality=morality, adult=adult):
                    prompt = build_personalization_prompt({
                        "enabled": True,
                        "morality_level": morality,
                        "adult_content_restriction": adult,
                    })
                    for kind, selected in (("morality", morality), ("adult_content", adult)):
                        for path in (root / kind).glob("*.txt"):
                            content = path.read_text(encoding="utf-8").strip()
                            self.assertEqual(content in prompt, path.stem == selected)
                    self.assertEqual(prompt.count("【道德准则："), 1)
                    self.assertEqual(prompt.count("【成人内容限制："), 1)
                    self.assertNotIn("{content}", prompt)
                    self.assertNotIn("{level}", prompt)

    def test_legacy_config_uses_medium_prompts(self):
        prompt = build_personalization_prompt({"enabled": True})
        self.assertIn("【道德准则：中】", prompt)
        self.assertIn("【成人内容限制：中】", prompt)

    def test_disabled_personalization_does_not_inject(self):
        self.assertIsNone(build_personalization_prompt({"enabled": False}))

    def test_save_and_reload_preserves_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            for morality, adult in (("low", "none"), ("medium", "low"), ("high", "high")):
                saved = save_personalization_config(folder, {
                    "enabled": True,
                    "morality_level": morality,
                    "adult_content_restriction": adult,
                })
                loaded = load_personalization_config(folder)
                for config in (saved, loaded):
                    self.assertEqual(config["morality_level"], morality)
                    self.assertEqual(config["adult_content_restriction"], adult)


if __name__ == "__main__":
    unittest.main()
