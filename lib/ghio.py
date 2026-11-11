import json
import subprocess


class GhError(RuntimeError):
    pass


def _run(args):
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(" ".join(args) + ": " + (proc.stderr or "").strip())
    return proc.stdout


def gh_login():
    """The authenticated gh account login (used to identify the tool's own reviews)."""
    return _run(["gh", "api", "user", "--jq", ".login"]).strip()


def list_open_prs(repo):
    out = _run([
        "gh", "pr", "list", "-R", repo, "--state", "open",
        "--json", "number,headRefOid,author,title", "--limit", "100",
    ])
    return json.loads(out)


def list_owner_repos(owner):
    out = _run([
        "gh", "repo", "list", owner, "--no-archived",
        "--json", "nameWithOwner", "--limit", "1000",
    ])
    return [r["nameWithOwner"] for r in json.loads(out)]


def list_reviews(repo, pr):
    out = _run(["gh", "api", "repos/{}/pulls/{}/reviews".format(repo, pr)])
    return json.loads(out)


def pr_diff(repo, pr):
    return _run(["gh", "pr", "diff", str(pr), "-R", repo])


_THREADS_QUERY = """
query($owner:String!,$name:String!,$pr:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100){
        nodes{
          isResolved isOutdated
          comments(first:100){
            nodes{ databaseId author{login} body path line }
          }
        }
      }
    }
  }
}
"""


def list_review_threads(repo, pr):
    """Normalized review threads for a PR: [{isResolved, isOutdated, comments:[...]}].

    Each comment is {id, author, body, path, line}. Reading threads (not the flat
    comments list) is what surfaces isResolved/isOutdated so the loop can stop
    tracking a settled thread.
    """
    owner, _, name = repo.partition("/")
    out = _run([
        "gh", "api", "graphql",
        "-f", "query=" + _THREADS_QUERY,
        "-f", "owner=" + owner, "-f", "name=" + name, "-F", "pr=" + str(pr),
    ])
    data = json.loads(out)
    nodes = (((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
    threads = (nodes.get("reviewThreads") or {}).get("nodes") or []
    result = []
    for thread in threads:
        comments = []
        for c in ((thread.get("comments") or {}).get("nodes") or []):
            comments.append({
                "id": c.get("databaseId"),
                "author": (c.get("author") or {}).get("login"),
                "body": c.get("body") or "",
                "path": c.get("path"),
                "line": c.get("line"),
            })
        result.append({
            "isResolved": bool(thread.get("isResolved")),
            "isOutdated": bool(thread.get("isOutdated")),
            "comments": comments,
        })
    return result


def reply_to_thread(repo, pr, comment_id, body):
    """Post a reply into an existing review-comment thread (never a new top-level comment).

    Replying to the thread's root comment inherits its line anchor, so a ```suggestion```
    block renders with GitHub's one-click 'Commit suggestion' button.
    """
    payload = {"body": body}
    proc = subprocess.run(
        ["gh", "api",
         "repos/{}/pulls/{}/comments/{}/replies".format(repo, pr, comment_id),
         "-X", "POST", "--input", "-"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise GhError("gh reply failed: " + (proc.stderr or "").strip())
    return json.loads(proc.stdout or "{}")
