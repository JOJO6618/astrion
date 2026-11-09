"""Focused policy regressions, synthetic identities and clocks only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modules.native_browser.retention import BrowserRetention, IDLE_MS, AI_PROTECTION_MS

NOW = 10_000_000


def tab(owner, *, selected=True, last=None, ai=0, busy=False):
    return {"id": owner, "owner": owner, "selected": selected,
            "lastActive": NOW if last is None else last, "lastAi": ai, "busy": busy}


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.policy = BrowserRetention()

    def decide(self, statuses, tabs, now=NOW):
        return self.policy.decide("synthetic", statuses, tabs, now)

    def test_all_foregrounds_plus_three_backgrounds(self):
        owners = {name: {"front": name in "AB"} for name in "ABCDEF"}
        result = self.decide(owners, [tab(name) for name in owners])
        self.assertEqual(len(result["retained"]), 5)
        self.assertTrue({"A", "B"} <= set(result["retained"]))
        self.assertEqual(len(result["release"]), 1)

    def test_background_limit_overrides_selection_and_ai_hour(self):
        owners = {name: {} for name in "ABCD"}
        rows = [tab(name, ai=NOW) for name in owners]
        self.assertEqual(self.decide(owners, rows)["release"], ["A"])

    def test_selected_and_ai_protection_then_thirty_minutes(self):
        rows = [tab("A", last=1), tab("A", selected=False, last=NOW-IDLE_MS+1),
                tab("A", selected=False, last=1, ai=NOW-AI_PROTECTION_MS+1),
                tab("A", selected=False, last=1, ai=NOW-AI_PROTECTION_MS)]
        for i, row in enumerate(rows): row["id"] = str(i)
        self.assertEqual(self.decide({"A": {"front": True}}, rows)["release"], ["3"])
        self.assertEqual(self.decide({"A": {"front": True}}, rows, NOW+1)["release"], ["1", "2", "3"])

    def test_running_to_background_gets_newest_slot(self):
        owners = {name: {"front": name=="A"} for name in "ABCDE"}
        rows = [tab(name) for name in owners]
        self.decide(owners, rows)
        owners["A"]["front"] = False
        self.assertIn("A", self.decide(owners, rows)["retained"])

    def test_recently_viewed_background_and_polling_do_not_reorder(self):
        owners = {name: {"front": name=="A"} for name in "ABCD"}
        rows = [tab(name) for name in owners]
        self.decide(owners, rows)
        owners["A"]["front"] = False
        owners["B"]["front"] = True
        self.decide(owners, rows)
        owners["B"]["front"] = False
        result = self.decide(owners, rows)
        self.assertEqual(result["release"], ["C"])
        self.assertEqual(result, self.decide(owners, rows, NOW+1000))

    def test_unknown_does_not_mean_deleted(self):
        result = self.decide({"A": {"unknown": True}}, [tab("A")])
        self.assertEqual(result["deleted"], [])
        self.assertIn("A", result["retained"])

    def test_deleted_overrides_all_protection(self):
        result = self.decide({"A": {"exists": False, "front": True}}, [tab("A", ai=NOW)])
        self.assertEqual(result["deleted"], ["A"])
        self.assertEqual(result["release"], ["A"])

    def test_busy_tab_is_not_idle_evicted(self):
        self.assertEqual(self.decide({"A": {"front": True}},
                         [tab("A", selected=False, last=1, busy=True)])["release"], [])

    def test_metadata_only_owners_do_not_take_slots(self):
        result = self.decide({name: {} for name in "ABCDE"}, [tab("A")])
        self.assertEqual(result["retained"], ["A"])
        self.assertEqual(result["release"], [])

    def test_fast_visits_between_polls_keep_actual_order(self):
        owners = {name: {"viewed": at} for name, at in [("Z", 1), ("A", 2), ("Y", 3), ("B", 4)]}
        result = self.decide(owners, [tab(name) for name in owners])
        self.assertEqual(result["release"], ["Z"])
        owners["Z"]["viewed"] = 5
        self.assertEqual(self.decide(owners, [tab(name) for name in owners])["release"], ["A"])


if __name__ == "__main__":
    unittest.main()
