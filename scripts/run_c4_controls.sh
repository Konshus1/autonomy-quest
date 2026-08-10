#!/bin/sh
# Run the C4/M2 principal-isolation controls against REAL PostgreSQL principals, non-skipping.
#
# WHY THIS SCRIPT EXISTS. tests/test_c4_governance_pg.py is skipif'd on AQ_C4_TEST_DSN, so a plain
# `pytest -q` reports skips and a green run. A SKIPPED CONTROL IS A CONTROL THAT CANNOT COME
# BACK NEGATIVE — it is indistinguishable from a passing one in the summary line, and these six are
# the only checks that the four database principals are actually separated rather than merely
# declared. The original six plus every added boundary control must execute before any release
# claim about the trust boundary.
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
trap 'cleanup' INT TERM

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

# -p no:cacheprovider keeps the repo clean; -rs surfaces skip reasons so a skip is legible.
# Prefer the active project interpreter, but make the checked-in control runnable on a clean host
# through uv rather than silently skipping because pytest is absent.
set +e
if python3 -c 'import pytest, psycopg2' >/dev/null 2>&1; then
  OUT=$(cd "$ROOT" && python3 -m pytest tests/test_c4_governance_pg.py -q -rs -p no:cacheprovider 2>&1)
  RC=$?
elif command -v uv >/dev/null 2>&1; then
  OUT=$(cd "$ROOT" && uv run --with pytest --with psycopg2-binary \
    python -m pytest tests/test_c4_governance_pg.py -q -rs -p no:cacheprovider 2>&1)
  RC=$?
else
  OUT="pytest+psycopg2 are absent and uv is unavailable"
  RC=2
fi
set -e
echo "$OUT" | tail -100

# SKIP IS A RIG FAILURE, NOT A PASS. This is the whole point of the script.
if echo "$OUT" | grep -qiE "skipped"; then
  rig_fail "a C4 control SKIPPED — DSNs did not reach pytest. Skips are not passes."
fi
# Assert the expected number actually EXECUTED. "0 passed" also exits 0 in some configurations,
# and a control set that silently shrank is the failure mode this file is guarding against.
if ! echo "$OUT" | grep -qE "^13 passed"; then
  [ "$RC" -ne 0 ] && { echo "C4 CONTROL RED" >&2; cleanup; exit 1; }
  rig_fail "expected exactly 13 controls to run; summary was: $(echo "$OUT" | tail -1)"
fi

cleanup
echo "C4 OK: 13/13 principal-isolation controls ran against real principals and passed."
