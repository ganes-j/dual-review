import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import post  # noqa: E402


_DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 import os
+import sys
+x = 1
 y = 2
 z = 3
"""


def f(model, tier, title, line, file="app.py"):
    return {"model": model, "file": file, "line": line, "side": "RIGHT",
            "severity": "high", "title": title, "body": "detail", "tier": tier}


class TestValidDiffLines(unittest.TestCase):
    def test_right_side_added_and_context_lines(self):
        valid = post.valid_diff_lines(_DIFF)
        # new file lines 1..5 (import os, import sys, x=1, y=2, z=3) all commentable
        self.assertIn(("app.py", 2), valid)   # + import sys
        self.assertIn(("app.py", 3), valid)   # + x = 1
        self.assertIn(("app.py", 1), valid)   # context import os
        self.assertNotIn(("app.py", 99), valid)


class TestPayload(unittest.TestCase):
    def test_ae2_disagreement_summary_only_never_inline(self):
        findings = [f("claude", "disagreement", "maybe bug", 2)]
        payload = post.build_review_payload(findings, 7, _DIFF)
        self.assertNotIn("comments", payload)           # nothing inline
        self.assertIn("maybe bug", payload["body"])      # but in summary

    def test_high_on_valid_line_is_inline_and_in_summary(self):
        findings = [f("codex", "high", "null deref", 3)]
        payload = post.build_review_payload(findings, 7, _DIFF)
        self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(payload["comments"][0]["line"], 3)
        self.assertIn("null deref", payload["body"])

    def test_high_out_of_diff_falls_to_summary_no_422(self):
        findings = [f("codex", "high", "whole-file issue", 999)]
        payload = post.build_review_payload(findings, 7, _DIFF)
        self.assertNotIn("comments", payload)            # not inline -> can't 422
        self.assertIn("whole-file issue", payload["body"])

    def test_uncertain_never_inline(self):
        findings = [f("claude", "uncertain", "single-model note", 2)]
        payload = post.build_review_payload(findings, 7, _DIFF)
        self.assertNotIn("comments", payload)

    def test_exactly_one_marker(self):
        findings = [f("codex", "high", "x", 3)]
        body = post.build_review_payload(findings, 42, _DIFF)["body"]
        self.assertEqual(body.count(post.marker(42)), 1)

    def test_secret_shaped_strings_are_scrubbed_from_body_and_comments(self):
        leak = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        finding = {"model": "codex", "file": "app.py", "line": 3, "side": "RIGHT",
                   "severity": "high", "title": "leaked " + leak, "body": "token=" + leak, "tier": "high"}
        payload = post.build_review_payload([finding], 7, _DIFF)
        self.assertNotIn(leak, payload["body"])
        self.assertNotIn(leak, payload["comments"][0]["body"])

    def test_zero_findings_still_posts_clean_review_with_marker(self):
        payload = post.build_review_payload([], 42, _DIFF)
        self.assertNotIn("comments", payload)
        self.assertIn("No findings", payload["body"])
        self.assertIn(post.marker(42), payload["body"])
        self.assertEqual(payload["event"], "COMMENT")


if __name__ == "__main__":
    unittest.main()
