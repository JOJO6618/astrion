from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

# 本模块测试按 web 模式布局推断 skills 目录，需在导入 config 前设定模式。
os.environ["TERMINAL_SANDBOX_MODE"] = "web"

from modules.skills_manager import (
    _get_sync_lock,
    archive_skill_directory,
    get_skills_catalog,
    infer_private_skills_dir,
    sync_workspace_skills,
    validate_skill_directory,
    wait_skill_file_ready,
)


class SkillsManagerTest(unittest.TestCase):
    def test_validate_skill_directory_requires_skill_md_and_frontmatter_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "sample-skill"
            skill_dir.mkdir()

            missing_file = validate_skill_directory(skill_dir)
            self.assertFalse(missing_file.get("success"))
            self.assertEqual(missing_file.get("error"), "缺少 SKILL.md")

            (skill_dir / "SKILL.md").write_text("---\nname: Sample\n---\n", encoding="utf-8")
            missing_description = validate_skill_directory(skill_dir)
            self.assertFalse(missing_description.get("success"))
            self.assertEqual(missing_description.get("error"), "缺少 description:")

            (skill_dir / "SKILL.md").write_text(
                "---\nname: Sample\ndescription: Demo skill\n---\n",
                encoding="utf-8",
            )
            valid = validate_skill_directory(skill_dir)
            self.assertTrue(valid.get("success"))
            self.assertEqual(valid.get("skill_name"), "sample-skill")

    def test_archive_skill_directory_moves_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "sample-skill"
            target_root = root / "agentskills"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: Sample\ndescription: Demo skill\n---\n",
                encoding="utf-8",
            )

            archived = archive_skill_directory(skill_dir, target_root)
            self.assertTrue(archived.get("success"))
            self.assertFalse(skill_dir.exists())
            self.assertTrue((target_root / "sample-skill" / "SKILL.md").exists())

            duplicate_src = root / "sample-skill"
            duplicate_src.mkdir()
            (duplicate_src / "SKILL.md").write_text(
                "---\nname: Sample\ndescription: Demo skill\n---\n",
                encoding="utf-8",
            )
            duplicate = archive_skill_directory(duplicate_src, target_root)
            self.assertFalse(duplicate.get("success"))
            self.assertEqual(duplicate.get("error"), "目标 skill 已存在")

    def test_private_skills_are_cataloged_and_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_root = root / "global"
            private_root = root / "users" / "jojo" / "agentskills"
            data_dir = root / "users" / "jojo" / "data"
            project = root / "project"
            private_skill = private_root / "private-skill"
            private_skill.mkdir(parents=True)
            data_dir.mkdir(parents=True)
            project.mkdir(parents=True)
            (private_skill / "SKILL.md").write_text(
                "---\nname: Private Skill\ndescription: Private demo\n---\n",
                encoding="utf-8",
            )

            self.assertEqual(infer_private_skills_dir(data_dir), private_root.resolve())
            catalog = get_skills_catalog(base_dir=str(global_root), private_dir=private_root)
            self.assertEqual([item["id"] for item in catalog], ["private-skill"])

            synced = sync_workspace_skills(
                project,
                enabled_skills=["private-skill"],
                base_dir=str(global_root),
                private_dir=private_root,
            )
            self.assertTrue(synced.get("success"))
            self.assertTrue((project / ".astrion" / "skills" / "private-skill" / "SKILL.md").exists())


class WaitSkillFileReadyTest(unittest.TestCase):
    """读取方等待原语：覆盖并发全量同步（rmtree+重建）的瞬时窗口。"""

    def test_existing_file_returns_true_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / ".astrion" / "skills"
            target = skills_dir / "demo" / "SKILL.md"
            target.parent.mkdir(parents=True)
            target.write_text("x", encoding="utf-8")
            start = time.monotonic()
            self.assertTrue(wait_skill_file_ready(target, skills_dir, max_wait_seconds=0.5))
            self.assertLess(time.monotonic() - start, 0.2)

    def test_file_appearing_during_wait_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / ".astrion" / "skills"
            skills_dir.mkdir(parents=True)
            target = skills_dir / "demo" / "SKILL.md"

            def create_later():
                time.sleep(0.3)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("x", encoding="utf-8")

            threading.Thread(target=create_later, daemon=True).start()
            self.assertTrue(
                wait_skill_file_ready(target, skills_dir, max_wait_seconds=2.0, poll_interval=0.05)
            )

    def test_in_flight_sync_lock_is_awaited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / ".astrion" / "skills"
            skills_dir.mkdir(parents=True)
            target = skills_dir / "demo" / "SKILL.md"
            lock = _get_sync_lock(skills_dir)

            def fake_sync():
                with lock:
                    time.sleep(0.3)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("x", encoding="utf-8")

            threading.Thread(target=fake_sync, daemon=True).start()
            # 等假同步先拿到锁，模拟读取方撞上重建窗口
            time.sleep(0.05)
            self.assertFalse(target.is_file())
            self.assertTrue(
                wait_skill_file_ready(target, skills_dir, max_wait_seconds=2.0, poll_interval=0.05)
            )

    def test_missing_file_returns_false_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills_dir = root / ".astrion" / "skills"
            skills_dir.mkdir(parents=True)
            target = skills_dir / "nope" / "SKILL.md"
            start = time.monotonic()
            self.assertFalse(
                wait_skill_file_ready(target, skills_dir, max_wait_seconds=0.4, poll_interval=0.05)
            )
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.4)
            self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()
