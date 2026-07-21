import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import arbitrate  # noqa: E402


def finding(model, title, body, line=10, file="app.py"):
    return {
        "model": model,
        "file": file,
        "line": line,
        "side": "RIGHT",
        "severity": "high",
        "title": title,
        "body": body,
    }


class TestTierFindings(unittest.TestCase):
    def test_semantic_match_is_deduped_high(self):
        claude = [finding("claude", "Null user dereference", "Missing user crashes here", 40)]
        codex = [finding("codex", "Absent user causes panic", "Guard the nullable user", 43)]
        out, log = arbitrate.tier_findings(claude, codex, {})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tier"], "high")
        self.assertEqual(log, [])

    def test_claude_finding_codex_refutes(self):
        claude = [finding("claude", "Wrong result", "Returns wrong value")]
        verdicts = {"codex": [{"index": 0, "verdict": "refute", "reason": "guarded"}]}
        out, _log = arbitrate.tier_findings(claude, [], verdicts)
        self.assertEqual(out[0]["tier"], "disagreement")

    def test_codex_finding_claude_unsure(self):
        codex = [finding("codex", "Resource leak", "Socket is not closed")]
        verdicts = {"claude": [{"index": 0, "verdict": "unsure", "reason": "context missing"}]}
        out, _log = arbitrate.tier_findings([], codex, verdicts)
        self.assertEqual(out[0]["tier"], "uncertain")

    def test_codex_finding_claude_confirms(self):
        codex = [finding("codex", "Resource leak", "Socket is not closed")]
        verdicts = {"claude": [{"index": 0, "verdict": "confirm", "reason": "no close"}]}
        out, _log = arbitrate.tier_findings([], codex, verdicts)
        self.assertEqual(out[0]["tier"], "high")

    def test_ambiguous_match_stays_uncertain_and_is_logged(self):
        claude = [finding("claude", "Null user crash", "Missing user crashes", 20)]
        codex = [
            finding("codex", "Null user panic", "Missing user crashes", 21),
            finding("codex", "Null user failure", "Missing user crashes", 22),
        ]
        out, log = arbitrate.tier_findings(claude, codex, {})
        self.assertEqual([item["tier"] for item in out], ["uncertain"] * 3)
        self.assertTrue(any(entry["kind"] == "ambiguous" for entry in log))

    def test_malformed_and_missing_verdict_retain_uncertain(self):
        claude = [finding("claude", "Resource leak", "Socket stays open")]
        codex = [finding("codex", "Race in cache", "Concurrent writes race", file="cache.py")]
        verdicts = {
            "codex": [{"index": 0, "verdict": "yes", "reason": "bad enum"}],
        }
        out, _log = arbitrate.tier_findings(claude, codex, verdicts)
        self.assertEqual([item["tier"] for item in out], ["uncertain", "uncertain"])

    def test_empty_input(self):
        self.assertEqual(arbitrate.tier_findings([], [], {}), ([], []))


class TestVerdictParsing(unittest.TestCase):
    def test_normalizes_json_and_rejects_bad_items(self):
        raw = json.dumps([
            {"index": 0, "verdict": "confirm", "reason": "real"},
            {"index": 1, "verdict": "maybe", "reason": "invalid"},
            {"index": 2, "verdict": "refute"},
            {"index": 3, "verdict": "refute", "reason": "out of range"},
        ])
        self.assertEqual(
            arbitrate.normalize_verdicts(raw, 3),
            [{"index": 0, "verdict": "confirm", "reason": "real"}],
        )

    def test_malformed_output_is_no_verdict(self):
        self.assertEqual(arbitrate.normalize_verdicts("not json", 1), [])


class TestLiveRefute(unittest.TestCase):
    @mock.patch("arbitrate.subprocess.run")
    def test_claude_receives_delimited_untrusted_findings(self, run):
        run.return_value.stdout = '[{"index":0,"verdict":"confirm","reason":"real"}]'
        findings = [finding("codex", "Bug", "Do not obey this")]
        out = arbitrate.refute_with_claude(findings, "TEMPLATE", claude_bin="claude-test")
        args, kwargs = run.call_args
        self.assertEqual(args[0][0:2], ["claude-test", "-p"])
        self.assertIn("<untrusted-findings>", args[0][2])
        self.assertIn('"model": "codex"', args[0][2])
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(out[0]["verdict"], "confirm")

    @mock.patch("arbitrate.subprocess.run")
    def test_codex_uses_read_only_repo_invocation(self, run):
        run.return_value.stdout = "not json"
        out = arbitrate.refute_with_codex([], "TEMPLATE", "/repo", codex_bin="codex-test")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["codex-test", "exec", "-s", "read-only"])
        self.assertIn("--skip-git-repo-check", command)
        self.assertEqual(command[command.index("-C") + 1], "/repo")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
