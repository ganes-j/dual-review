import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import scope  # noqa: E402


def _cfg(tmp, allow, deny):
    path = os.path.join(tmp, "allowlist.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"allow": allow, "deny": deny}, fh)
    return path


class TestScope(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = _cfg(self.tmp, ["your-username/*"], ["your-work-org"])

    def test_ae1_work_org_denied(self):
        self.assertFalse(scope.is_allowed("your-work-org/core", self.cfg))

    def test_deny_wins_over_allow(self):
        cfg = _cfg(self.tmp, ["your-username/*", "your-work-org/x"], ["your-work-org"])
        self.assertFalse(scope.is_allowed("your-work-org/x", cfg))

    def test_unlisted_repo_denied(self):
        self.assertFalse(scope.is_allowed("someoneelse/x", self.cfg))

    def test_allowlisted_repo_allowed(self):
        self.assertTrue(scope.is_allowed("your-username/api-service", self.cfg))
        self.assertTrue(scope.is_allowed("your-username/web-app", self.cfg))

    def test_exact_owner_no_prefix_or_substring(self):
        self.assertFalse(scope.is_allowed("your-username-evil/x", self.cfg))
        self.assertFalse(scope.is_allowed("your-usernameXYZ/foo", self.cfg))

    def test_malformed_repo_string(self):
        self.assertFalse(scope.is_allowed("noslash", self.cfg))
        self.assertFalse(scope.is_allowed("a/b/c", self.cfg))
        self.assertFalse(scope.is_allowed("/x", self.cfg))
        self.assertFalse(scope.is_allowed("x/", self.cfg))

    def test_missing_config_fail_closed(self):
        self.assertFalse(scope.is_allowed("your-username/api-service", os.path.join(self.tmp, "nope.json")))

    def test_malformed_json_fail_closed(self):
        path = os.path.join(self.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertFalse(scope.is_allowed("your-username/api-service", path))

    def test_empty_allow_fail_closed(self):
        cfg = _cfg(self.tmp, [], ["your-work-org"])
        self.assertFalse(scope.is_allowed("your-username/api-service", cfg))

    def test_empty_deny_fail_closed(self):
        cfg = _cfg(self.tmp, ["your-username/*"], [])
        self.assertFalse(scope.is_allowed("your-username/api-service", cfg))

    def test_missing_deny_fail_closed(self):
        path = os.path.join(self.tmp, "nodeny.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"allow": ["your-username/*"]}, fh)
        self.assertFalse(scope.is_allowed("your-username/api-service", path))


def _cfg_manual(tmp, manual_allow):
    path = os.path.join(tmp, "manual.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"allow": ["your-username/*"], "deny": ["your-work-org"],
                   "manual_allow": manual_allow}, fh)
    return path


class TestManualAllow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_exact_match_allowed(self):
        cfg = _cfg_manual(self.tmp, ["your-work-org/foo"])
        self.assertTrue(scope.is_manual_allowed("your-work-org/foo", cfg))

    def test_owner_glob_entry_rejected(self):
        cfg = _cfg_manual(self.tmp, ["your-work-org/*"])
        self.assertFalse(scope.is_manual_allowed("your-work-org/foo", cfg))

    def test_unlisted_repo_not_manual_allowed(self):
        cfg = _cfg_manual(self.tmp, ["your-work-org/foo"])
        self.assertFalse(scope.is_manual_allowed("your-work-org/bar", cfg))

    def test_missing_manual_allow_key_fail_closed(self):
        cfg = _cfg(self.tmp, ["your-username/*"], ["your-work-org"])  # no manual_allow
        self.assertFalse(scope.is_manual_allowed("your-work-org/foo", cfg))

    def test_empty_manual_allow_fail_closed(self):
        cfg = _cfg_manual(self.tmp, [])
        self.assertFalse(scope.is_manual_allowed("your-work-org/foo", cfg))

    def test_non_list_manual_allow_fail_closed(self):
        cfg = _cfg_manual(self.tmp, "your-work-org/foo")
        self.assertFalse(scope.is_manual_allowed("your-work-org/foo", cfg))

    def test_malformed_repo_string_rejected(self):
        cfg = _cfg_manual(self.tmp, ["your-work-org/foo"])
        self.assertFalse(scope.is_manual_allowed("your-work-org", cfg))
        self.assertFalse(scope.is_manual_allowed("", cfg))
        self.assertFalse(scope.is_manual_allowed("a/b/c", cfg))

    def test_missing_config_fail_closed(self):
        self.assertFalse(scope.is_manual_allowed(
            "your-work-org/foo", os.path.join(self.tmp, "nope.json")))

    def test_gates_are_independent(self):
        cfg = _cfg_manual(self.tmp, ["your-work-org/foo"])
        self.assertFalse(scope.is_allowed("your-work-org/foo", cfg))
        self.assertTrue(scope.is_manual_allowed("your-work-org/foo", cfg))


if __name__ == "__main__":
    unittest.main()
