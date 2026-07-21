import datetime
import os

from scrub import scrub_secrets


def append_manual_review(audit_path, repo, pr, lanes):
    """Append a durable audit line for a manual (manual_allow) review.

    Records that a sanctioned non-watched repo was reviewed and which model
    lanes ran, so Codex egress on such repos has a standing trail independent
    of the launchd watcher log. `lanes` is "claude" or "claude+codex". The line
    is scrubbed of secret-shaped strings, matching the tool's other output.
    """
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = scrub_secrets("%s\t%s#%s\t%s\n" % (ts, repo, pr, lanes))
    with open(audit_path, "a", encoding="utf-8") as fh:
        fh.write(line)
