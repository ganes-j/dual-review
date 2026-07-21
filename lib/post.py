import json
import re
import subprocess

from scrub import scrub_secrets

_TIER_ORDER = ("high", "uncertain", "disagreement")
_TIER_LABEL = {
    "high": "High confidence (both models)",
    "uncertain": "Uncertain (single model)",
    "disagreement": "Disagreement (models split)",
}
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def marker(pr):
    return "<!-- dual-review reviewed pr={} -->".format(pr)


def group_by_tier(findings):
    grouped = {t: [] for t in _TIER_ORDER}
    for f in findings or []:
        tier = f.get("tier")
        grouped[tier if tier in grouped else "uncertain"].append(f)
    return grouped


def _loc(f):
    loc = f.get("file", "?")
    if f.get("line") is not None:
        loc += ":" + str(f["line"])
    return loc


def build_summary_body(grouped, pr):
    lines = ["## dual-review", ""]
    total = sum(len(grouped[t]) for t in _TIER_ORDER)
    if total == 0:
        lines.append("No findings. Both models came up clean.")
    else:
        for t in _TIER_ORDER:
            items = grouped[t]
            if not items:
                continue
            lines.append("### {} ({})".format(_TIER_LABEL[t], len(items)))
            for f in items:
                lines.append("- `{}` — {} ({})".format(_loc(f), f.get("title", ""), f.get("model", "")))
            lines.append("")
    lines.append(marker(pr))
    return "\n".join(lines)


def valid_diff_lines(diff):
    """Set of (path, new_line_number) commentable on the RIGHT side of a unified diff."""
    valid = set()
    path = None
    new_line = None
    for raw in (diff or "").splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            path = None if p == "/dev/null" else p
            new_line = None
        elif raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            new_line = int(m.group(1)) if m else None
        elif new_line is not None and path is not None:
            if raw.startswith("+"):
                valid.add((path, new_line))
                new_line += 1
            elif raw.startswith("-") or raw.startswith("\\"):
                continue
            else:
                valid.add((path, new_line))
                new_line += 1
    return valid


def build_inline_comments(grouped, valid_lines):
    """Only high-confidence findings on a valid RIGHT-side diff line become inline comments.

    Findings whose line is not in the diff fall back to the summary body (they are
    already listed there), so a single out-of-diff line can never 422 the review.
    """
    comments = []
    for f in grouped.get("high", []):
        path, line, side = f.get("file"), f.get("line"), f.get("side", "RIGHT")
        if line is not None and side == "RIGHT" and (path, line) in valid_lines:
            comments.append({
                "path": path,
                "line": line,
                "side": "RIGHT",
                "body": "**[{}]** {}\n\n{}".format(f.get("model", ""), f.get("title", ""), f.get("body", "")),
            })
    return comments


def build_review_payload(findings, pr, diff):
    grouped = group_by_tier(findings)
    comments = build_inline_comments(grouped, valid_diff_lines(diff))
    for c in comments:
        c["body"] = scrub_secrets(c["body"])
    payload = {"body": scrub_secrets(build_summary_body(grouped, pr)), "event": "COMMENT"}
    if comments:
        payload["comments"] = comments
    return payload


def submit_review(repo, pr, payload):
    proc = subprocess.run(
        ["gh", "api", "repos/{}/pulls/{}/reviews".format(repo, pr), "-X", "POST", "--input", "-"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("gh review submit failed: " + (proc.stderr or "").strip())
    return json.loads(proc.stdout or "{}")
