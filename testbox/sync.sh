#!/usr/bin/env bash
# Bring the testbox back in line with the repo, and SAY SO if it has drifted.
#
# Why this exists: every fix applied by hand to a running box is an undocumented step. The box
# keeps working, the repo does not gain the fix, and eventually the only machine on earth where
# this system runs is one nobody can rebuild. That is a snowflake, and a snowflake is a lie about
# reproducibility.
#
# So: the REPO is the source of truth. This script makes the box match it, and refuses to do so
# silently if the box contains work the repo does not.
#
#   ./testbox/sync.sh          check for drift, then sync (refuses if drift would be LOST)
#   ./testbox/sync.sh --force  sync anyway, discarding local changes
#   ./testbox/sync.sh --check  report drift and exit; change nothing

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf '\033[36m[sync]\033[0m %s\n' "$1"; }
warn() { printf '\033[33m[sync] DRIFT:\033[0m %s\n' "$1"; }
die()  { printf '\033[31m[sync] STOPPED:\033[0m %s\n' "$1" >&2; exit 1; }

MODE="${1:-}"

# ---------------------------------------------------------------------------
# 1. DRIFT CHECK — does the box hold anything the repo doesn't?
#
# Fail loud, not silent. A sync that quietly discards a fix someone made on the box is worse
# than no sync: the fix disappears and nobody knows it existed.
# ---------------------------------------------------------------------------
git fetch -q origin

DIRTY="$(git status --porcelain | grep -vE '^\?\? (instance\.yaml|\.env|\.venv/|mission_schema\.sql|evidence/)' || true)"
BEHIND="$(git rev-list --count HEAD..origin/main)"
AHEAD="$(git rev-list --count origin/main..HEAD)"

if [ -n "$DIRTY" ]; then
  warn "the box has uncommitted changes the repo does not have:"
  echo "$DIRTY" | sed 's/^/        /'
  echo
  [ "$MODE" = "--force" ] || die "refusing to discard them. Commit them to the repo, or re-run with --force.
   These are exactly the fixes that get lost and then have to be rediscovered."
fi

if [ "$AHEAD" -gt 0 ]; then
  warn "the box is $AHEAD commit(s) AHEAD of origin/main — it has work the repo does not:"
  git log --oneline origin/main..HEAD | sed 's/^/        /'
  [ "$MODE" = "--force" ] || die "push them, or re-run with --force to discard."
fi

if [ "$MODE" = "--check" ]; then
  [ -z "$DIRTY" ] && [ "$AHEAD" -eq 0 ] && say "no drift. The box matches the repo."
  say "behind origin/main by $BEHIND commit(s)"
  exit 0
fi

# ---------------------------------------------------------------------------
# 2. SYNC
# ---------------------------------------------------------------------------
say "syncing to origin/main (was $BEHIND behind)"
git reset -q --hard origin/main
say "now at $(git rev-parse --short HEAD) — $(git log -1 --format=%s | head -c 60)"

# ---------------------------------------------------------------------------
# 3. Re-apply the schema. Migrations are idempotent; running them again is how a synced box
#    picks up a new table without anyone remembering to.
# ---------------------------------------------------------------------------
if [ -f .env ]; then
  set -a; . ./.env; set +a
  for f in schema/*.sql; do
    psql "$AQ_DB_URL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>&1 \
      && say "schema applied: $f" \
      || warn "schema $f did not apply cleanly — check it by hand"
  done
fi

# ---------------------------------------------------------------------------
# 4. Restart what runs from the code we just changed. Source-landed is NOT live-running:
#    a long-lived process keeps serving the OLD code until it is restarted.
# ---------------------------------------------------------------------------
if pgrep -f "aq.py ui" >/dev/null; then
  pkill -f "aq.py ui" || true
  nohup ./.venv/bin/python aq.py ui >/tmp/ui.log 2>&1 &
  sleep 2
  pgrep -f "aq.py ui" >/dev/null && say "UI restarted on the new code" || warn "UI did not come back up"
fi

say "in sync."
