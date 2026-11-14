"""存储空间统计回归：分类、扫描归桶、缓存、日志与备份清理。

全部使用临时目录，不读取也不删除真实数据根。
运行：在项目根执行 ``python3 -B test/2026-11-15_存储空间统计/test_storage_stats.py``
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.storage_stats import cache as cache_mod  # noqa: E402
from modules.storage_stats.catalog import classify  # noqa: E402
from modules.storage_stats.cleanup import clean_backups, clean_logs  # noqa: E402
from modules.storage_stats.scanner import scan  # noqa: E402

TEMP_BASE = ROOT / "cache"


class ClassifyTest(unittest.TestCase):
    def test_media_prefix_wins(self):
        self.assertEqual(classify(("conversations", "media_store", "a.png")), "media")
        self.assertEqual(classify(("conversations", "agents", "conv.json")), "agent_conversations")

    def test_known_prefixes(self):
        cases = {
            ("save", "conv_1", "snapshots", "1.json"): "backups",
            ("mcp_profiles", "hash", "Default", "a"): "mcp_profiles",
            ("sub_agent_tasks", "t1", "conversation.json"): "sub_agents",
            ("chat", "conversations", "a.json"): "chat",
            ("api_costs", "conv.json"): "other",
            ("storage_stats.json",): "other",
        }
        for parts, expected in cases.items():
            self.assertEqual(classify(parts), expected, parts)

    def test_file_rules(self):
        self.assertEqual(classify(("sub_agent_runtime.json",)), "sub_agents")
        self.assertEqual(classify(("sub_agents.json",)), "sub_agents")


class ScanTest(unittest.TestCase):
    def setUp(self):
        TEMP_BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="scan-", dir=str(TEMP_BASE)))
        self.data = self.tmp / "data"
        self.logs = self.tmp / "logs"
        (self.data / "save" / "conv_1" / "snapshots").mkdir(parents=True)
        (self.data / "save" / "conv_1" / "snapshots" / "1.json").write_text("a" * 2048)
        (self.data / "conversations" / "media_store").mkdir(parents=True)
        (self.data / "conversations" / "media_store" / "img.png").write_text("b" * 1024)
        (self.data / "conversations" / "ws" ).mkdir(parents=True)
        (self.data / "conversations" / "ws" / "conv_1.json").write_text("c" * 512)
        (self.data / "mcp_profiles" / "hash").mkdir(parents=True)
        (self.data / "mcp_profiles" / "hash" / "Cookies").write_text("d" * 256)
        (self.data / "sub_agent_runtime.json").write_text("{}")
        self.logs.mkdir()
        (self.logs / "a.log").write_text("e" * 4096)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_buckets(self):
        result = scan(self.data, self.logs)
        buckets = {item["id"]: item for item in result["categories"]}
        self.assertGreater(buckets["backups"]["bytes"], 0)
        self.assertGreater(buckets["media"]["bytes"], 0)
        self.assertGreater(buckets["agent_conversations"]["bytes"], 0)
        self.assertGreater(buckets["mcp_profiles"]["bytes"], 0)
        self.assertGreater(buckets["sub_agents"]["bytes"], 0)
        self.assertGreater(buckets["logs"]["bytes"], 0)
        # 总和 = 各分类之和，且等于顶层 total_bytes
        self.assertEqual(result["total_bytes"], sum(item["bytes"] for item in result["categories"]))
        self.assertEqual(len(result["backups"]), 1)
        self.assertEqual(result["backups"][0]["id"], "conv_1")

    def test_symlink_skipped(self):
        target = self.tmp / "outside.bin"
        target.write_text("x" * 100)
        (self.data / "save" / "conv_1" / "link.bin").symlink_to(target)
        result = scan(self.data, self.logs)
        buckets = {item["id"]: item for item in result["categories"]}
        self.assertLess(buckets["backups"]["files"], 100)


class CleanLogsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="logs-", dir=str(TEMP_BASE)))
        self.logs = self.tmp / "logs"
        self.logs.mkdir()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_truncate_active_and_remove_archives(self):
        (self.logs / "active.log").write_text("x" * 1000)
        (self.logs / "active.log.1").write_text("y" * 1000)
        sub = self.logs / "title_debug"
        sub.mkdir()
        (sub / "a.json").write_text("z" * 100)

        result = clean_logs(self.logs)

        self.assertEqual(result["truncated"], 1)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["failed"], [])
        self.assertTrue((self.logs / "active.log").exists())
        self.assertEqual((self.logs / "active.log").stat().st_size, 0)
        self.assertFalse((self.logs / "active.log.1").exists())
        self.assertFalse((sub / "a.json").exists())

    def test_missing_root_is_noop(self):
        result = clean_logs(self.tmp / "nope")
        self.assertEqual(result["freed_bytes"], 0)
        self.assertEqual(result["removed"], 0)


class CleanBackupsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="backups-", dir=str(TEMP_BASE)))
        self.save = self.tmp / "save"
        self.save.mkdir()

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_backup(self, name, days_old, with_meta=True):
        target = self.save / name / "snapshots"
        target.mkdir(parents=True)
        (target / "1.json").write_text("a" * 1024)
        ts = time.time() - days_old * 86400
        if with_meta:
            (self.save / name / "meta.json").write_text(
                json.dumps({"updated_at": datetime.fromtimestamp(ts).isoformat()})
            )
        import os

        os.utime(self.save / name, (ts, ts))

    def test_removes_only_old_conv_dirs(self):
        self._make_backup("conv_old", 40)
        self._make_backup("conv_new", 1)
        self._make_backup("not_conv", 40)

        result = clean_backups(self.save, 30)

        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(result["removed"], ["conv_old"])
        self.assertFalse((self.save / "conv_old").exists())
        self.assertTrue((self.save / "conv_new").exists())
        self.assertTrue((self.save / "not_conv").exists(), "非 conv_ 前缀目录不应被删除")

    def test_missing_meta_falls_back_to_mtime(self):
        self._make_backup("conv_old", 50, with_meta=False)
        result = clean_backups(self.save, 30)
        self.assertEqual(result["removed"], ["conv_old"])

    def test_symlink_dir_skipped(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        (self.save / "conv_link").symlink_to(outside)

        result = clean_backups(self.save, 1)

        self.assertEqual(result["removed"], [])
        self.assertTrue((outside / "keep.txt").exists())


class CacheTest(unittest.TestCase):
    def setUp(self):
        TEMP_BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="cache-", dir=str(TEMP_BASE)))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_and_version_guard(self):
        with mock.patch.object(cache_mod, "DATA_DIR", str(self.tmp)):
            self.assertIsNone(cache_mod.load_cache())
            payload = {
                "version": cache_mod.CACHE_VERSION,
                "scanned_at": "2026-11-15T03:00:00+08:00",
                "total_bytes": 123,
                "categories": [],
            }
            cache_mod.write_cache(payload)
            loaded = cache_mod.load_cache()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["total_bytes"], 123)

            cache_mod.write_cache({"version": 999, "total_bytes": 1})
            self.assertIsNone(cache_mod.load_cache(), "版本不符应视为无缓存")


if __name__ == "__main__":
    unittest.main(verbosity=2)
