"""Regression tests for shallow versioning snapshot pairing.

Covers the bug where intermediate track_edit journal rows persisted to
state.jsonl were replayed as independent snapshots, causing diffs to be
computed against a partial snapshot of the *same* message (every tracked
file showed as "added" with full insertions, e.g. +1928 -0).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from modules.shallow_versioning import ShallowVersioningManager


def _new_manager(project: Path, data: Path, conv: str = "conv_test") -> ShallowVersioningManager:
    """Mirror production usage: a fresh short-lived instance per operation."""
    return ShallowVersioningManager(project_path=project, data_dir=data, conversation_id=conv)


def _read_state_rows(data: Path, conv: str = "conv_test"):
    state_file = data / "save" / conv / "shallow_backups" / "state.jsonl"
    if not state_file.exists():
        return []
    return [json.loads(line) for line in state_file.read_text(encoding="utf-8").splitlines() if line.strip()]


class ShallowVersioningPairingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shallow_ver_test_"))
        self.project = self.tmp / "project"
        self.data = self.tmp / "data"
        self.project.mkdir(parents=True)
        self.data.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, content: str) -> None:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # realistic multi-instance flow (the original bug scenario)
    # ------------------------------------------------------------------
    def test_diff_pairs_with_previous_message_across_instances(self) -> None:
        # Production order: track_edit runs BEFORE the tool applies the edit
        # (tools_execution backs up first), so for newly created files
        # track_edit sees a missing file (v1=None) and make_snapshot stores v2.
        # message 1: AI creates two new files (each track_edit runs in its own instance)
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_1")
        self._write("a.txt", "a1\na2\n")
        _new_manager(self.project, self.data).track_edit("b.txt", "msg_1")
        self._write("b.txt", "b1\nb2\n")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        # message 2: modify a.txt, create c.txt
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_2")  # early-return: already tracked
        self._write("a.txt", "a1\na2 changed\na3\n")
        _new_manager(self.project, self.data).track_edit("c.txt", "msg_2")
        self._write("c.txt", "c1\n")
        _new_manager(self.project, self.data).make_snapshot("msg_2")

        # state file must hold exactly one row per message
        rows = _read_state_rows(self.data)
        msg_ids = [r["message_id"] for r in rows]
        self.assertEqual(msg_ids.count("msg_1"), 1, f"duplicate msg_1 rows: {msg_ids}")
        self.assertEqual(msg_ids.count("msg_2"), 1, f"duplicate msg_2 rows: {msg_ids}")

        # lazy reader (fresh instance, like the HTTP endpoints)
        reader = _new_manager(self.project, self.data)
        stats = reader.get_diff_stats("msg_2") or {}
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_2")}

        # a.txt modified (+2 -1), c.txt added (+1 -0), b.txt untouched
        self.assertEqual(set(files), {"a.txt", "c.txt"}, f"unexpected diff set: {files}")
        self.assertEqual(files["a.txt"]["status"], "modified")
        self.assertEqual((files["a.txt"]["insertions"], files["a.txt"]["deletions"]), (2, 1))
        self.assertEqual(files["c.txt"]["status"], "added")
        self.assertEqual((files["c.txt"]["insertions"], files["c.txt"]["deletions"]), (1, 0))
        self.assertEqual(stats.get("insertions"), 3)
        self.assertEqual(stats.get("deletions"), 1)

    # ------------------------------------------------------------------
    # legacy dirty logs: duplicates collapsed at load, healed on next save
    # ------------------------------------------------------------------
    def test_legacy_duplicate_rows_healed_on_load(self) -> None:
        conv = "conv_legacy"
        backup_dir = self.data / "save" / conv / "shallow_backups"
        backup_dir.mkdir(parents=True)

        # real backup files for a.txt v1/v2 and b.txt v1
        (backup_dir / "a@v1").write_text("a1\na2\n", encoding="utf-8")
        (backup_dir / "a@v2").write_text("a1\na2 changed\na3\n", encoding="utf-8")
        (backup_dir / "b@v1").write_text("b1\n", encoding="utf-8")

        def backup(name, version):
            return {"backup_file_name": name, "version": version, "backup_time": "t"}

        # hand-craft the legacy append-only pattern: partials + full per message
        legacy_rows = [
            {"message_id": "msg_1", "timestamp": "t", "tracked_file_backups": {"a.txt": backup("a@v1", 1)}},
            {"message_id": "msg_1", "timestamp": "t", "tracked_file_backups": {"a.txt": backup("a@v1", 1), "b.txt": backup("b@v1", 1)}},
            {"message_id": "msg_2", "timestamp": "t", "tracked_file_backups": {"a.txt": backup("a@v2", 2), "b.txt": backup("b@v1", 1)}},
        ]
        state_file = backup_dir / "state.jsonl"
        state_file.write_text("".join(json.dumps(r) + "\n" for r in legacy_rows), encoding="utf-8")

        manager = _new_manager(self.project, self.data, conv)
        # load collapses duplicates: one snapshot per message
        self.assertEqual(len(manager.list_snapshots()), 2)
        # diff for msg_2 pairs against msg_1 full: only a.txt modified, NOT both added
        files = {f["path"]: f for f in manager.get_file_diff_stats("msg_2")}
        self.assertEqual(set(files), {"a.txt"}, f"unexpected diff set: {files}")
        self.assertEqual(files["a.txt"]["status"], "modified")
        self.assertEqual((files["a.txt"]["insertions"], files["a.txt"]["deletions"]), (2, 1))

        # next mutation rewrites the file clean (one row per message)
        self._write("a.txt", "a1\na2 changed\na3\na4\n")
        _new_manager(self.project, self.data, conv).track_edit("a.txt", "msg_3")
        _new_manager(self.project, self.data, conv).make_snapshot("msg_3")
        rows = _read_state_rows(self.data, conv)
        msg_ids = [r["message_id"] for r in rows]
        self.assertEqual(len(msg_ids), len(set(msg_ids)), f"duplicates remain: {msg_ids}")

    # ------------------------------------------------------------------
    # message with exactly one newly tracked file (recorded stats were
    # accidentally correct pre-fix; lazy view was not)
    # ------------------------------------------------------------------
    def test_single_new_file_message(self) -> None:
        # Production order: track_edit before the edit is applied.
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_1")
        self._write("a.txt", "a1\n")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        # msg_2 edits a.txt and tracks one new file b.txt
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_2")  # early-return: already tracked
        self._write("a.txt", "a1\na2\n")
        _new_manager(self.project, self.data).track_edit("b.txt", "msg_2")
        self._write("b.txt", "b1\nb2\nb3\n")
        _new_manager(self.project, self.data).make_snapshot("msg_2")

        reader = _new_manager(self.project, self.data)
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_2")}
        self.assertEqual(set(files), {"a.txt", "b.txt"})
        self.assertEqual(files["a.txt"]["status"], "modified")
        self.assertEqual((files["a.txt"]["insertions"], files["a.txt"]["deletions"]), (1, 0))
        self.assertEqual(files["b.txt"]["status"], "added")
        self.assertEqual((files["b.txt"]["insertions"], files["b.txt"]["deletions"]), (3, 0))

    # ------------------------------------------------------------------
    # cross-instance visibility: mutation reloads state inside the lock
    # ------------------------------------------------------------------
    def test_mutation_reloads_state_from_other_instances(self) -> None:
        self._write("a.txt", "a1\n")
        stale = _new_manager(self.project, self.data)  # loads empty state
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_1")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        # stale instance mutates: must not wipe msg_1's row
        self._write("b.txt", "b1\n")
        stale.track_edit("b.txt", "msg_2")
        _new_manager(self.project, self.data).make_snapshot("msg_2")

        rows = _read_state_rows(self.data)
        msg_ids = [r["message_id"] for r in rows]
        self.assertIn("msg_1", msg_ids)
        self.assertIn("msg_2", msg_ids)
        self.assertEqual(len(msg_ids), 2)

    # ------------------------------------------------------------------
    # rewind still works
    # ------------------------------------------------------------------
    def test_rewind_restores_tracked_files(self) -> None:
        self._write("a.txt", "original\n")
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_1")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        self._write("a.txt", "modified\n")
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_2")
        _new_manager(self.project, self.data).make_snapshot("msg_2")

        result = _new_manager(self.project, self.data).rewind("msg_1")
        self.assertTrue(result.get("success"))
        self.assertEqual((self.project / "a.txt").read_text(encoding="utf-8"), "original\n")

    # ------------------------------------------------------------------
    # get_snapshot_by_seq maps to user messages correctly
    # ------------------------------------------------------------------
    def test_get_snapshot_by_seq(self) -> None:
        for i in (1, 2, 3):
            self._write(f"f{i}.txt", f"content {i}\n")
            _new_manager(self.project, self.data).track_edit(f"f{i}.txt", f"msg_{i}")
            _new_manager(self.project, self.data).make_snapshot(f"msg_{i}")

        reader = _new_manager(self.project, self.data)
        self.assertEqual(reader.get_snapshot_by_seq(1).message_id, "msg_1")
        self.assertEqual(reader.get_snapshot_by_seq(2).message_id, "msg_2")
        self.assertEqual(reader.get_snapshot_by_seq(3).message_id, "msg_3")
        self.assertIsNone(reader.get_snapshot_by_seq(4))


class ShallowVersioningFirstEditDiffBaseTest(unittest.TestCase):
    """Regression tests for the diff base of files first tracked in a message.

    Production order is: file exists on disk -> track_edit backs up the
    pre-edit content (v1) -> the edit is applied -> make_snapshot stores the
    post-edit backup (v2), replacing the intermediate track_edit row. The
    pre-edit backup then survives only on disk; diffs must recover it via the
    version fallback instead of showing the whole file as added (+all -0).
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shallow_ver_base_test_"))
        self.project = self.tmp / "project"
        self.data = self.tmp / "data"
        self.project.mkdir(parents=True)
        self.data.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, content: str) -> None:
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # the reported bug: first message edits a pre-existing file
    # ------------------------------------------------------------------
    def test_first_message_edit_of_preexisting_file_shows_real_diff(self) -> None:
        self._write("main.py", "line1\nline2\nline3\n")
        _new_manager(self.project, self.data).track_edit("main.py", "msg_1")
        self._write("main.py", "line1\nline2 changed\nline3\nline4\n")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        # the intermediate track_edit row was replaced: only the final row
        # remains, referencing v2; the pre-edit v1 survives only on disk.
        rows = _read_state_rows(self.data)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tracked_file_backups"]["main.py"]["version"], 2)

        reader = _new_manager(self.project, self.data)
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_1")}
        self.assertEqual(set(files), {"main.py"})
        self.assertEqual(files["main.py"]["status"], "modified")
        self.assertEqual((files["main.py"]["insertions"], files["main.py"]["deletions"]), (2, 1))
        stats = reader.get_diff_stats("msg_1") or {}
        self.assertEqual((stats.get("insertions"), stats.get("deletions")), (2, 1))

        patch = reader.get_file_patch_lines("main.py", "msg_1")
        adds = [l["content"] for l in patch["lines"] if l["type"] == "add"]
        removes = [l["content"] for l in patch["lines"] if l["type"] == "remove"]
        self.assertEqual(removes, ["line2"])
        self.assertEqual(adds, ["line2 changed", "line4"])

    # ------------------------------------------------------------------
    # a file created by the first message must still show as added
    # ------------------------------------------------------------------
    def test_first_message_new_file_still_added(self) -> None:
        _new_manager(self.project, self.data).track_edit("new.txt", "msg_1")  # absent at track time
        self._write("new.txt", "n1\nn2\n")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        reader = _new_manager(self.project, self.data)
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_1")}
        self.assertEqual(set(files), {"new.txt"})
        self.assertEqual(files["new.txt"]["status"], "added")
        self.assertEqual((files["new.txt"]["insertions"], files["new.txt"]["deletions"]), (2, 0))

    # ------------------------------------------------------------------
    # an edit that leaves content identical yields no changes (v1 kept)
    # ------------------------------------------------------------------
    def test_first_message_unchanged_edit_shows_no_changes(self) -> None:
        self._write("same.txt", "s1\ns2\n")
        _new_manager(self.project, self.data).track_edit("same.txt", "msg_1")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        rows = _read_state_rows(self.data)
        self.assertEqual(rows[0]["tracked_file_backups"]["same.txt"]["version"], 1)
        reader = _new_manager(self.project, self.data)
        self.assertEqual(reader.get_file_diff_stats("msg_1"), [])
        self.assertFalse(reader.has_any_changes("msg_1"))

    # ------------------------------------------------------------------
    # first message deletes a pre-existing file -> full deletions
    # ------------------------------------------------------------------
    def test_first_message_delete_shows_deletions(self) -> None:
        self._write("gone.txt", "g1\ng2\ng3\n")
        _new_manager(self.project, self.data).track_edit("gone.txt", "msg_1")
        (self.project / "gone.txt").unlink()
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        reader = _new_manager(self.project, self.data)
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_1")}
        self.assertEqual(set(files), {"gone.txt"})
        self.assertEqual(files["gone.txt"]["status"], "deleted")
        self.assertEqual((files["gone.txt"]["insertions"], files["gone.txt"]["deletions"]), (0, 3))

    # ------------------------------------------------------------------
    # the normal previous-snapshot path keeps winning for later messages
    # ------------------------------------------------------------------
    def test_second_message_diff_uses_previous_snapshot_row(self) -> None:
        self._write("a.txt", "a1\n")
        _new_manager(self.project, self.data).track_edit("a.txt", "msg_1")
        self._write("a.txt", "a1\na2\n")
        _new_manager(self.project, self.data).make_snapshot("msg_1")

        # already tracked: track_edit early-returns in production, so a plain
        # content change followed by make_snapshot is the realistic flow.
        self._write("a.txt", "a1\na2\na3\n")
        _new_manager(self.project, self.data).make_snapshot("msg_2")

        reader = _new_manager(self.project, self.data)
        files = {f["path"]: f for f in reader.get_file_diff_stats("msg_2")}
        self.assertEqual(set(files), {"a.txt"})
        self.assertEqual(files["a.txt"]["status"], "modified")
        self.assertEqual((files["a.txt"]["insertions"], files["a.txt"]["deletions"]), (1, 0))

        # msg_1 diff still resolves through the fallback (pre-existing file)
        files1 = {f["path"]: f for f in reader.get_file_diff_stats("msg_1")}
        self.assertEqual(files1["a.txt"]["status"], "modified")
        self.assertEqual((files1["a.txt"]["insertions"], files1["a.txt"]["deletions"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
