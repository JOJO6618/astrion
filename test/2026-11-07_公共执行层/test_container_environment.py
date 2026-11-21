"""A removed Docker resource must not be attached to the Host backend."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.execution_plane.local import LocalExecutionBackend
from modules.persistent_terminal import PersistentTerminal
from modules.terminal_manager import TerminalManager


class LegacyContainerResourceTest(unittest.TestCase):
    def test_local_backend_rejects_docker_resource(self):
        owner = SimpleNamespace(container_session=SimpleNamespace(mode="docker"))
        with self.assertRaisesRegex(ValueError, "only accepts Host"):
            LocalExecutionBackend(owner)

    def test_legacy_terminal_options_cannot_select_a_writable_host_shell(self):
        for constructor in (TerminalManager, PersistentTerminal):
            for options in ({"sandbox_mode": "web"}, {"sandbox_mode": "docker"},
                            {"sandbox_options": {"docker_readonly_exec": True}}):
                with self.subTest(constructor=constructor.__name__, options=options):
                    with self.assertRaises(ValueError):
                        constructor("synthetic", **options)


if __name__ == "__main__":
    unittest.main()
