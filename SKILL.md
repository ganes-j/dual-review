---
name: dual-review
description: Cross-model (Claude + Codex) adversarial PR reviewer + resolution loop — a local, keyless launchd watcher for personal GitHub repos. Reviews each new PR with both models, cross-refutes their findings, and posts a confidence-grouped review. Use to manage, run, or configure dual-review; not auto-invoked.
---

# dual-review

A local, keyless watcher that reviews PRs on your personal repos with **both Claude and Codex**, has each model refute the other's findings, and posts a single confidence-grouped review (inline comments only for findings both models back). No API keys — it uses your existing `gh`, `claude`, and `codex` logins. The auto watcher never touches denied (e.g. work) repos — the fail-closed scope gate blocks them. A specific PR on a denied repo can be reviewed only by an explicit, opt-in **manual run** (see below).

## The on-switch is yours

Building the tool does **not** turn it on. It only reviews PRs once you load the launchd agent below. Until then nothing runs. Enabling it means it will autonomously post reviews on every PR in the repos listed under `watch` in `config/allowlist.json` — turn it on deliberately.

## Configure

`config/allowlist.json` (copy from `config/allowlist.example.json`; it's gitignored so your repo list stays local):
- `watch` — the repos it polls each tick. Add repos here when you want them reviewed.
- `allow` — the safety gate (owner globs, e.g. `your-username/*`). A watched repo must also pass this.
- `deny` — hard-denied owners (e.g. `your-work-org`). Deny always wins on the auto path. A missing/empty allow or deny list fails closed (nothing runs).
- `manual_allow` — exact `owner/name` repos (no globs) reachable **only** by an explicit manual run naming the exact repo and PR. Never polled, never in the auto candidate set. This is how a specific denied-owner PR is reviewed on demand; `deny` still blocks that same repo on the auto path.

## Run manually

```bash
python3 lib/orchestrator.py --once            # one tick over the watch list
python3 lib/orchestrator.py --once --dry-run  # discover + log candidates; no model calls, no posting
python3 lib/orchestrator.py --repo your-username/dual-review        # scope one run to a single repo (all open PRs)
python3 lib/orchestrator.py --repo your-username/dual-review --pr 1  # review exactly PR #1
python3 lib/orchestrator.py --repo your-username/dual-review --pr 1 --force 1   # re-review PR #1 even if already marked
```

## Manual review of a manual_allow repo

Add the exact `owner/name` to `manual_allow` in `config/allowlist.json` first, then:

```bash
python3 lib/orchestrator.py --repo your-work-org/foo --pr 42            # Claude-only (default)
python3 lib/orchestrator.py --repo your-work-org/foo --pr 42 --codex    # also run the Codex (OpenAI) lane
```

A `manual_allow` repo is never auto-polled and requires an explicit `--pr`. It defaults to **Claude-only**; `--codex` opts that run into the Codex (OpenAI) lane. Every manual review is recorded to `state/manual-audit.log` (`timestamp · repo#pr · lanes`) before the review runs. Sending a denied repo's diff to the Codex lane means that source leaves your machine for OpenAI — only enable `--codex` on a work repo if your org sanctions that egress; the Claude-only default keeps it deliberate.

## Enable the scheduled watcher (your call)

```bash
cp launchd/com.dual-review.watch.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dual-review.watch.plist
launchctl list | grep dual-review        # status
```

Ticks every 5 min (StartInterval), logging to `state/watch.log` (mode 600, secret-scrubbed, rotated). A failed tick fires a macOS notification instead of stopping silently.

## After the review: the resolution loop (Phase B)

Every tick also advances a **pass 2** over already-reviewed, open PRs. When you (or a
contributor) reply to one of the tool's inline finding comments, the next tick:

1. **Judges intent** (agree / dismiss / question) and **re-verifies** the finding against
   the *current* diff with a single Claude pass — the code may have changed since the review.
2. Acts, and **never commits**:
   - **agree + still valid** → drafts a minimal fix as a native GitHub `suggestion` block
     (or a text-patch reply when a suggestion can't express it) for **you** to click
     "Commit suggestion". Fixes on workflow/CI, secret, or auth-sensitive paths are *not*
     auto-drafted — you get a note that a human must write them.
   - **no longer valid** → posts pushback and drafts nothing, even on an explicit "fix it".
   - **dismiss** → leaves the thread for you to resolve.
   - **question** → posts a short clarification.

Replies are treated as untrusted data (a crafted reply can't steer the judge into drafting
on a dismissed or invalid finding). Each reply is handled once (tracked in `state/`), resolved/
outdated threads are dropped, and a reply the judge couldn't score (e.g. a usage-cap tick) is
left unprocessed and retried next tick — never a partial post. Drafted fixes are labeled
unverified LLM output; all posted content is scrubbed of secret-shaped strings.

## Disable

```bash
launchctl bootout gui/$(id -u)/com.dual-review.watch
rm ~/Library/LaunchAgents/com.dual-review.watch.plist
```

## Notes

- Reviews run only while the Mac is on; a PR opened and merged while it is off is not reviewed (a still-open PR is caught on the next tick — discovery is a reconciliation sweep, not event-detection).
- Idempotent per PR: a PR carrying the tool's own done-marker is not re-reviewed (re-run with `--force`). Reply handling is idempotent per comment id.
- `--dry-run` logs both the PRs it would review (pass 1) and the replies it would resolve (pass 2) without calling any model or posting.
- Stdlib only (Python 3); `unittest` suite under `tests/`.
