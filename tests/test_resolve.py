import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import resolve  # noqa: E402


TOOL = "your-username-bot"
AUTHOR = "your-username"
ROOT_BODY = "**[claude]** null deref on user\n\nGuard `user` before deref."


def _c(cid, author, body, path="app.py", line=3):
    return {"id": cid, "author": author, "body": body, "path": path, "line": line}


def _root(cid=100, path="app.py", line=3, body=ROOT_BODY):
    return _c(cid, TOOL, body, path, line)


def _thread(comments, resolved=False, outdated=False):
    return {"isResolved": resolved, "isOutdated": outdated, "comments": comments}


def _finding(file="app.py", line=3, title="null deref", body="guard it", model="claude"):
    return {"file": file, "line": line, "title": title, "body": body, "model": model}


def _judg(intent="agree", still_valid=True, fix_kind="none", message="msg", suggestion="", patch=""):
    return {"intent": intent, "still_valid": still_valid, "fix_kind": fix_kind,
            "message": message, "suggestion": suggestion, "patch": patch}


class TestRecoverFinding(unittest.TestCase):
    def test_parses_tool_comment_into_finding(self):
        f = resolve.recover_finding(_root(path="src/auth.ts", line=42))
        self.assertEqual(f["file"], "src/auth.ts")
        self.assertEqual(f["line"], 42)
        self.assertEqual(f["model"], "claude")
        self.assertEqual(f["title"], "null deref on user")
        self.assertIn("Guard", f["body"])

    def test_unformatted_body_degrades_gracefully(self):
        f = resolve.recover_finding(_c(1, TOOL, "just some text", "a.py", 5))
        self.assertEqual(f["file"], "a.py")
        self.assertEqual(f["title"], "just some text")


class TestScrub(unittest.TestCase):
    def test_redacts_github_token_and_private_key(self):
        raw = ("token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
               "-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\n-----END RSA PRIVATE KEY-----")
        out = resolve.scrub_secrets(raw)
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", out)
        self.assertNotIn("MIIabc123", out)

    def test_none_is_safe(self):
        self.assertEqual(resolve.scrub_secrets(None), "")


class TestSensitivePath(unittest.TestCase):
    def test_workflow_and_secret_and_auth_paths_are_sensitive(self):
        self.assertTrue(resolve.is_sensitive_path(".github/workflows/ci.yml"))
        self.assertTrue(resolve.is_sensitive_path("src/auth/login.ts"))
        self.assertTrue(resolve.is_sensitive_path("config/.env"))
        self.assertTrue(resolve.is_sensitive_path("keys/id_rsa"))

    def test_ordinary_paths_are_not_sensitive(self):
        self.assertFalse(resolve.is_sensitive_path("app.py"))
        self.assertFalse(resolve.is_sensitive_path("src/author.py"))  # 'author' != 'auth'
        self.assertFalse(resolve.is_sensitive_path("src/utils/format.ts"))


class TestNormalizeJudgment(unittest.TestCase):
    def test_valid_object(self):
        raw = '{"intent":"agree","still_valid":true,"fix_kind":"suggestion","message":"m","suggestion":"x = user.id if user else None","patch":""}'
        j = resolve.normalize_judgment(raw)
        self.assertEqual(j["intent"], "agree")
        self.assertTrue(j["still_valid"])
        self.assertEqual(j["fix_kind"], "suggestion")

    def test_fenced_object_extracted(self):
        raw = "Here:\n```json\n{\"intent\":\"dismiss\",\"still_valid\":false,\"fix_kind\":\"none\",\"message\":\"m\"}\n```"
        j = resolve.normalize_judgment(raw)
        self.assertEqual(j["intent"], "dismiss")

    def test_malformed_returns_none(self):
        self.assertIsNone(resolve.normalize_judgment("not json"))
        self.assertIsNone(resolve.normalize_judgment(""))

    def test_bad_intent_returns_none(self):
        self.assertIsNone(resolve.normalize_judgment('{"intent":"whatever","still_valid":true,"fix_kind":"none","message":"m"}'))

    def test_non_bool_still_valid_returns_none(self):
        self.assertIsNone(resolve.normalize_judgment('{"intent":"agree","still_valid":"yes","fix_kind":"none","message":"m"}'))

    def test_invalid_finding_forces_no_fix(self):
        raw = '{"intent":"agree","still_valid":false,"fix_kind":"suggestion","message":"m","suggestion":"code"}'
        j = resolve.normalize_judgment(raw)
        self.assertEqual(j["fix_kind"], "none")
        self.assertEqual(j["suggestion"], "")

    def test_empty_suggestion_downgrades_fix_kind(self):
        raw = '{"intent":"agree","still_valid":true,"fix_kind":"suggestion","message":"m","suggestion":""}'
        self.assertEqual(resolve.normalize_judgment(raw)["fix_kind"], "none")


class TestDecideAction(unittest.TestCase):
    def test_none_judgment_defers_without_marking(self):
        d = resolve.decide_action(None, _finding())
        self.assertEqual(d["action"], "defer")
        self.assertFalse(d["mark_processed"])
        self.assertIsNone(d["post_body"])

    def test_dismiss_is_noop_but_marks_processed(self):
        d = resolve.decide_action(_judg(intent="dismiss", still_valid=True), _finding())
        self.assertEqual(d["action"], "none")
        self.assertIsNone(d["post_body"])
        self.assertTrue(d["mark_processed"])

    def test_ae4_agree_valid_suggestion_drafts(self):
        j = _judg(intent="agree", still_valid=True, fix_kind="suggestion",
                  suggestion="x = user.id if user else None")
        d = resolve.decide_action(j, _finding())
        self.assertEqual(d["action"], "draft")
        self.assertEqual(d["fix_kind"], "suggestion")
        self.assertIn("```suggestion", d["post_body"])
        self.assertIn("Unverified", d["post_body"])
        self.assertTrue(d["mark_processed"])

    def test_ae5_invalid_finding_pushback_no_draft(self):
        # author said "fix it" (intent agree) but re-verify found it no longer holds
        j = _judg(intent="agree", still_valid=False, fix_kind="none", message="line was already guarded")
        d = resolve.decide_action(j, _finding())
        self.assertEqual(d["action"], "pushback")
        self.assertNotIn("```suggestion", d["post_body"] or "")
        self.assertTrue(d["mark_processed"])

    def test_patch_fallback_uses_diff_block(self):
        j = _judg(intent="agree", still_valid=True, fix_kind="patch",
                  patch="--- a/app.py\n+++ b/app.py\n@@\n+def helper(): ...")
        d = resolve.decide_action(j, _finding())
        self.assertEqual(d["action"], "draft")
        self.assertEqual(d["fix_kind"], "patch")
        self.assertIn("```diff", d["post_body"])
        self.assertNotIn("```suggestion", d["post_body"])

    def test_sensitive_path_blocks_suggestion(self):
        j = _judg(intent="agree", still_valid=True, fix_kind="suggestion", suggestion="on: push")
        d = resolve.decide_action(j, _finding(file=".github/workflows/ci.yml"))
        self.assertEqual(d["action"], "blocked")
        self.assertNotIn("```suggestion", d["post_body"])
        self.assertTrue(d["mark_processed"])

    def test_question_valid_posts_clarification(self):
        d = resolve.decide_action(_judg(intent="question", still_valid=True, message="It fails when user is None."), _finding())
        self.assertEqual(d["action"], "clarify")
        self.assertIn("None", d["post_body"])
        self.assertTrue(d["mark_processed"])

    def test_post_body_is_scrubbed(self):
        j = _judg(intent="agree", still_valid=False, message="leaked ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        d = resolve.decide_action(j, _finding())
        self.assertNotIn("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", d["post_body"])


class TestSelectActionable(unittest.TestCase):
    def test_returns_finding_and_reply_for_author_reply(self):
        t = _thread([_root(100), _c(101, AUTHOR, "yes fix this")])
        acts = resolve.select_actionable([t], TOOL, set())
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["reply_id"], 101)
        self.assertEqual(acts[0]["root_id"], 100)
        self.assertEqual(acts[0]["reply_body"], "yes fix this")
        self.assertEqual(acts[0]["finding"]["file"], "app.py")

    def test_excludes_already_processed_reply(self):
        t = _thread([_root(100), _c(101, AUTHOR, "yes")])
        self.assertEqual(resolve.select_actionable([t], TOOL, {101}), [])

    def test_excludes_resolved_and_outdated_threads(self):
        base = [_root(100), _c(101, AUTHOR, "yes")]
        self.assertEqual(resolve.select_actionable([_thread(base, resolved=True)], TOOL, set()), [])
        self.assertEqual(resolve.select_actionable([_thread(base, outdated=True)], TOOL, set()), [])

    def test_ignores_non_tool_rooted_threads(self):
        # a thread the author started (root not authored by the tool) is not ours
        t = _thread([_c(200, AUTHOR, "random comment"), _c(201, "someone", "reply")])
        self.assertEqual(resolve.select_actionable([t], TOOL, set()), [])

    def test_tool_own_replies_are_not_actionable(self):
        # tool posted a pushback (id 102); it must not be treated as an author reply
        t = _thread([_root(100), _c(101, AUTHOR, "yes"), _c(102, TOOL, "pushback")])
        acts = resolve.select_actionable([t], TOOL, {101})
        self.assertEqual(acts, [])


class TestJudgeReply(unittest.TestCase):
    def _proc(self, stdout):
        m = mock.Mock()
        m.stdout = stdout
        return m

    def test_parses_model_output(self):
        raw = '{"intent":"agree","still_valid":true,"fix_kind":"none","message":"m"}'
        with mock.patch.object(resolve.subprocess, "run", return_value=self._proc(raw)):
            j = resolve.judge_reply(_finding(), "fix it", "diff", "TEMPLATE")
        self.assertEqual(j["intent"], "agree")

    def test_garbage_output_returns_none(self):
        with mock.patch.object(resolve.subprocess, "run", return_value=self._proc("usage limit reached")):
            self.assertIsNone(resolve.judge_reply(_finding(), "fix it", "diff", "TEMPLATE"))


if __name__ == "__main__":
    unittest.main()
