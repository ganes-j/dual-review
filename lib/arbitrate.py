import json
import re
import subprocess


_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL)
_VERDICTS = ("confirm", "refute", "unsure")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "can",
    "causes", "could", "does", "for", "from", "if", "in", "into", "is",
    "it", "may", "of", "on", "or", "that", "the", "this", "to", "when",
    "will", "with",
}
_CANONICAL = {
    "nil": "null", "none": "null", "nullable": "null", "missing": "null",
    "dereference": "crash", "dereferenced": "crash", "exception": "crash",
    "panic": "crash", "panics": "crash", "fails": "failure", "failed": "failure",
    "incorrect": "wrong", "invalid": "wrong", "erroneous": "wrong",
    "concurrent": "race", "concurrency": "race", "racy": "race",
    "leaked": "leak", "leaking": "leak", "exposed": "exposure",
    "unchecked": "validation", "unvalidated": "validation", "validate": "validation",
}


def _extract_json_array(text):
    """Return the first valid top-level JSON array in model output."""
    text = (text or "").strip()
    candidates = []
    match = _FENCE_RE.search(text)
    if match:
        candidates.append(match.group(1).strip())
    candidates.append(text)
    left, right = text.find("["), text.rfind("]")
    if left != -1 and right > left:
        candidates.append(text[left:right + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(data, list):
            return data
    return None


def normalize_verdicts(raw_text, finding_count=None):
    """Validate refute output; malformed output is equivalent to no verdict.

    Invalid individual entries are dropped, as are duplicate verdicts for an
    index: contradictory or repeated answers are not safe evidence for a tier.
    """
    array = _extract_json_array(raw_text)
    if array is None:
        return []
    candidates = []
    counts = {}
    for item in array:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        verdict = item.get("verdict")
        reason = item.get("reason")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            continue
        if finding_count is not None and index >= finding_count:
            continue
        if verdict not in _VERDICTS or reason in (None, ""):
            continue
        normalized = {"index": index, "verdict": verdict, "reason": str(reason)}
        candidates.append(normalized)
        counts[index] = counts.get(index, 0) + 1
    return [item for item in candidates if counts[item["index"]] == 1]


def _tokens(finding):
    text = "{} {}".format(finding.get("title", ""), finding.get("body", "")).lower()
    words = re.findall(r"[a-z0-9_]+", text)
    return {_CANONICAL.get(word, word) for word in words
            if word not in _STOP_WORDS and len(word) > 2}


def _pair_score(left, right):
    """Score a plausible semantic match; zero means there is insufficient evidence."""
    if left.get("file") != right.get("file"):
        return 0.0
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    common = left_tokens & right_tokens
    if not common:
        return 0.0
    overlap = len(common) / float(min(len(left_tokens), len(right_tokens)) or 1)
    left_line, right_line = left.get("line"), right.get("line")
    if isinstance(left_line, int) and isinstance(right_line, int):
        distance = abs(left_line - right_line)
        proximity = 1.0 if distance <= 3 else 0.7 if distance <= 8 else 0.3 if distance <= 20 else 0.0
    else:
        proximity = 0.4
    # Nearby reports can use quite different wording, but still need at least
    # one meaningful concept in common. Far-apart reports require strong text.
    if (proximity >= 0.7 and overlap >= 0.2) or overlap >= 0.6:
        return overlap + proximity
    return 0.0


def _verdict_map(verdicts):
    """Accept already-normalized verdict arrays and reject malformed entries."""
    if not isinstance(verdicts, list):
        return {}
    valid = []
    counts = {}
    for item in verdicts:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        verdict = item.get("verdict")
        if (isinstance(index, bool) or not isinstance(index, int) or index < 0
                or verdict not in _VERDICTS):
            continue
        valid.append((index, verdict))
        counts[index] = counts.get(index, 0) + 1
    return {index: verdict for index, verdict in valid if counts[index] == 1}


def tier_findings(claude_findings, codex_findings, refute_verdicts=None):
    """Dedupe cross-model findings and assign confidence tiers.

    ``refute_verdicts`` maps the reviewing model name to its verdict list:
    ``codex`` verdicts address Claude findings and ``claude`` verdicts address
    Codex findings. Returns ``(tiered_findings, match_log)``.
    """
    claude_findings = list(claude_findings or [])
    codex_findings = list(codex_findings or [])
    if not claude_findings and not codex_findings:
        return [], []

    candidates = {}
    reverse = {}
    for ci, claude_finding in enumerate(claude_findings):
        for xi, codex_finding in enumerate(codex_findings):
            score = _pair_score(claude_finding, codex_finding)
            if score:
                candidates.setdefault(ci, []).append((xi, score))
                reverse.setdefault(xi, []).append((ci, score))

    matches = {}
    matched_codex = set()
    log = []
    ambiguous_claude = {ci for ci, pairs in candidates.items() if len(pairs) > 1}
    ambiguous_codex = {xi for xi, pairs in reverse.items() if len(pairs) > 1}
    for ci, pairs in candidates.items():
        xi = pairs[0][0]
        if len(pairs) == 1 and len(reverse.get(xi, [])) == 1:
            matches[ci] = xi
            matched_codex.add(xi)

    ambiguous_pairs = sorted(
        (ci, xi) for ci, pairs in candidates.items() for xi, _score in pairs
        if ci in ambiguous_claude or xi in ambiguous_codex
    )
    if ambiguous_pairs:
        log.append({"kind": "ambiguous", "pairs": ambiguous_pairs})

    verdicts = refute_verdicts if isinstance(refute_verdicts, dict) else {}
    codex_on_claude = _verdict_map(verdicts.get("codex"))
    claude_on_codex = _verdict_map(verdicts.get("claude"))
    output = []

    for index, finding in enumerate(claude_findings):
        item = dict(finding)
        if index in matches:
            item["tier"] = "high"
        else:
            verdict = codex_on_claude.get(index)
            item["tier"] = ("high" if verdict == "confirm" else
                            "disagreement" if verdict == "refute" else "uncertain")
            if index not in ambiguous_claude:
                log.append({"kind": "unmatched", "model": "claude", "index": index})
        output.append(item)

    for index, finding in enumerate(codex_findings):
        if index in matched_codex:
            continue
        item = dict(finding)
        verdict = claude_on_codex.get(index)
        item["tier"] = ("high" if verdict == "confirm" else
                        "disagreement" if verdict == "refute" else "uncertain")
        output.append(item)
        if index not in ambiguous_codex:
            log.append({"kind": "unmatched", "model": "codex", "index": index})
    return output, log


# A concise public name for callers that treat this module as the arbitration step.
arbitrate = tier_findings


def _build_prompt(template, findings):
    payload = json.dumps(findings, ensure_ascii=False, sort_keys=True)
    return "{}\n\n<untrusted-findings>\n{}\n</untrusted-findings>\n".format(template, payload)


def refute_with_claude(other_findings, template, claude_bin="claude"):
    proc = subprocess.run(
        [claude_bin, "-p", _build_prompt(template, other_findings)],
        capture_output=True, text=True,
    )
    return normalize_verdicts(proc.stdout, len(other_findings))


def refute_with_codex(other_findings, template, repo_dir, codex_bin="codex"):
    proc = subprocess.run(
        [codex_bin, "exec", "-s", "read-only", "--skip-git-repo-check",
         "-C", repo_dir, _build_prompt(template, other_findings)],
        capture_output=True, text=True, input="",
    )
    return normalize_verdicts(proc.stdout, len(other_findings))


def cross_refute(claude_findings, codex_findings, template, repo_dir):
    """Have each model assess only the other model's independent findings."""
    return {
        "claude": refute_with_claude(codex_findings, template),
        "codex": refute_with_codex(claude_findings, template, repo_dir),
    }
