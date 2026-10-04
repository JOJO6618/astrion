import tempfile
import unittest
from pathlib import Path

from modules.file_manager import FileManager


class FileManagerReadEmptyFileTest(unittest.TestCase):
    def test_read_text_segment_empty_file_returns_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_file = Path(tmpdir) / "empty.txt"
            empty_file.write_text("", encoding="utf-8")

            manager = FileManager(tmpdir)
            result = manager.read_text_segment("empty.txt")

            self.assertTrue(result.get("success"))
            self.assertEqual(result.get("path"), "empty.txt")
            self.assertEqual(result.get("content"), "")
            self.assertEqual(result.get("total_lines"), 0)
            self.assertEqual(result.get("line_start"), 0)
            self.assertEqual(result.get("line_end"), 0)


if __name__ == "__main__":
    unittest.main()
