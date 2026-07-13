#!/usr/bin/env bash
# verify.sh — proves the system is ALIVE, not merely INSTALLED.
#
# The whole point of this file:
#   "Installed" is not "done". A TURNING LOOP is done.
#
# Every check below reads GROUND TRUTH — a row in the database, a real model call, a real
# cycle that actually completed. None of them can be satisfied by a log line that claims
# success, a health endpoint that returns 200 because the process is up, or a status field
# something else wrote. If this script passes, the loop turned. If the loop did not turn,
# this script fails, loudly, and the bootstrap is NOT finished.
#
# Do not "fix" a failure here by relaxing a check. A relaxed check is worse than no check:
# it reports green on a dead system, and a system that lies about being alive is the one
# thing an autonomous operator must never be.

set -euo pipefail

FAIL=0

# shellcheck source=scripts/_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=1; }
info() { printf '        %s\n' "$1"; }

if [ -z "${AQ_DB_URL:-}" ]; then
  echo "AQ_DB_URL is not set, and no .env was found next to this repo." >&2
  echo "  Run ./install.sh first — it creates .env with the database URL." >&2
  exit 78   # EX_CONFIG
fi

psql_q() { psql "$AQ_DB_URL" -tAX -c "$1"; }

echo
echo "autonomy-quest — liveness verification"
echo "======================================"
echo

# ---------------------------------------------------------------------------
# 1. The datastore answers.
# ---------------------------------------------------------------------------
echo "[1/4] Datastore"
if [ "$(psql_q 'select 1' 2>/dev/null)" = "1" ]; then
  pass "Postgres answers a query"
else
  fail "Postgres did not answer. The system has nowhere to remember anything."
fi

# Graph layer, if the interview selected AGE.
if [ "${AQ_GRAPH:-none}" = "age" ]; then
  if psql_q "select count(*) from pg_extension where extname='age'" 2>/dev/null | grep -q '^1$'; then
    pass "Apache AGE extension is present (graph + relational in one database)"
  else
    fail "AGE was selected in the interview but the extension is not installed."
  fi
fi

# ---------------------------------------------------------------------------
# 2. The model gateway completes a REAL call.
#
# Not "the key is set". Not "the gateway process is running". A real completion,
# round-tripped, with a token count we can point at. A gateway that is up but cannot
# reach a model is a gateway that will stall the loop on its first turn.
# ---------------------------------------------------------------------------
echo
echo "[2/4] Executor — can the loop actually get a model to answer?"
if RESP="$(./scripts/model_ping.sh 2>/dev/null)" && [ -n "$RESP" ]; then
  pass "Executor completed a live call — ${RESP}"
else
  fail "The executor could not get a model to answer. Subscription mode: is the agent logged in? API mode: is the key in .env?"
fi

# ---------------------------------------------------------------------------
# 3. THE ONE THAT MATTERS: the loop has completed at least one FULL cycle.
#
# A full cycle means all four of these happened and are on disk:
#   - it decided to do something   (a run row exists)
#   - it did it                    (that run has an outcome, not just a 'started' status)
#   - it recorded what happened    (the outcome is populated)
#   - it learned something         (a learning row references that run)
#
# We require the LEARNING row specifically. A system that acts and records but never
# learns is automation, not evolution — it is the thing this project exists NOT to be.
# ---------------------------------------------------------------------------
echo
echo "[3/4] The loop"

CYCLES="$(psql_q "
  select count(*)
  from runs r
  join learnings l on l.run_id = r.id
  where r.completed_at is not null
    and r.outcome is not null
" 2>/dev/null || echo 0)"

if [ "${CYCLES:-0}" -ge 1 ]; then
  pass "The loop has completed ${CYCLES} full cycle(s): acted → recorded → learned"
  echo
  info "Most recent completed cycle (this is your proof — a row, not a claim):"
  psql "$AQ_DB_URL" -X -c "
    select r.id, r.started_at, r.completed_at, left(r.outcome, 48) as outcome,
           left(l.insight, 48) as learned
    from runs r join learnings l on l.run_id = r.id
    where r.completed_at is not null and r.outcome is not null
    order by r.completed_at desc limit 1;" 2>/dev/null || true
else
  fail "The loop has NEVER completed a full cycle."
  echo
  info "This system is INSTALLED but NOT ALIVE. That is not done."
  info "Do not report success. Diagnose why the loop is not turning:"
  info "    ./aq loop --once --verbose"
  info "Common causes: no work to do (empty mission?), model gateway failing,"
  info "or the loop runner not started (check 'docker ps')."
fi

# ---------------------------------------------------------------------------
# 4. The instance is AIMED. A loop turning at nothing is a very expensive no-op.
# ---------------------------------------------------------------------------
echo
echo "[4/4] Mission"
OBJ="$(python3 -c "import yaml;m=(yaml.safe_load(open('instance.yaml')) or {}).get('mission') or {};print(m.get('objective') or '')" 2>/dev/null || echo "")"
MEAS="$(python3 -c "import yaml;m=(yaml.safe_load(open('instance.yaml')) or {}).get('mission') or {};print((m.get('measure') or {}).get('what') or '')" 2>/dev/null || echo "")"
if [ -n "$OBJ" ] && [ "$OBJ" != "null" ] && [ -n "$MEAS" ] && [ "$MEAS" != "null" ]; then
  pass "Instance is aimed"
  info "objective: ${OBJ}"
  info "measure:   ${MEAS}"
else
  fail "instance.yaml has no mission. The interview was skipped or left incomplete."
  info "An unaimed instance will run happily and accomplish nothing. Go back to interview/01-mission.md."
fi

# ---------------------------------------------------------------------------
echo
echo "======================================"
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mALIVE.\033[0m The loop is turning and it is aimed at the mission.\n'
  echo "Setup is complete. Hand over (setup.md §6)."
  echo
  exit 0
else
  printf '\033[31mNOT ALIVE.\033[0m Setup is NOT complete.\n'
  echo "Tell the human plainly what failed above. Do not report success."
  echo
  exit 1
fi
