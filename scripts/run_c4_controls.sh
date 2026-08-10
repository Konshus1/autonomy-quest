#!/bin/sh
# Run the C4/M2 principal-isolation controls against REAL PostgreSQL principals, non-skipping.
#
# WHY THIS SCRIPT EXISTS. tests/test_c4_governance_pg.py is skipif'd on AQ_C4_TEST_DSN, so a plain
# `pytest -q` reports skips and a green run. A SKIPPED CONTROL IS A CONTROL THAT CANNOT COME
# BACK NEGATIVE — it is indistinguishable from a passing one in the summary line, and these controls are
# the only checks that the five database principals are actually separated rather than merely
# declared. The original six plus every added boundary control must execute before any release
# claim about the trust boundary.
#
# This script stands up exact Compose, publishes the Postgres port on a private loopback port,
# derives the five DSNs from the generated secrets, runs the controls, and FAILS IF ANY SKIPS.
#
# Usage:  scripts/run_c4_controls.sh
# Exit:   0 all mandatory controls ran and passed
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

# Build every modified runtime service, not just the migration image. A host-only pytest green
# cannot certify the image that production will execute.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" build app governance evaluator \
  || rig_fail "runtime image build failed"
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
AQ_C4_EVALUATOR_DSN="postgresql://aq_evaluator:${AQ_EVALUATOR_DB_PASSWORD}@$H/aq"
export AQ_C4_TEST_DSN AQ_C4_ACTOR_DSN AQ_C4_LOOP_DSN AQ_C4_GOVERNANCE_DSN AQ_C4_EVALUATOR_DSN

# Assert that each runtime image contains the exact checked-out control-plane source, then import
# the production app with its real service credential. This catches stale images and CREATE-at-
# import failures that database-only controls cannot see.
HOST_APP_SHA=$(python3 -c "import hashlib;print(hashlib.sha256(open('$ROOT/management/api/app.py','rb').read()).hexdigest())")
for SERVICE in app governance evaluator; do
  IMAGE_SHA=$(AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
    --entrypoint python "$SERVICE" -c \
    "import hashlib;print(hashlib.sha256(open('/app/management/api/app.py','rb').read()).hexdigest())" \
    2>/dev/null) || rig_fail "$SERVICE image could not execute Python"
  [ "$IMAGE_SHA" = "$HOST_APP_SHA" ] || rig_fail "$SERVICE image source hash is stale"
  AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
    --entrypoint python "$SERVICE" -c \
    "from management.api.app import store,causal; assert store.backing=='postgres'; print('runtime-import-ok')" \
    >/dev/null || rig_fail "$SERVICE production import failed"
done

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
if echo "$OUT" | grep -qiE "skipped|xfailed|xpassed|deselected"; then
  rig_fail "a C4 control did not execute cleanly (skip/xfail/xpass/deselection)."
fi
# Assert the expected number actually EXECUTED. "0 passed" also exits 0 in some configurations,
# and a control set that silently shrank is the failure mode this file is guarding against.
if ! echo "$OUT" | grep -qE "^25 passed in [0-9]+([.][0-9]+)?s$"; then
  [ "$RC" -ne 0 ] && { echo "C4 CONTROL RED" >&2; cleanup; exit 1; }
  rig_fail "expected exactly 25 controls to run; summary was: $(echo "$OUT" | tail -1)"
fi

# Re-consume one acquisition receipt through the production evaluator image. The operation is a
# replay by now, so it must return the already-staged edge without creating a second application.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
  --entrypoint python evaluator -c '
import os, psycopg2
from management.api.principle_governance import PgGovernedPrincipleLifecycle
with psycopg2.connect(os.environ["AQ_DB_URL"]) as c, c.cursor() as q:
 q.execute("select acquisition_id,(result->>%s)::bigint from plan_acquisition where result ? %s order by acquisition_id limit 1",("_aq_completion_run_id","causal_proposals"))
 row=q.fetchone()
 assert row is not None
edge=PgGovernedPrincipleLifecycle(os.environ["AQ_GOVERNANCE_DB_URL"],init_schema=False).attest_acquisition(row[0],row[1],0)
assert edge>0
print("production-evaluator-consumer-ok",edge)
' >/dev/null || { echo "C4 CONTROL RED: production evaluator consumer failed" >&2; cleanup; exit 1; }

cleanup
echo "C4 OK: 25/25 principal-isolation controls ran against real principals and passed."
