"""Exercise real Flask settings/auth decorators with temporary desktop runtime data."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
        base = Path(directory)
        os.environ.update(ASTRION_IGNORE_DOTENV="1", ASTRION_DATA_ROOT=str(base / "runtime"),
                          TERMINAL_SANDBOX_MODE="host", AGENT_API_DUMP_ENABLED="0",
                          ASTRION_DESKTOP_VERSION="crosspet-test", CROSSPET_STATE_DIR=str(base / "state"))
        from flask import Flask
        from modules.crosspet.observer import CrossPetObserver
        from modules.crosspet.settings import control_path, read_enabled
        from server import crosspet_settings, state

        class Manual(CrossPetObserver):
            def ensure_started(self):
                self.start_requested = True

            def enqueue(self, item):
                self.queue.append(item)

        class ApiTests(unittest.TestCase):
            def setUp(self):
                self.case = tempfile.TemporaryDirectory(dir=base)
                self.root = Path(self.case.name)
                self.observer = Manual(config_path=self.root / "absent-pet.json",
                                       settings_path=self.root / "config/crosspet.json")
                self.observer.start_requested = False
                self.mock = patch.object(crosspet_settings, "OBSERVER", self.observer)
                self.mock.start()
                app = Flask(__name__)
                app.secret_key = "synthetic-test-key"
                app.register_blueprint(crosspet_settings.crosspet_settings_bp)
                self.client = app.test_client()
                state.active_login_nonces["test-host"] = {"synthetic-nonce"}
                self.login("admin")

            def login(self, role):
                with self.client.session_transaction() as session:
                    session.update(username="test-host", role=role, login_nonce="synthetic-nonce")

            def tearDown(self):
                self.mock.stop()
                state.active_login_nonces.pop("test-host", None)
                self.case.cleanup()

            def test_default_and_saved_preference_without_pet_setup(self):
                response = self.client.get("/api/crosspet")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json, {"success": True, "supported": True, "enabled": False})
                self.assertFalse(self.observer.settings_path.exists())
                response = self.client.post("/api/crosspet", json={"enabled": True})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json["enabled"])
                self.assertTrue(self.observer.start_requested)
                self.assertFalse(self.observer.config_path.exists())
                self.assertTrue(read_enabled(self.observer.settings_path))
                self.assertTrue(self.client.get("/api/crosspet").json["enabled"])
                self.assertEqual(control_path(), base / "runtime/config/crosspet.json")

            def test_disable_stops_writing_and_reenable_resumes(self):
                self.client.post("/api/crosspet", json={"enabled": True})
                self.observer.tick(100)
                target = base / "state/astrion-state.json"
                before = target.read_bytes()
                response = self.client.post("/api/crosspet", json={"enabled": False})
                self.assertEqual(response.status_code, 200)
                self.observer.tick(120)
                self.assertEqual(target.read_bytes(), before)
                self.client.post("/api/crosspet", json={"enabled": True})
                self.observer.tick(121)
                self.assertEqual(json.loads(target.read_text())["ts"], 121)

            def test_invalid_payload_cannot_enable_export(self):
                for value in ({}, {"enabled": "true"}, {"enabled": 1}, []):
                    self.assertEqual(self.client.post("/api/crosspet", json=value).status_code, 400)
                self.assertFalse(self.observer.settings_path.exists())

            def test_anonymous_and_nonadmin_are_rejected(self):
                with self.client.session_transaction() as session:
                    session.clear()
                self.assertEqual(self.client.get("/api/crosspet").status_code, 401)
                self.assertEqual(self.client.post("/api/crosspet", json={"enabled": True}).status_code, 401)
                self.login("user")
                self.assertFalse(self.client.get("/api/crosspet").json["supported"])
                self.assertEqual(self.client.post("/api/crosspet", json={"enabled": True}).status_code, 403)
                self.assertFalse(self.observer.settings_path.exists())

            def test_nonhost_cannot_enable(self):
                with patch.object(crosspet_settings, "IS_HOST_MODE", False):
                    self.assertFalse(self.client.get("/api/crosspet").json["supported"])
                    self.assertEqual(self.client.post("/api/crosspet", json={"enabled": True}).status_code, 403)
                self.assertFalse(self.observer.settings_path.exists())

            def test_both_backend_entrypoints_register_settings(self):
                from server.app_legacy import app
                from server.headless_app import HEADLESS_BLUEPRINTS
                rules = [rule for rule in app.url_map.iter_rules() if rule.rule == "/api/crosspet"]
                self.assertEqual(len(rules), 2)
                self.assertTrue(any("POST" in rule.methods for rule in rules))
                self.assertIn(("crosspet_settings", crosspet_settings.crosspet_settings_bp), HEADLESS_BLUEPRINTS)

        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ApiTests))
        if not result.wasSuccessful():
            raise SystemExit(1)


if __name__ == "__main__":
    main()
