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
echo "[1/5] Datastore"
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
echo "[2/5] Executor — can the loop actually get a model to answer?"
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
echo "[3/5] The loop"

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
  info "    ./aq.py once"
  info "Common causes: no work to do (empty mission?), model gateway failing,"
  info "or the loop runner not started (check 'docker ps')."
fi

# ---------------------------------------------------------------------------
# 4. The instance is AIMED. A loop turning at nothing is a very expensive no-op.
# ---------------------------------------------------------------------------
echo
echo "[4/5] Mission"
OBJ="$(python3 -c "import yaml;m=(yaml.safe_load(open('instance.yaml')) or {}).get('mission') or {};print(m.get('objective') or '')" 2>/dev/null || echo "")"
MEAS="$(python3 -c "import yaml;m=(yaml.safe_load(open('instance.yaml')) or {}).get('mission') or {};print((m.get('measure') or {}).get('what') or '')" 2>/dev/null || echo "")"
MISSION_GATE="$(python3 ./scripts/verify_config.py instance.yaml 2>/dev/null || true)"
MISSION_STATUS="${MISSION_GATE%%|*}"
MISSION_REST="${MISSION_GATE#*|}"
MISSION_CODE="${MISSION_REST%%|*}"
MISSION_DETAIL="${MISSION_REST#*|}"
if [ -z "$OBJ" ] || [ "$OBJ" = "null" ] || [ -z "$MEAS" ] || [ "$MEAS" = "null" ]; then
  fail "instance.yaml has no mission. The interview was skipped or left incomplete."
  info "An unaimed instance will run happily and accomplish nothing. Go back to interview/01-mission.md."
elif [ "$MISSION_STATUS" != "ok" ]; then
  case "$MISSION_CODE" in
    maximize_without_spend_cap)
      fail "goal:maximize has no hard spend cap."
      info "$MISSION_DETAIL"
      info "A maximize goal has no mission ceiling by definition. Give it a hard spend cap before"
      info "verify.sh can accept it as bounded."
      ;;
    missing_ceiling)
      fail "reach_and_maintain measure has NO CEILING."
      info "$MISSION_DETAIL"
      info "This is the exact shape that ran a real instance to 50,082: a measure with no target gets run"
      info "to infinity. Set measure.target (the number that means done) or measure.target_query."
      ;;
    *)
      fail "mission measure is not structurally bounded."
      info "$MISSION_DETAIL"
      ;;
  esac
else
  if [ "$MISSION_CODE" = "maximize_spend_cap" ]; then
    pass "Instance is aimed — goal:maximize is bounded by a hard spend cap"
  else
    pass "Instance is aimed — reach_and_maintain has a ceiling"
  fi
  info "objective: ${OBJ}"
  info "measure:   ${MEAS}"
fi

# ---------------------------------------------------------------------------
# 5. Optional curiosity is bounded and externally seeded.
#
# Absence preserves today's behavior. If enabled, curiosity gets the same structural treatment as
# the mission ceiling gate: bounded appetite, a ground-truth frontier the loop cannot define for
# itself, and a frontier ceiling. Documentation is not enforcement.
# ---------------------------------------------------------------------------
echo
echo "[5/5] Curiosity (optional)"
CUR="$(python3 - <<'PY' 2>/dev/null || echo "error"
import yaml
import math
import re
d = yaml.safe_load(open("instance.yaml")) or {}
c = d.get("curiosity")
if not c or not c.get("enabled", False):
    print("disabled")
    raise SystemExit

def positive_number(v):
    try:
        f = float(v)
        return math.isfinite(f) and f > 0
    except Exception:
        return False

def positive_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0

budget = c.get("budget") or {}
frontier = c.get("frontier") or {}
source = (frontier.get("source") or "").strip()
authority = (frontier.get("authority") or "").strip()
goal = frontier.get("goal")
loop_owned = {
    "work", "runs", "learnings", "shared_learnings", "measurements", "heartbeat", "hibernation",
}
source_l = source.lower()
# v1 catches direct FROM/JOIN references. A view or CTE laundering loop-owned rows behind an
# external name is the operator defeating their own frontier.authority declaration.
bad_sources = sorted(
    t for t in loop_owned
    if re.search(r"\b(from|join)\s+([a-zA-Z_]\w*\.)?" + re.escape(t) + r"\b", source_l)
)

missing = []
if not positive_int(budget.get("cycles_per_day")):
    missing.append("budget.cycles_per_day > 0")
if not positive_number(budget.get("max_cost_usd_per_day")):
    missing.append("budget.max_cost_usd_per_day > 0")
if not source:
    missing.append("frontier.source")
if not authority:
    missing.append("frontier.authority")
if frontier.get("target") is None and not frontier.get("target_query"):
    missing.append("frontier.target or frontier.target_query")
if goal not in ("reach_and_maintain", "maximize"):
    missing.append("frontier.goal")
if not positive_int(frontier.get("max_items_per_cycle", 1)):
    missing.append("frontier.max_items_per_cycle > 0")
if bad_sources:
    missing.append("frontier.source must not read loop-owned tables: " + ", ".join(bad_sources))

if missing:
    print("missing|" + "; ".join(missing))
else:
    print("ok")
PY
)"

case "$CUR" in
  disabled)
    pass "Curiosity is absent or disabled — current behavior unchanged"
    ;;
  ok)
    pass "Curiosity is bounded and frontier-seeded"
    ;;
  missing\|*)
    fail "curiosity is enabled without structural bounds."
    info "${CUR#missing|}"
    info "Set a finite standing budget and an external frontier.source + authority with a ceiling."
    ;;
  *)
    fail "could not validate curiosity configuration in instance.yaml"
    ;;
esac

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
