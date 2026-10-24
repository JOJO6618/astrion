"""Regression checks for isolated LibreOffice profiles and truthful failure status."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "agentskills/docx/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("docx_accept_changes", SCRIPTS / "accept_changes.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def document(path, tracked=False):
    body = '<w:ins w:id="1"><w:r><w:t>new</w:t></w:r></w:ins>' if tracked else '<w:r><w:t>new</w:t></w:r>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>' + body + '</w:p></w:body></w:document>')


class AcceptChangesTests(unittest.TestCase):
    def test_profiles_are_unique_and_cleaned(self):
        profiles = []
        with tempfile.TemporaryDirectory(prefix="astrion-test-") as temp:
            source = Path(temp) / "source.docx"
            document(source)
            with patch.object(module, "get_soffice_env", return_value={}), patch.object(module.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0)
                for index in range(2):
                    _, message = module.accept_changes(str(source), str(Path(temp) / f"out{index}.docx"))
                    self.assertTrue(message.startswith("Successfully"), message)
                calls = [call.args[0] for call in run.call_args_list]
                profiles = [next(arg for arg in call if arg.startswith("-env:UserInstallation=")) for call in calls]
                self.assertEqual(profiles[0], profiles[1])
                self.assertEqual(profiles[2], profiles[3])
                self.assertNotEqual(profiles[0], profiles[2])
                self.assertIn("file://", profiles[0])
            self.assertFalse(list(Path(temp).glob("astrion_lo_*")))
            self.assertTrue(source.is_file())

    def test_timeout_does_not_publish_or_claim_success(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.docx", Path(temp) / "out.docx"
            document(source, tracked=True)
            with patch.object(module, "get_soffice_env", return_value={}), patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("soffice", 60)):
                _, message = module.accept_changes(str(source), str(output))
            self.assertTrue(message.startswith("Error"))
            self.assertFalse(output.exists())

    def test_silent_macro_failure_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.docx", Path(temp) / "out.docx"
            document(source, tracked=True)
            with patch.object(module, "get_soffice_env", return_value={}), patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                _, message = module.accept_changes(str(source), str(output))
            self.assertIn("Tracked changes remain", message)
            self.assertFalse(output.exists())

    def test_input_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.docx"
            document(source, tracked=True)
            original = source.read_bytes()
            _, message = module.accept_changes(str(source), str(source))
            self.assertTrue(message.startswith("Error"))
            self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
