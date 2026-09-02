import json
import re
import subprocess

import hermes_runner
from scrub import scrub_secrets

_INTENTS = ("agree", "dismiss", "question")
_FIX_KINDS = ("suggestion", "patch", "none")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)
_ROOT_RE = re.compile(r"^\*\*\[(.*?)\]\*\*\s*(.*?)\n\n(.*)$", re.DOTALL)

_UNVERIFIED = "⚠️ **Unverified LLM-drafted fix** — review before committing."
_PUSHBACK = ("↩️ **Re-verified: this finding no longer holds against the current diff.** "
             "No fix drafted.")
_BLOCKED = ("\U0001f512 **Sensitive path.** This finding touches a workflow/CI, secret, or "
            "auth-sensitive file, so no auto-suggestion is drafted — a human must write and "
            "commit any fix.")

_SENSITIVE_SEGMENTS = {
    "secret", "secrets", "credential", "credentials", "password", "passwords",
    "token", "tokens", "auth", "authentication", "authorization", "env",
    "key", "keys", "pem", "cert", "certs", "keystore", "id_rsa", "id_dsa",
}


def is_sensitive_path(path):
    """True for workflow/CI, secret, and auth-sensitive paths (no auto-suggestion there)."""
    if not path:
        return False
    low = path.lower()
    if ".github/workflows/" in low or low.startswith(".github/"):
        return True
    segments = [s for s in re.split(r"[/._\-]", low) if s]
    return any(s in _SENSITIVE_SEGMENTS for s in segments)


def recover_finding(root_comment):
    """Reconstruct the finding a review-thread's tool-authored root comment describes."""
    body = root_comment.get("body") or ""
    m = _ROOT_RE.match(body)
    if m:
        model, title, detail = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    else:
        first, _, rest = body.partition("\n")
        model, title, detail = "", first.strip(), rest.strip()
    return {
        "file": root_comment.get("path"),
        "line": root_comment.get("line"),
        "title": title,
        "body": detail,
        "model": model,
    }


def _extract_json_object(text):
    """Return the first valid top-level JSON object in model output, or None."""
    text = (text or "").strip()
    candidates = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(text)
    lb, rb = text.find("{"), text.rfind("}")
    if lb != -1 and rb > lb:
        candidates.append(text[lb:rb + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def normalize_judgment(raw_text):
    """Validate judge output against the schema; return None on anything untrustworthy.

    None means "could not judge" — the caller defers and does NOT mark the reply
    processed, so a usage-cap / rate-limit reply is safely retried next tick.
    """
    obj = _extract_json_object(raw_text)
    if obj is None:
        return None
    intent = obj.get("intent")
    still_valid = obj.get("still_valid")
    if intent not in _INTENTS or not isinstance(still_valid, bool):
        return None
    fix_kind = obj.get("fix_kind")
    if fix_kind not in _FIX_KINDS:
        fix_kind = "none"
    suggestion = obj.get("suggestion") if isinstance(obj.get("suggestion"), str) else ""
    patch = obj.get("patch") if isinstance(obj.get("patch"), str) else ""
    message = obj.get("message") if isinstance(obj.get("message"), str) else ""
    # A fix can never ride on an invalid finding, and an empty draft is no draft.
    if not still_valid:
        fix_kind, suggestion, patch = "none", "", ""
    if fix_kind == "suggestion" and not suggestion.strip():
        fix_kind = "none"
    if fix_kind == "patch" and not patch.strip():
        fix_kind = "none"
    return {"intent": intent, "still_valid": still_valid, "fix_kind": fix_kind,
            "message": message, "suggestion": suggestion, "patch": patch}


def _action(name, post_body, mark_processed, fix_kind="none"):
    return {"action": name, "post_body": post_body, "mark_processed": mark_processed,
            "fix_kind": fix_kind}


def decide_action(judgment, finding):
    """Pure decision: re-verification gates every outcome; an untrusted reply can't override it.

    Precedence: cannot-judge -> defer (no state write); dismiss -> no-op; finding no
    longer valid -> pushback (even on an explicit "fix it"); question -> clarify;
    agree + valid -> draft (blocked to a human note on sensitive paths).
    """
    if judgment is None:
        return _action("defer", None, False)

    message = judgment.get("message", "")
    if judgment["intent"] == "dismiss":
        return _action("none", None, True)

    if not judgment["still_valid"]:
        return _action("pushback", scrub_secrets(_PUSHBACK + "\n\n" + message), True)

    if judgment["intent"] == "question":
        return _action("clarify", scrub_secrets(message), True)

    # intent == "agree" and the finding still holds.
    fix_kind = judgment["fix_kind"]
    if fix_kind == "none":
        return _action("clarify", scrub_secrets(message), True)

    if is_sensitive_path(finding.get("file")):
        return _action("blocked", scrub_secrets(_BLOCKED + "\n\n" + message), True)

    if fix_kind == "suggestion":
        body = "{}\n\n{}\n\n```suggestion\n{}\n```".format(_UNVERIFIED, message, judgment["suggestion"])
        return _action("draft", scrub_secrets(body), True, fix_kind="suggestion")

    body = "{} (out-of-hunk — not a one-click suggestion; apply manually)\n\n{}\n\n```diff\n{}\n```".format(
        _UNVERIFIED, message, judgment["patch"])
    return _action("draft", scrub_secrets(body), True, fix_kind="patch")


def select_actionable(threads, tool_login, processed_ids):
    """Unprocessed author replies on open, tool-rooted review threads.

    Skips resolved/outdated threads, threads the tool did not open, the tool's own
    replies, and any reply already recorded in state.
    """
    processed = set(processed_ids or [])
    out = []
    for thread in threads or []:
        if thread.get("isResolved") or thread.get("isOutdated"):
            continue
        comments = thread.get("comments") or []
        if not comments:
            continue
        root = comments[0]
        if root.get("author") != tool_login:
            continue
        finding = recover_finding(root)
        for comment in comments[1:]:
            if comment.get("author") == tool_login:
                continue
            if comment.get("id") in processed:
                continue
            out.append({
                "root_id": root.get("id"),
                "reply_id": comment.get("id"),
                "reply_body": comment.get("body") or "",
                "finding": finding,
            })
    return out


def _build_judge_prompt(template, finding, reply_body, diff):
    finding_json = json.dumps(
        {k: finding.get(k) for k in ("file", "line", "title", "body")},
        ensure_ascii=False, sort_keys=True)
    return (
        "{}\n\n<original-finding>\n{}\n</original-finding>\n\n"
        "<untrusted-author-reply>\n{}\n</untrusted-author-reply>\n\n"
        "<untrusted-diff>\n{}\n</untrusted-diff>\n"
    ).format(template, finding_json, reply_body, diff)


def judge_reply(finding, reply_body, diff, template, claude_bin="claude",
                primary_runner="claude", hermes_model=None):
    """Run the configured primary judge; None on any unparseable output (defer)."""
    prompt = _build_judge_prompt(template, finding, reply_body, diff)
    if primary_runner == "hermes":
        return normalize_judgment(hermes_runner.run(prompt, model=hermes_model))
    try:
        proc = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    return normalize_judgment(getattr(proc, "stdout", "") or "")
