import json
import os
import sys


def _empty():
    return {"reviewed": {}, "processed_replies": []}


def load(state_path):
    """Load state; return empty state on a missing or corrupt file (never raise)."""
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError):
        sys.stderr.write("dual-review: state file unreadable/corrupt; starting from empty state\n")
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    data.setdefault("reviewed", {})
    data.setdefault("processed_replies", [])
    return data


def save(state_path, state):
    parent = os.path.dirname(state_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)


def _pr_key(repo, pr):
    return "{}#{}".format(repo, pr)


def is_reviewed(state, repo, pr):
    return _pr_key(repo, pr) in state.get("reviewed", {})


def mark_reviewed(state, repo, pr, meta=None):
    state.setdefault("reviewed", {})[_pr_key(repo, pr)] = meta or {}


def is_reply_processed(state, comment_id):
    return comment_id in state.get("processed_replies", [])


def mark_reply_processed(state, comment_id):
    replies = state.setdefault("processed_replies", [])
    if comment_id not in replies:
        replies.append(comment_id)
