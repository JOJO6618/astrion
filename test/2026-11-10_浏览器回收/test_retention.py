"""Browser group capacity and explicit close regressions."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.native_browser.retention import BrowserGroup, decide_retention


class RetentionTest(unittest.TestCase):
    def test_running_groups_count_toward_limit_and_can_exceed_it(self):
        groups = {str(index): BrowserGroup(index) for index in range(1, 5)}
        self.assertEqual(decide_retention(groups)["release"], ["1"])
        groups.pop("1")
        groups.update({str(index): BrowserGroup(index, running=True) for index in range(5, 9)})
        self.assertEqual(decide_retention(groups)["release"], ["2", "3", "4"])
        groups = {key: groups[key] for key in "5678"}
        self.assertEqual(decide_retention(groups)["release"], [])
        groups["7"] = replace(groups["7"], running=False)
        self.assertEqual(decide_retention(groups)["release"], ["7"])
        groups = {key: replace(groups[key], running=False) for key in "568"}
        self.assertEqual(decide_retention(groups)["release"], [])

    def test_close_releases_below_capacity_as_soon_as_main_task_ends(self):
        groups = {"A": BrowserGroup(1, closed=True, running=True)}
        self.assertEqual(decide_retention(groups)["release"], [])
        groups["A"] = replace(groups["A"], running=False)
        self.assertEqual(decide_retention(groups)["release"], ["A"])
        groups["A"] = replace(groups["A"], closed=False)
        self.assertEqual(decide_retention(groups)["release"], [])

    def test_closing_a_group_frees_a_slot_before_capacity_eviction(self):
        groups = {key: BrowserGroup(index) for index, key in enumerate("ABCD")}
        groups["D"] = replace(groups["D"], closed=True)
        self.assertEqual(decide_retention(groups)["release"], ["D"])

    def test_lru_follows_access_order_not_task_finish_order(self):
        groups = {"A": BrowserGroup(5), "B": BrowserGroup(2), "C": BrowserGroup(3), "D": BrowserGroup(4)}
        self.assertEqual(decide_retention(groups)["release"], ["B"])

    def test_metadata_only_groups_do_not_take_slots(self):
        groups = {"A": BrowserGroup(None, closed=True), "B": BrowserGroup(None, running=True),
                  "C": BrowserGroup(3), "D": BrowserGroup(4), "E": BrowserGroup(5)}
        self.assertEqual(decide_retention(groups)["release"], [])

    def test_unknown_existence_protects_pages_and_deletion_releases_saved_records(self):
        groups = {"A": BrowserGroup(1, closed=True, exists=None),
                  "B": BrowserGroup(2, running=True, exists=False), "C": BrowserGroup(None, exists=False)}
        self.assertEqual(decide_retention(groups), {"deleted": ["B", "C"], "release": []})


if __name__ == "__main__":
    unittest.main()
