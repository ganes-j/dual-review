#!/bin/bash
# launchd entry for dual-review: run ONE orchestrator tick, then exit.
# launchd owns the interval (StartInterval in the plist). This script sets an
# explicit PATH because a launchd agent does NOT inherit the login-shell PATH,
# so gh/claude/codex/hermes (in Homebrew, Volta, or Hermes' standard venv) would
# otherwise be unresolvable. Logs to a mode-600 file, scrubs secret-shaped strings, rotates,
# and alerts loudly on failure instead of stopping silently.
export PATH="$HOME/.local/bin:$HOME/.hermes/hermes-agent/venv/bin:/opt/homebrew/bin:$HOME/.volta/bin:/usr/bin:/bin:/usr/sbin:/sbin"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/state/watch.log"

mkdir -p "$ROOT/state"
touch "$LOG"
chmod 600 "$LOG"
if [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then mv -f "$LOG" "$LOG.1"; fi

{
  echo "=== tick $(date '+%Y-%m-%dT%H:%M:%S') ==="
  python3 "$ROOT/lib/orchestrator.py" "$@" 2>&1 \
    | sed -E 's/(gh[opsu]_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]+|xai-[A-Za-z0-9_-]+)/[REDACTED]/g'
  rc=${PIPESTATUS[0]}
  echo "tick rc=$rc"
  if [ "$rc" -ne 0 ]; then
    /usr/bin/osascript -e 'display notification "dual-review tick failed (rc='"$rc"') — see state/watch.log" with title "dual-review"' 2>/dev/null || true
  fi
} >> "$LOG" 2>&1
