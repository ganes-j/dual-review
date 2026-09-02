import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import orchestrator  # noqa: E402


def _cfg(tmp, watch, manual_allow=None):
    path = os.path.join(tmp, "allowlist.json")
    config = {"watch": watch, "allow": ["your-username/*"], "deny": ["your-work-org"]}
    if manual_allow is not None:
        config["manual_allow"] = manual_allow
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return path


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.pr = {"number": 5, "headRefOid": "abc", "author": {"login": "your-username"}, "title": "x"}

    def _patches(self, candidates):
        return [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]),
            mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff --git a b\n@@ -1 +1 @@\n+x\n"),
            mock.patch.object(orchestrator.discovery, "select_candidates", return_value=candidates),
            mock.patch.object(orchestrator, "review_pr", return_value=3),
        ]

    def test_dry_run_discovers_but_never_posts(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        patches = self._patches([self.pr])
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp, dry_run=True)
        orchestrator.review_pr.assert_not_called()

    def test_happy_path_reviews_and_marks(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        patches = self._patches([self.pr])
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp)
        orchestrator.review_pr.assert_called_once()
        import state as state_mod
        st = state_mod.load(self.state)
        self.assertTrue(state_mod.is_reviewed(st, "your-username/dual-review", 5))

    def test_scope_gate_skips_denied_watch_entry(self):
        cfg = _cfg(self.tmp, ["your-work-org/secret"])
        patches = self._patches([self.pr])
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp)
        orchestrator.ghio.list_open_prs.assert_not_called()
        orchestrator.review_pr.assert_not_called()

    def test_deny_wins_on_auto_even_when_manual_allowed(self):
        # R12/AE5: a denied repo in BOTH watch and manual_allow is still skipped
        # on the auto tick — deny governs the auto path, manual_allow does not widen it.
        cfg = _cfg(self.tmp, ["your-work-org/secret"], manual_allow=["your-work-org/secret"])
        patches = self._patches([self.pr])
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp)
        orchestrator.ghio.list_open_prs.assert_not_called()
        orchestrator.review_pr.assert_not_called()

    def test_pr_without_repo_is_rejected(self):
        # --pr is scoped to one repo; without --repo it would fan out across watch (PR#3 review).
        with self.assertRaises(SystemExit):
            orchestrator.main(["--pr", "5"])

    def test_single_pr_run_skips_pass2_on_other_prs(self):
        # PR#3 review: --pr N must leave other PRs untouched (R8) — no pass-2 resolution on them.
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        pr4 = {"number": 4, "headRefOid": "a", "author": {"login": "x"}, "title": "t"}
        pr5 = {"number": 5, "headRefOid": "b", "author": {"login": "x"}, "title": "t"}
        marker4 = [{"user": {"login": "your-username"}, "body": discovery.marker(4)}]
        patches = [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[pr4, pr5]),
            mock.patch.object(orchestrator.ghio, "list_reviews",
                              side_effect=lambda repo, num: marker4 if num == 4 else []),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"),
            mock.patch.object(orchestrator.discovery, "select_candidates",
                              wraps=orchestrator.discovery.select_candidates),
            mock.patch.object(orchestrator, "review_pr", return_value=1),
            mock.patch.object(orchestrator, "resolve_pr", return_value=0),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                              only_repo="your-username/dual-review", only_pr=5)
        orchestrator.review_pr.assert_called_once()
        orchestrator.resolve_pr.assert_not_called()

    def test_no_candidates_no_review(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        patches = self._patches([])  # discovery returns nothing (all reviewed)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp)
        orchestrator.review_pr.assert_not_called()

    def test_empty_watch_is_noop(self):
        cfg = _cfg(self.tmp, [])
        patches = self._patches([self.pr])
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp)
        orchestrator.ghio.gh_login.assert_not_called()

    def test_only_pr_reviews_matching_pr_and_leaves_others_untouched(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        open_prs = [dict(self.pr, number=num) for num in (4, 5, 6)]
        with mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"), \
                mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=open_prs), \
                mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]), \
                mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"), \
                mock.patch.object(orchestrator.discovery, "select_candidates",
                                  wraps=orchestrator.discovery.select_candidates), \
                mock.patch.object(orchestrator, "review_pr", return_value=1) as review_pr:
            orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                                  only_pr=5)

        review_pr.assert_called_once()
        self.assertEqual(review_pr.call_args.args[1], 5)

    def test_only_pr_respects_marker_unless_same_pr_is_forced(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        marker_review = [{"user": {"login": "your-username"}, "body": discovery.marker(5)}]

        for force_pr, expected_calls in ((None, 0), (5, 1)):
            with self.subTest(force_pr=force_pr), \
                    mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"), \
                    mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]), \
                    mock.patch.object(orchestrator.ghio, "list_reviews",
                                      return_value=marker_review), \
                    mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"), \
                    mock.patch.object(orchestrator.discovery, "select_candidates",
                                      wraps=orchestrator.discovery.select_candidates), \
                    mock.patch.object(orchestrator, "resolve_pr", return_value=0), \
                    mock.patch.object(orchestrator, "review_pr", return_value=1) as review_pr:
                orchestrator.run_once(config_path=cfg, state_path=self.state,
                                      repo_dir=self.tmp, only_pr=5, force_pr=force_pr)
                self.assertEqual(review_pr.call_count, expected_calls)

    def test_auto_allowed_repo_without_only_pr_is_reviewed(self):
        cfg = _cfg(self.tmp, [])
        patches = self._patches([self.pr])
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in patches])

        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                              only_repo="your-username/dual-review")

        orchestrator.review_pr.assert_called_once()

    def test_manual_repo_without_only_pr_is_refused(self):
        repo = "your-work-org/manual-review"
        cfg = _cfg(self.tmp, [], manual_allow=[repo])
        with mock.patch.object(orchestrator, "_log") as log, \
                mock.patch.object(orchestrator.ghio, "list_open_prs") as list_open_prs, \
                mock.patch.object(orchestrator, "review_pr") as review_pr:
            orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                                  only_repo=repo)

        log.assert_called_once_with("SKIP %s: manual_allow repo requires an explicit --pr" % repo)
        list_open_prs.assert_not_called()
        review_pr.assert_not_called()

    def test_repo_in_neither_gate_is_refused(self):
        repo = "other/private"
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        with mock.patch.object(orchestrator, "_log") as log, \
                mock.patch.object(orchestrator.ghio, "list_open_prs") as list_open_prs, \
                mock.patch.object(orchestrator, "review_pr") as review_pr:
            orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                                  only_repo=repo, only_pr=5)

        log.assert_called_once_with("SKIP %s: repo is neither allowed nor manual_allowed" % repo)
        list_open_prs.assert_not_called()
        review_pr.assert_not_called()

    def test_manual_repo_with_only_pr_reviews_matching_pr(self):
        repo = "your-work-org/manual-review"
        cfg = _cfg(self.tmp, [], manual_allow=[repo])
        open_prs = [dict(self.pr, number=num) for num in (4, 5, 6)]
        with mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"), \
                mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=open_prs), \
                mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]), \
                mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"), \
                mock.patch.object(orchestrator.discovery, "select_candidates",
                                  wraps=orchestrator.discovery.select_candidates), \
                mock.patch.object(orchestrator, "review_pr", return_value=1) as review_pr:
            orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                                  only_repo=repo, only_pr=5)

        review_pr.assert_called_once()
        self.assertEqual(review_pr.call_args.args[:2], (repo, 5))

    def test_main_threads_pr_option_to_run_once(self):
        with mock.patch.object(orchestrator, "run_once") as run_once:
            orchestrator.main(["--repo", "your-username/dual-review", "--pr", "5"])

        run_once.assert_called_once_with(
            dry_run=False, force_pr=None, only_repo="your-username/dual-review", only_pr=5, codex=False,
            primary_runner="claude", hermes_model=None)


import discovery  # noqa: E402


def _agree_valid_suggestion():
    return {"intent": "agree", "still_valid": True, "fix_kind": "suggestion",
            "message": "m", "suggestion": "x = user.id if user else None", "patch": ""}


class TestPass2Resolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.pr = {"number": 5, "headRefOid": "abc", "author": {"login": "contributor"}, "title": "x"}
        self.thread = {
            "isResolved": False, "isOutdated": False,
            "comments": [
                {"id": 100, "author": "your-username", "body": "**[claude]** bug\n\ndetail", "path": "app.py", "line": 3},
                {"id": 101, "author": "contributor", "body": "yes fix this", "path": "app.py", "line": 3},
            ],
        }

    def _patches(self, judgment):
        marker_review = [{"user": {"login": "your-username"}, "body": discovery.marker(5)}]
        return [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]),
            mock.patch.object(orchestrator.ghio, "list_reviews", return_value=marker_review),
            mock.patch.object(orchestrator.discovery, "select_candidates", return_value=[]),
            mock.patch.object(orchestrator.ghio, "list_review_threads", return_value=[self.thread]),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"),
            mock.patch.object(orchestrator.resolve, "judge_reply", return_value=judgment),
            mock.patch.object(orchestrator.ghio, "reply_to_thread", return_value={}),
        ]

    def _run(self, judgment, **kw):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        patches = self._patches(judgment)
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp, **kw)

    def test_happy_path_drafts_and_marks_reply_processed(self):
        self._run(_agree_valid_suggestion())
        orchestrator.ghio.reply_to_thread.assert_called_once()
        body = orchestrator.ghio.reply_to_thread.call_args.args[3]
        self.assertIn("```suggestion", body)
        import state as state_mod
        self.assertTrue(state_mod.is_reply_processed(state_mod.load(self.state), 101))

    def test_dry_run_never_judges_or_posts(self):
        self._run(_agree_valid_suggestion(), dry_run=True)
        orchestrator.resolve.judge_reply.assert_not_called()
        orchestrator.ghio.reply_to_thread.assert_not_called()

    def test_defer_does_not_post_or_mark(self):
        self._run(None)  # judge could not produce a verdict (e.g. usage cap)
        orchestrator.ghio.reply_to_thread.assert_not_called()
        import state as state_mod
        self.assertFalse(state_mod.is_reply_processed(state_mod.load(self.state), 101))


class TestCodexGating(unittest.TestCase):
    """U3: manual_allow repos default Claude-only; --codex opts in. review_pr's
    5th positional arg is the effective router_off (True => Codex suppressed)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.pr = {"number": 5, "headRefOid": "abc", "author": {"login": "your-username"}, "title": "x"}

    def _run(self, cfg, only_repo, only_pr, codex):
        patches = [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]),
            mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"),
            mock.patch.object(orchestrator.discovery, "select_candidates", return_value=[self.pr]),
            mock.patch.object(orchestrator, "resolve_pr", return_value=0),
            mock.patch.object(orchestrator, "review_pr", return_value=1),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                              only_repo=only_repo, only_pr=only_pr, codex=codex)
        return orchestrator.review_pr

    def test_manual_repo_defaults_claude_only(self):
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        review_pr = self._run(cfg, "your-work-org/manual-review", 5, codex=False)
        review_pr.assert_called_once()
        self.assertTrue(review_pr.call_args.args[4])

    def test_manual_repo_with_codex_runs_both(self):
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        review_pr = self._run(cfg, "your-work-org/manual-review", 5, codex=True)
        review_pr.assert_called_once()
        self.assertFalse(review_pr.call_args.args[4])

    def test_auto_repo_dual_by_default_without_codex(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        review_pr = self._run(cfg, "your-username/dual-review", 5, codex=False)
        review_pr.assert_called_once()
        self.assertFalse(review_pr.call_args.args[4])

    def test_global_router_off_forces_claude_only_even_with_codex(self):
        off = os.path.join(self.tmp, ".router-off")
        open(off, "w").close()
        with mock.patch.object(orchestrator, "ROUTER_OFF", off):
            cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
            review_pr = self._run(cfg, "your-work-org/manual-review", 5, codex=True)
        review_pr.assert_called_once()
        self.assertTrue(review_pr.call_args.args[4])

    def test_main_threads_codex_flag(self):
        with mock.patch.object(orchestrator, "run_once") as run_once:
            orchestrator.main(["--repo", "your-work-org/manual-review", "--pr", "5", "--codex"])
        self.assertTrue(run_once.call_args.kwargs["codex"])

    def test_main_threads_hermes_runner_and_model(self):
        with mock.patch.object(orchestrator, "run_once") as run_once:
            orchestrator.main(["--primary-runner", "hermes", "--hermes-model", "anthropic/claude-sonnet-4"])
        self.assertEqual(run_once.call_args.kwargs["primary_runner"], "hermes")
        self.assertEqual(run_once.call_args.kwargs["hermes_model"], "anthropic/claude-sonnet-4")

    def test_hermes_runner_requires_an_explicit_model(self):
        with self.assertRaises(SystemExit):
            orchestrator.main(["--primary-runner", "hermes"])


class TestManualAudit(unittest.TestCase):
    """U4: manual_allow reviews append a durable audit line; auto reviews do not."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state.json")
        self.audit = os.path.join(self.tmp, "manual-audit.log")
        self.pr = {"number": 5, "headRefOid": "abc", "author": {"login": "your-username"}, "title": "x"}

    def _run(self, cfg, only_repo, only_pr=None, codex=False):
        patches = [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]),
            mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"),
            mock.patch.object(orchestrator.discovery, "select_candidates", return_value=[self.pr]),
            mock.patch.object(orchestrator, "resolve_pr", return_value=0),
            mock.patch.object(orchestrator, "review_pr", return_value=1),
            mock.patch.object(orchestrator, "MANUAL_AUDIT_PATH", self.audit),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                              only_repo=only_repo, only_pr=only_pr, codex=codex)

    def _audit_text(self):
        with open(self.audit, encoding="utf-8") as fh:
            return fh.read()

    def test_manual_review_writes_claude_only_line(self):
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        self._run(cfg, "your-work-org/manual-review", only_pr=5, codex=False)
        text = self._audit_text()
        self.assertIn("your-work-org/manual-review#5", text)
        self.assertIn("claude", text)
        self.assertNotIn("claude+codex", text)

    def test_manual_review_with_codex_records_both_lanes(self):
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        self._run(cfg, "your-work-org/manual-review", only_pr=5, codex=True)
        self.assertIn("claude+codex", self._audit_text())

    def test_auto_review_writes_no_audit(self):
        cfg = _cfg(self.tmp, ["your-username/dual-review"])
        self._run(cfg, only_repo=None)
        self.assertFalse(os.path.exists(self.audit))

    def test_audit_written_before_egress_survives_review_failure(self):
        # PR#3 review: the audit record precedes egress, so a failing review still leaves a trail.
        cfg = _cfg(self.tmp, [], manual_allow=["your-work-org/manual-review"])
        patches = [
            mock.patch.object(orchestrator.ghio, "gh_login", return_value="your-username"),
            mock.patch.object(orchestrator.ghio, "list_open_prs", return_value=[self.pr]),
            mock.patch.object(orchestrator.ghio, "list_reviews", return_value=[]),
            mock.patch.object(orchestrator.ghio, "pr_diff", return_value="diff"),
            mock.patch.object(orchestrator.discovery, "select_candidates", return_value=[self.pr]),
            mock.patch.object(orchestrator, "review_pr", side_effect=RuntimeError("boom")),
            mock.patch.object(orchestrator, "MANUAL_AUDIT_PATH", self.audit),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        with self.assertRaises(RuntimeError):
            orchestrator.run_once(config_path=cfg, state_path=self.state, repo_dir=self.tmp,
                                  only_repo="your-work-org/manual-review", only_pr=5, codex=True)
        self.assertIn("your-work-org/manual-review#5", self._audit_text())


if __name__ == "__main__":
    unittest.main()
