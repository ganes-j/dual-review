def marker(pr):
    """The hidden done-marker embedded in the tool's review body for a PR."""
    return "<!-- dual-review reviewed pr={} -->".format(pr)


def has_tool_review(reviews, pr, tool_login):
    """True iff a review authored by `tool_login` carries this PR's done-marker.

    Keyed per-PR (not per-SHA), so a later push does not clear the marker.
    Only the tool's own reviews count, so a marker string pasted by anyone
    else does not suppress a review.
    """
    m = marker(pr)
    for rev in reviews:
        author = (rev.get("user") or {}).get("login")
        body = rev.get("body") or ""
        if author == tool_login and m in body:
            return True
    return False


def select_candidates(open_prs, reviews_by_pr, tool_login, force_pr=None):
    """Return the open PRs that still need a first review.

    A reconciliation sweep over already-fetched open PRs: any PR without a
    tool-authored done-marker is a candidate. `force_pr` bypasses the marker
    for exactly that PR (manual re-trigger); it never widens to other PRs.
    """
    candidates = []
    for pr in open_prs:
        num = pr.get("number")
        if force_pr is not None and num == force_pr:
            candidates.append(pr)
            continue
        reviews = reviews_by_pr.get(num, [])
        if not has_tool_review(reviews, num, tool_login):
            candidates.append(pr)
    return candidates
