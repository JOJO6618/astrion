"""Isolated export preference/path regressions; never touch installed pet settings."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modules.crosspet.observer import CrossPetObserver, default_state_dir, read_destination
from modules.crosspet.settings import read_enabled, save_enabled
from utils.atomic_io import atomic_write_json


class ManualObserver(CrossPetObserver):
    def enqueue(self, item):
        self.queue.append(item)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.base = Path(self.temp.name)
        self.control = self.base / "config/crosspet.json"
        self.peer = self.base / "pet/astrion.json"
        self.out = self.base / "state"
        self.observer = ManualObserver(config_path=self.peer, settings_path=self.control)
        self.env = patch.dict("os.environ", {"CROSSPET_STATE_DIR": str(self.out)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_missing_preference_is_disabled_without_creation(self):
        self.assertFalse(read_enabled(self.control))
        self.observer.tick()
        self.assertFalse(self.control.exists())
        self.assertFalse(self.out.exists())

    def test_only_astrion_switch_is_required(self):
        save_enabled(self.control, True)
        self.observer.event(("user", "workspace", "conv_test"), "task", "gpt-5", "crosspet_start", {})
        self.observer.tick()
        self.assertFalse(self.peer.exists())
        self.assertEqual({p.name for p in self.out.iterdir()},
                         {"astrion-state.json", "astrion-host.json", "seen-astrion"})
        self.assertEqual(next(iter(json.loads((self.out / "astrion-state.json").read_text())["sessions"].values()))["pose"], "thinking")

    def test_peer_enabled_cannot_enable_export(self):
        self.peer.parent.mkdir()
        self.peer.write_text(json.dumps({"version": 1, "enabled": True, "state_dir": str(self.out)}))
        self.observer.tick()
        self.assertFalse(self.out.exists())

    def test_peer_disabled_does_not_block_custom_directory(self):
        custom = self.base / "custom"
        self.peer.parent.mkdir()
        self.peer.write_text(json.dumps({"version": 1, "enabled": False, "state_dir": str(custom)}))
        with patch.dict("os.environ", {"CROSSPET_STATE_DIR": ""}):
            save_enabled(self.control, True)
            self.observer.tick()
        self.assertTrue((custom / "astrion-state.json").is_file())
        self.assertFalse(self.out.exists())

    def test_platform_defaults_match_native_readers(self):
        self.assertEqual(default_state_dir("darwin"), Path("/tmp/crosspet"))
        with patch.dict("os.environ", {"LOCALAPPDATA": str(self.base / "Local")}):
            self.assertEqual(default_state_dir("win32"), self.base / "Local/CrossPet/state")
        with patch.dict("os.environ", {"CROSSPET_STATE_DIR": ""}):
            self.assertEqual(read_destination(self.peer), default_state_dir() / "astrion-state.json")

    def test_explicit_directory_precedes_peer_configuration(self):
        self.peer.parent.mkdir()
        self.peer.write_text('{"version":1,"state_dir":"invalid-relative"}')
        self.assertEqual(read_destination(self.peer), self.out / "astrion-state.json")

    def test_disabled_does_not_read_peer_configuration(self):
        with patch("modules.crosspet.observer.read_destination") as directory:
            self.observer.tick()
            directory.assert_not_called()

    def test_reenable_writes_immediately_without_waiting_for_heartbeat(self):
        save_enabled(self.control, True)
        self.observer.tick(100)
        sequence = self.observer.sequence
        save_enabled(self.control, False)
        self.observer.tick(101)
        save_enabled(self.control, True)
        self.observer.tick(102)
        self.assertGreater(self.observer.sequence, sequence)
        self.assertEqual(json.loads((self.out / "astrion-state.json").read_text())["ts"], 102)

    def test_preference_persists_and_requires_a_boolean(self):
        save_enabled(self.control, True)
        self.assertTrue(read_enabled(self.control))
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                save_enabled(self.control, value)
        save_enabled(self.control, False)
        self.assertFalse(read_enabled(self.control))

    def test_malformed_preference_never_outputs_state(self):
        self.control.parent.mkdir()
        self.control.write_text('{"version":1,"enabled":"true"}')
        with self.assertRaises(ValueError):
            self.observer.tick()
        self.assertFalse(self.out.exists())
        save_enabled(self.control, False)
        self.assertFalse(read_enabled(self.control))

    def test_preference_symlink_is_rejected(self):
        self.control.parent.mkdir()
        target = self.base / "unrelated.json"
        target.write_text("untouched")
        self.control.symlink_to(target)
        with self.assertRaises(ValueError):
            read_enabled(self.control)
        with self.assertRaises(ValueError):
            save_enabled(self.control, True)
        self.assertEqual(target.read_text(), "untouched")

    def test_disable_waits_for_inflight_write_then_blocks_future_writes(self):
        save_enabled(self.control, True)
        entered, release, disabled = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def blocking_write(path, value):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test write not released")
            atomic_write_json(path, value)

        def worker():
            try:
                self.observer.tick(100)
            except Exception as exc:
                errors.append(exc)

        def turn_off():
            save_enabled(self.control, False)
            disabled.set()

        with patch("modules.crosspet.observer.atomic_write_json", side_effect=blocking_write):
            writer = threading.Thread(target=worker)
            writer.start()
            self.assertTrue(entered.wait(3))
            switch = threading.Thread(target=turn_off)
            switch.start()
            try:
                self.assertFalse(disabled.wait(0.05))
            finally:
                release.set()
                writer.join(3)
                switch.join(3)
            self.assertFalse(writer.is_alive())
            self.assertFalse(switch.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(disabled.is_set())
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.iterdir()}
        self.observer.tick(111)
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
