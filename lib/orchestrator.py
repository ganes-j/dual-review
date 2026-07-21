import argparse
import json
import os
import sys

_LIB = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_LIB)
sys.path.insert(0, _LIB)

import arbitrate  # noqa: E402
import audit  # noqa: E402
import discovery  # noqa: E402
import ghio  # noqa: E402
import post  # noqa: E402
import resolve  # noqa: E402
import review  # noqa: E402
import scope  # noqa: E402
import state  # noqa: E402

CONFIG_PATH = os.path.join(_ROOT, "config", "allowlist.json")
STATE_PATH = os.path.join(_ROOT, "state", "dual-review.json")
MANUAL_AUDIT_PATH = os.path.join(_ROOT, "state", "manual-audit.log")
REVIEW_PROMPT = os.path.join(_ROOT, "prompts", "review.md")
REFUTE_PROMPT = os.path.join(_ROOT, "prompts", "refute.md")
JUDGE_PROMPT = os.path.join(_ROOT, "prompts", "judge.md")
ROUTER_OFF = os.path.expanduser("~/.claude/.router-off")

MAX_REPLIES_PER_PR = 10


def _log(msg):
    sys.stderr.write("[dual-review] " + msg + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def watch_list(config_path):
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    watch = cfg.get("watch")
    return list(watch) if isinstance(watch, list) else []


def review_pr(repo, pr, diff, repo_dir, router_off):
    """Full per-PR pipeline: dual review -> cross-refute -> tier -> post. Returns findings count."""
    review_tmpl = _read(REVIEW_PROMPT)
    refute_tmpl = _read(REFUTE_PROMPT)
    findings = review.dual_review(diff, repo_dir, review_tmpl, router_off=router_off)
    claude_f = [f for f in findings if f.get("model") == "claude"]
    codex_f = [f for f in findings if f.get("model") == "codex"]
    verdicts = {}
    if claude_f and codex_f and not router_off:
        verdicts = arbitrate.cross_refute(claude_f, codex_f, refute_tmpl, repo_dir)
    tiered, _matchlog = arbitrate.tier_findings(claude_f, codex_f, verdicts)
    payload = post.build_review_payload(tiered, pr, diff)
    post.submit_review(repo, pr, payload)
    return len(tiered)


def resolve_pr(repo, pr, login, judge_tmpl, state_path, dry_run=False):
    """Pass 2: advance the resolution loop on one reviewed, open PR. Returns replies handled.

    Judge each unprocessed author reply, re-verify the finding, and post a suggestion,
    pushback, or clarification — never a commit. A reply we could not judge is deferred
    (not marked processed), so a usage-cap tick resumes cleanly next time.
    """
    threads = ghio.list_review_threads(repo, pr)
    st = state.load(state_path)
    actionable = resolve.select_actionable(threads, login, st.get("processed_replies", []))
    if not actionable:
        return 0
    diff = ghio.pr_diff(repo, pr)
    acted = 0
    for item in actionable[:MAX_REPLIES_PER_PR]:
        if dry_run:
            _log("  would resolve reply %s on %s#%s (finding: %s)"
                 % (item["reply_id"], repo, pr, item["finding"].get("file")))
            continue
        judgment = resolve.judge_reply(item["finding"], item["reply_body"], diff, judge_tmpl)
        decision = resolve.decide_action(judgment, item["finding"])
        if decision["action"] == "defer":
            _log("  defer reply %s on %s#%s (could not judge — retry next tick)"
                 % (item["reply_id"], repo, pr))
            continue
        if decision["post_body"]:
            ghio.reply_to_thread(repo, pr, item["root_id"], decision["post_body"])
        if decision["mark_processed"]:
            state.mark_reply_processed(st, item["reply_id"])
            state.save(state_path, st)
        _log("  %s reply %s on %s#%s" % (decision["action"], item["reply_id"], repo, pr))
        acted += 1
    if len(actionable) > MAX_REPLIES_PER_PR:
        _log("  %s#%s: capped at %d replies this tick (%d pending)"
             % (repo, pr, MAX_REPLIES_PER_PR, len(actionable) - MAX_REPLIES_PER_PR))
    return acted


def run_once(config_path=CONFIG_PATH, state_path=STATE_PATH, repo_dir=_ROOT,
             dry_run=False, force_pr=None, only_repo=None, only_pr=None, codex=False):
    manual_run = False
    if only_repo is None:
        repos = watch_list(config_path)
    else:
        if scope.is_allowed(only_repo, config_path):
            repos = [only_repo]
        elif scope.is_manual_allowed(only_repo, config_path):
            if only_pr is None:
                _log("SKIP %s: manual_allow repo requires an explicit --pr" % only_repo)
                return
            repos = [only_repo]
            manual_run = True
        else:
            _log("SKIP %s: repo is neither allowed nor manual_allowed" % only_repo)
            return
    if not repos:
        _log("no repos to watch (config 'watch' empty, or --repo not allowed) — nothing to do")
        return
    login = ghio.gh_login()
    router_off = os.path.exists(ROUTER_OFF)
    if router_off:
        _log("router kill switch present — Codex lane disabled (Claude-only)")
    codex_suppressed = router_off or (manual_run and not codex)
    if manual_run and not router_off:
        _log("manual review of %s — %s" % (
            only_repo, "Codex enabled (--codex)" if codex else "Claude-only (pass --codex for Codex)"))
    try:
        judge_tmpl = _read(JUDGE_PROMPT)
    except OSError:
        judge_tmpl = None
    for repo in repos:
        if only_repo is None and not scope.is_allowed(repo, config_path):
            _log("SKIP %s: scope gate denied (fail-closed)" % repo)
            continue
        open_prs = ghio.list_open_prs(repo)
        reviews_by_pr = {p["number"]: ghio.list_reviews(repo, p["number"]) for p in open_prs}
        candidates = discovery.select_candidates(open_prs, reviews_by_pr, login, force_pr=force_pr)
        if only_pr is not None:
            candidates = [candidate for candidate in candidates
                          if candidate.get("number") == only_pr]
        _log("%s: %d open, %d to review%s" % (repo, len(open_prs), len(candidates),
                                              " [DRY-RUN]" if dry_run else ""))
        for pr_obj in candidates:
            pr = pr_obj.get("number")
            diff = ghio.pr_diff(repo, pr)
            if dry_run:
                _log("  would review %s#%s (%d bytes diff)" % (repo, pr, len(diff)))
                continue
            if manual_run:
                # Record the egress before it happens: if the audit write fails,
                # no Codex/Claude egress occurs without a durable record.
                audit.append_manual_review(
                    MANUAL_AUDIT_PATH, repo, pr,
                    "claude" if codex_suppressed else "claude+codex")
            n = review_pr(repo, pr, diff, repo_dir, codex_suppressed)
            st = state.load(state_path)
            state.mark_reviewed(st, repo, pr, {"findings": n})
            state.save(state_path, st)
            _log("  reviewed %s#%s: %d findings posted" % (repo, pr, n))

        # Pass 2 (Phase B): advance the resolution loop on already-reviewed open PRs.
        # A single-PR run reviews only its target and leaves other PRs untouched (R8);
        # manual_allow runs are review-only (KTD5). Both imply: skip pass 2 when only_pr is set.
        if only_pr is not None:
            continue
        cand_nums = {c.get("number") for c in candidates}
        for pr_obj in open_prs:
            num = pr_obj.get("number")
            if num in cand_nums or judge_tmpl is None:
                continue
            if not discovery.has_tool_review(reviews_by_pr.get(num, []), num, login):
                continue
            handled = resolve_pr(repo, num, login, judge_tmpl, state_path, dry_run=dry_run)
            if handled:
                _log("  advanced %s#%s: %d repl%s handled"
                     % (repo, num, handled, "y" if handled == 1 else "ies"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dual-review")
    ap.add_argument("--once", action="store_true", help="run a single tick (default behavior)")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover and log candidates but do not call models or post")
    ap.add_argument("--repo", help="restrict this run to a single (allowlisted) repo")
    ap.add_argument("--pr", type=int, metavar="PR",
                    help="restrict this run to a single PR number")
    ap.add_argument("--force", type=int, metavar="PR", help="re-review this PR number even if already marked")
    ap.add_argument("--codex", action="store_true",
                    help="enable the Codex lane for a manual_allow repo (default Claude-only)")
    args = ap.parse_args(argv)
    if args.pr is not None and not args.repo:
        ap.error("--pr requires --repo (single-PR targeting is scoped to one repo)")
    run_once(dry_run=args.dry_run, force_pr=args.force, only_repo=args.repo,
             only_pr=args.pr, codex=args.codex)


if __name__ == "__main__":
    main()
