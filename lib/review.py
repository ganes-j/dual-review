import re
import subprocess

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)
_REQUIRED = ("file", "severity", "title", "body")


def _extract_json_array(text):
    """Pull the first valid top-level JSON array out of model text.

    Tolerates ```json fences and surrounding prose. Returns None when no
    valid array is present — the caller treats that as zero findings, so
    injected instruction text that is not schema JSON is rejected, not obeyed.
    """
    import json
    text = (text or "").strip()
    candidates = []
    m = _FENCE_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(text)
    lb, rb = text.find("["), text.rfind("]")
    if lb != -1 and rb > lb:
        candidates.append(text[lb:rb + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if isinstance(data, list):
            return data
    return None


def normalize_findings(raw_text, model):
    """Validate raw model output against the finding schema; tag by model.

    Malformed output or a non-array yields [] (that model contributed nothing,
    the run continues). Individual items missing required fields are dropped.
    """
    arr = _extract_json_array(raw_text)
    if arr is None:
        return []
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        if not all(item.get(k) not in (None, "") for k in _REQUIRED):
            continue
        raw_line = item.get("line")
        try:
            line = int(raw_line) if raw_line is not None else None
        except (TypeError, ValueError):
            line = None
        side = item.get("side") if item.get("side") in ("LEFT", "RIGHT") else "RIGHT"
        out.append({
            "model": model,
            "file": str(item["file"]),
            "line": line,
            "side": side,
            "severity": str(item["severity"]).lower(),
            "title": str(item["title"]),
            "body": str(item["body"]),
        })
    return out


def _build_prompt(template, diff):
    return "{}\n\n<untrusted-diff>\n{}\n</untrusted-diff>\n".format(template, diff)


def review_with_claude(diff, template, claude_bin="claude"):
    proc = subprocess.run(
        [claude_bin, "-p", _build_prompt(template, diff)],
        capture_output=True, text=True,
    )
    return normalize_findings(proc.stdout, "claude")


def review_with_codex(diff, template, repo_dir, codex_bin="codex"):
    proc = subprocess.run(
        [codex_bin, "exec", "-s", "read-only", "--skip-git-repo-check",
         "-C", repo_dir, _build_prompt(template, diff)],
        capture_output=True, text=True, input="",
    )
    return normalize_findings(proc.stdout, "codex")


def dual_review(diff, repo_dir, template, router_off=False):
    """Run both reviewers independently; skip Codex when the router kill switch is set."""
    findings = review_with_claude(diff, template)
    if not router_off:
        findings += review_with_codex(diff, template, repo_dir)
    return findings
