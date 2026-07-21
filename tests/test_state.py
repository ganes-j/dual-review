import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import state  # noqa: E402


class TestState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state", "dual-review.json")

    def test_missing_file_yields_empty(self):
        self.assertEqual(state.load(self.path), {"reviewed": {}, "processed_replies": []})

    def test_roundtrip_reviewed_and_reply(self):
        s = state.load(self.path)
        state.mark_reviewed(s, "your-username/api-service", 12, {"at": "2026-07-18"})
        state.mark_reply_processed(s, "c123")
        state.save(self.path, s)

        s2 = state.load(self.path)
        self.assertTrue(state.is_reviewed(s2, "your-username/api-service", 12))
        self.assertFalse(state.is_reviewed(s2, "your-username/api-service", 13))
        self.assertTrue(state.is_reply_processed(s2, "c123"))
        self.assertFalse(state.is_reply_processed(s2, "c999"))

    def test_reply_dedup(self):
        s = state.load(self.path)
        state.mark_reply_processed(s, "c1")
        state.mark_reply_processed(s, "c1")
        self.assertEqual(s["processed_replies"].count("c1"), 1)

    def test_corrupt_file_yields_empty(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{corrupt")
        self.assertEqual(state.load(self.path), {"reviewed": {}, "processed_replies": []})


if __name__ == "__main__":
    unittest.main()
