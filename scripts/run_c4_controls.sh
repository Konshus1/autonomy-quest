#!/bin/sh
# Run the six C4 principal-isolation controls against REAL PostgreSQL principals, non-skipping.
#
# WHY THIS SCRIPT EXISTS. tests/test_c4_governance_pg.py is skipif'd on AQ_C4_TEST_DSN, so a plain
# `pytest -q` reports "6 skipped" and a green run. A SKIPPED CONTROL IS A CONTROL THAT CANNOT COME
# BACK NEGATIVE — it is indistinguishable from a passing one in the summary line, and these six are
# the only checks that the four database principals are actually separated rather than merely
# declared. They must be exercised before any release claim about the trust boundary.
#
# This script stands up exact Compose, publishes the Postgres port on a private loopback port,
# derives the four DSNs from the generated secrets, runs the controls, and FAILS IF ANY SKIPS.
#
# Usage:  scripts/run_c4_controls.sh
# Exit:   0 all six ran and passed
#         1 a control went red (the system is broken)
#         2 the rig failed — compose, port, secrets, or a SKIP (this script is broken)
#
# The 1-vs-2 split is deliberate. A harness that cannot say "my rig broke" will eventually say
# "the system is fine" instead.
set -eu

PROJ="aqc4$(date +%s 2>/dev/null || echo x)$$"
PORT="${AQ_C4_PORT:-55471}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${AQ_STATE_DIR:-${TMPDIR:-/tmp}/aq-c4-$PROJ}"
OVERRIDE="$STATE/pgport.yml"
rig_fail() { echo "C4-RIG-FAILURE: $*" >&2; cleanup; exit 2; }
cleanup() {
  AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" down -v >/dev/null 2>&1 || true
}
trap 'cleanup' INT TERM EXIT   # EXIT too: set -e can abort before the explicit cleanup

mkdir -p "$STATE"
printf 'services:\n  postgres:\n    ports:\n      - "%s:5432"\n' "$PORT" > "$OVERRIDE"

AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" up -d --build postgres migrate \
  || rig_fail "compose up failed (port $PORT may be in use; set AQ_C4_PORT)"

# The migrate service must actually finish. A half-applied schema would make these controls
# meaningless in a way that looks like a normal failure.
sleep 8
MIGRATE_RC=$(docker inspect "$PROJ-migrate-1" --format '{{.State.ExitCode}}' 2>/dev/null || echo missing)
[ "$MIGRATE_RC" = "0" ] || rig_fail "migrate exited $MIGRATE_RC"

. "$STATE/compose-secrets"
H="127.0.0.1:$PORT"
AQ_C4_TEST_DSN="postgresql://aq_owner:${AQ_DB_OWNER_PASSWORD}@$H/aq"
AQ_C4_ACTOR_DSN="postgresql://aq_actor:${AQ_ACTOR_DB_PASSWORD}@$H/aq"
AQ_C4_LOOP_DSN="postgresql://aq_loop:${AQ_LOOP_DB_PASSWORD}@$H/aq"
AQ_C4_GOVERNANCE_DSN="postgresql://aq_governance:${AQ_GOVERNANCE_DB_PASSWORD}@$H/aq"
export AQ_C4_TEST_DSN AQ_C4_ACTOR_DSN AQ_C4_LOOP_DSN AQ_C4_GOVERNANCE_DSN

# PREFLIGHT THE INTERPRETER BEFORE BLAMING THE PRODUCT.
#
# This script previously ran pytest and, when the summary did not say "N passed", exited 1 =
# PRODUCT RED. On a reviewer's machine host python3 was 3.14 WITHOUT pytest, so a missing test
# runner was reported as "our system is broken". That is precisely the failure the 1-vs-2 split
# exists to prevent, committed by the script that defines the split. A reproduce command that
# tells a stranger OUR code failed, when their interpreter lacks a dependency, is worse than no
# command at all.
#
# Import-check every dependency the controls need, as the interpreter that will run them.
PY_BIN="${AQ_PY:-python3}"
command -v "$PY_BIN" >/dev/null 2>&1 || rig_fail "no '$PY_BIN' on PATH (set AQ_PY to your interpreter)"
MISSING=$("$PY_BIN" -c 'import importlib.util as u; print(",".join(m for m in ("pytest","psycopg2") if u.find_spec(m) is None))' 2>/dev/null) || rig_fail "$PY_BIN could not run a probe script"
if [ -n "$MISSING" ]; then
  rig_fail "$PY_BIN ($("$PY_BIN" -V 2>&1)) is missing: $MISSING. Run 'pip install -r requirements.txt', or set AQ_PY to an interpreter that has them. This is a RIG problem, not a product failure."
fi

# -p no:cacheprovider keeps the repo clean; -rs surfaces skip reasons so a skip is legible.
set +e
OUT=$(cd "$ROOT" && "$PY_BIN" -m pytest tests/test_c4_governance_pg.py \
        tests/test_terminal_work_open_acquisition.py -q -rs -p no:cacheprovider 2>&1)
RC=$?
set -e
echo "$OUT" | tail -14

# Collection or import failure is ALSO a rig problem, not a red control.
if echo "$OUT" | grep -qiE "error during collection|ModuleNotFoundError|ImportError|no tests ran"; then
  rig_fail "pytest could not collect the controls (import/collection error) — rig, not product."
fi

# SKIP IS A RIG FAILURE, NOT A PASS. This is the whole point of the script.
if echo "$OUT" | grep -qiE "skipped"; then
  rig_fail "a C4 control SKIPPED — DSNs did not reach pytest. Skips are not passes."
fi
# EVERY COLLECTED CONTROL MUST HAVE PASSED. Counting passes against COLLECTED rather than against
# a hardcoded number means the assertion cannot rot as controls are added — but a collected count
# that silently shrinks would then pass too, so a floor is enforced as well.
COLLECTED=$(cd "$ROOT" && "$PY_BIN" -m pytest tests/test_c4_governance_pg.py \
              tests/test_terminal_work_open_acquisition.py -q --collect-only -p no:cacheprovider 2>/dev/null \
            | grep -c "::" || true)
PASSED=$(echo "$OUT" | grep -oE "[0-9]+ passed" | head -1 | cut -d' ' -f1)
PASSED="${PASSED:-0}"

if [ "$RC" -ne 0 ]; then
  echo "C4 CONTROL RED: $(echo "$OUT" | tail -1)" >&2
  cleanup; exit 1
fi
[ "$COLLECTED" -ge 6 ] || rig_fail "only $COLLECTED controls collected; expected at least 6 — the control set shrank"
[ "$PASSED" = "$COLLECTED" ] || rig_fail "collected $COLLECTED but $PASSED passed; something did not execute"

cleanup
echo "C4 OK: $PASSED/$COLLECTED controls ran against real principals and passed."
