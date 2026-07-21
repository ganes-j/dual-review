import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import discovery  # noqa: E402


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tool = "your-username"
        self.open_prs = [
            {"number": 1, "headRefOid": "aaa", "author": {"login": "your-username"}, "title": "one"},
            {"number": 2, "headRefOid": "bbb", "author": {"login": "your-username"}, "title": "two"},
            {"number": 3, "headRefOid": "ccc", "author": {"login": "your-username"}, "title": "three"},
        ]
        self.reviews = {
            1: [{"user": {"login": "your-username"}, "body": "summary\n" + discovery.marker(1)}],
            2: [{"user": {"login": "someone-else"}, "body": "see " + discovery.marker(2)}],
            3: [],
        }

    def test_skips_tool_reviewed_includes_rest(self):
        nums = sorted(p["number"] for p in discovery.select_candidates(self.open_prs, self.reviews, self.tool))
        self.assertEqual(nums, [2, 3])

    def test_ae3_marker_present_not_rereviewed(self):
        self.assertNotIn(1, [p["number"] for p in discovery.select_candidates(self.open_prs, self.reviews, self.tool)])

    def test_forged_marker_from_non_tool_author_not_counted(self):
        self.assertIn(2, [p["number"] for p in discovery.select_candidates(self.open_prs, self.reviews, self.tool)])

    def test_per_pr_keyed_survives_push(self):
        self.open_prs[0]["headRefOid"] = "new-sha-after-push"
        self.assertNotIn(1, [p["number"] for p in discovery.select_candidates(self.open_prs, self.reviews, self.tool)])

    def test_force_bypasses_marker_single_pr(self):
        cands = discovery.select_candidates(self.open_prs, self.reviews, self.tool, force_pr=1)
        nums = sorted(p["number"] for p in cands)
        self.assertEqual(nums, [1, 2, 3])

    def test_has_tool_review(self):
        self.assertTrue(discovery.has_tool_review(self.reviews[1], 1, self.tool))
        self.assertFalse(discovery.has_tool_review(self.reviews[2], 2, self.tool))
        self.assertFalse(discovery.has_tool_review(self.reviews[3], 3, self.tool))


if __name__ == "__main__":
    unittest.main()
