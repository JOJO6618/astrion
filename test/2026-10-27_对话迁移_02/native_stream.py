"""Exercise the production stream adapter with native Python stdlib only."""
import ast
from pathlib import Path
from tempfile import SpooledTemporaryFile
import unittest
from zipfile import ZipFile


class NativeStreamTests(unittest.TestCase):
    def test_zip_reads_native_spooled_upload_without_seekable_requirement(self):
        # Load the exact production class without importing Flask/application
        # dependencies, which are not installed for every system interpreter.
        source = Path(__file__).resolve().parents[2] / "modules/conversation_transfer/archive.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == "_ArchiveInput")
        namespace = {}
        exec(compile(ast.Module(body=[adapter], type_ignores=[]), str(source), "exec"), namespace)
        with SpooledTemporaryFile() as upload:
            with ZipFile(upload, "w") as archive:
                archive.writestr("conversation.json", '{"messages":[]}')
                archive.writestr("assets/image", b"image-content")
            upload.seek(0)
            with ZipFile(namespace["_ArchiveInput"](upload)) as archive:
                self.assertEqual(archive.read("conversation.json"), b'{"messages":[]}')
                self.assertEqual(archive.read("assets/image"), b"image-content")
            self.assertFalse(upload.closed)


if __name__ == "__main__":
    unittest.main()
