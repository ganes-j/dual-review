# dual-review

A local, keyless watcher that reviews pull requests on your personal GitHub repos with **both Claude and Codex**, has each model try to refute the other's findings, and posts a single confidence-grouped review. When you reply to a finding, a second pass judges your reply, re-checks the finding against the current diff, and drafts a committable fix — it never commits on its own.

No API keys. It reuses your existing `gh`, `claude`, and `codex` logins. No CI, no GitHub App — just a launchd agent on your Mac. It never touches denied (e.g. work) repos automatically; a denied repo is reachable only through an explicit, opt-in manual run.

## How it works

Each tick, for every repo in the `watch` list:

1. **Discover** open PRs that don't already carry the tool's done-marker (once-on-open; `--force` re-runs).
2. **Review** the diff with Claude and Codex independently.
3. **Cross-refute** — each model tries to knock down the other's findings.
4. **Tier and post** one review: *high confidence* (both models agree) gets inline comments; *uncertain* (single model) is listed in the summary only.
5. **Resolve** (pass 2) — advance any replies on already-reviewed PRs (see below).

## Requirements

- macOS (launchd) for the scheduled watcher; the CLI runs anywhere with Python 3.
- Authenticated `gh` and `codex` CLIs on your `PATH`, plus either `claude` (the default primary runner) **or** Hermes (see below).
- Python 3 standard library only — no pip dependencies.

## Setup

```bash
git clone <this-repo> dual-review && cd dual-review

# 1. Create your config from the example and edit it (see Configure below).
cp config/allowlist.example.json config/allowlist.json

# 2. Run once manually to confirm your logins work.
python3 lib/orchestrator.py --once --dry-run
```

`config/allowlist.json` is gitignored — your repo list stays local and is never committed. To run the scheduled watcher, also edit the launchd plist paths (see *Enable the scheduled watcher*).

## Configure

`config/allowlist.json` (copied from `config/allowlist.example.json`):

- `watch` — repos polled each tick. Add repos here to have them reviewed.
- `allow` — the safety gate (owner globs, e.g. `your-username/*`). A watched repo must also pass this.
- `deny` — hard-denied owners (e.g. your employer's org). Deny always wins on the auto path. A missing/empty `allow` or `deny` list fails closed (nothing runs).
- `manual_allow` — exact `owner/name` repos (no globs) reachable **only** by an explicit manual run naming the exact repo and PR. Never polled. This is how a specific denied-owner PR is reviewed on demand; `deny` still blocks that repo on the auto path.

## Enable the scheduled watcher

Cloning the tool does **not** turn it on. It only reviews PRs once you load the launchd agent, and then it posts autonomously on every PR in `watch` — so turn it on deliberately.

First edit `launchd/com.dual-review.watch.plist` and replace `/absolute/path/to/dual-review` with your clone's absolute path (three places). Then:

```bash
cp launchd/com.dual-review.watch.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dual-review.watch.plist
launchctl list | grep dual-review        # status
```

Ticks every 5 minutes, logging to `state/watch.log` (mode 600, secret-scrubbed, rotated). A failed tick fires a macOS notification instead of stopping silently.

## Run manually

```bash
python3 lib/orchestrator.py --once            # one tick over the watch list
python3 lib/orchestrator.py --once --dry-run  # discover + log candidates; no model calls, no posting
python3 lib/orchestrator.py --repo your-username/dual-review        # one repo, all open PRs
python3 lib/orchestrator.py --repo your-username/dual-review --pr 1  # exactly PR #1
python3 lib/orchestrator.py --repo your-username/dual-review --pr 1 --force 1   # re-review PR #1 even if already marked
```

`--pr` requires `--repo` (single-PR targeting is scoped to one repo).

## Hermes primary runner (opt-in)

The default remains Claude + Codex. To run Hermes as the primary review, refutation,
and resolution lane, select it explicitly and name its model:

```bash
python3 lib/orchestrator.py --once \
  --primary-runner hermes \
  --hermes-model anthropic/claude-sonnet-4
```

Hermes receives each prompt through stdin in an isolated, safe-mode one-shot session;
project rules, plugins, and MCP servers are not loaded. Its output is schema-validated in
the same way as the Claude output. `--hermes-model` is required deliberately: choose a
model/provider independent from the Codex lane, or the confidence tiers would no longer be
cross-model evidence. Use a model identifier accepted by `hermes chat --help` and available
through your own Hermes authentication; no API key is added by dual-review.

The bundled `bin/watch.sh` includes the usual Hermes install locations (`~/.local/bin` and
`~/.hermes/hermes-agent/venv/bin`) in its launchd PATH.

## Manual review of a manual_allow repo

Add the exact `owner/name` to `manual_allow` in your config first, then:

```bash
python3 lib/orchestrator.py --repo your-work-org/some-repo --pr 42            # Claude-only (default)
python3 lib/orchestrator.py --repo your-work-org/some-repo --pr 42 --codex    # also run the Codex (OpenAI) lane
```

A `manual_allow` repo is never auto-polled and requires an explicit `--pr`. It defaults to **Claude-only**; `--codex` opts that run into the Codex (OpenAI) lane. Every manual review is recorded to `state/manual-audit.log` (`timestamp · repo#pr · lanes`) before the review runs, so a failed review still leaves a trail.

> Sending a denied repo's diff to the Codex lane means that source leaves your machine for OpenAI. Only enable `--codex` on a work repo if your organization sanctions that egress. The default is Claude-only, and every manual review is audited.

## The resolution loop (pass 2)

When you reply to one of the tool's inline finding comments, the next tick:

1. Judges intent (agree / dismiss / question) and re-verifies the finding against the *current* diff.
2. Acts, and **never commits**:
   - agree + still valid → drafts a native GitHub `suggestion` (or a text-patch reply) for you to click "Commit suggestion". Fixes on workflow/CI, secret, or auth-sensitive paths are not auto-drafted — you get a note that a human must write them.
   - no longer valid → posts pushback and drafts nothing, even on an explicit "fix it".
   - dismiss → leaves the thread for you.
   - question → posts a short clarification.

Replies are treated as untrusted data; re-verification gates every action, so a crafted reply can't force a draft on a dismissed or invalid finding. Each reply is handled once. Pass 2 does not run on single-PR (`--pr`) or `manual_allow` runs — those are review-only.

## Safety model

- **Auto path is fail-closed.** Any config problem (missing file, empty `allow`/`deny`, malformed repo) results in nothing running.
- **`deny` is absolute on the auto path** — a denied owner is never auto-reviewed, even if it also appears in `watch` or `manual_allow`.
- **Denied repos are reachable only manually**, by an explicit run naming the exact repo and PR, and only if listed in `manual_allow` (exact names, no owner globs).
- **Codex egress is opt-in.** Manual reviews default to Claude-only; the Codex (OpenAI) lane runs only with `--codex`, and every manual review is audited.
- The loop **never commits** — all fixes land as GitHub suggestions you approve.

## Disable

```bash
launchctl bootout gui/$(id -u)/com.dual-review.watch
rm ~/Library/LaunchAgents/com.dual-review.watch.plist
```

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Standard-library `unittest`, no dependencies.

## Layout

```
lib/          orchestrator, scope gate, discovery, review, arbitration, posting, resolution, audit, scrub
prompts/      review / refute / judge prompt templates
config/       allowlist.example.json (copy to allowlist.json — gitignored, local)
launchd/      com.dual-review.watch.plist
bin/          watch.sh (launchd entrypoint)
tests/        unittest suite
state/        runtime logs + marker state (gitignored)
```
