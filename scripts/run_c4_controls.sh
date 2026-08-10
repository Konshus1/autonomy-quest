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

# Start the real narrow governance service without publishing its host port, then call it from the
# exact built app image. This proves the production route, token, full-plan client and durable
# allow/block/abstain receipts are wired together; a host-only DB function test is insufficient.
GOV_NAME="$PROJ-governance-probe"
GOV_ID=$(AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --name "$GOV_NAME" -d --rm --no-deps -T governance) \
  || rig_fail "narrow governance service failed to start"
sleep 3
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
  -e AQ_GOVERNANCE_URL="http://$GOV_NAME:8090" --entrypoint python app -c '
import json,os,time,uuid,psycopg2
from runner.causal_sync import authorize_plan
with psycopg2.connect(os.environ["AQ_DB_URL"]) as c, c.cursor() as q:
 q.execute("""select e.source_action,e.direct_effect,e.scope_conditions,e.relation_direction
 from causal_edge e where (select t.to_status from causal_principle_transition t
        where t.cause=e.source_action and t.effect=e.direct_effect and t.scope=e.scope_conditions
        order by t.id desc limit 1)=%s order by e.edge_id limit 1""",("promoted",))
 edge=q.fetchone()
 q.execute("select id from work order by id limit 4")
 work_ids=[row[0] for row in q.fetchall()]
assert edge is not None and len(work_ids)==4, "exact fixtures did not survive C4 controls"
cause,effect,scope,direction=edge
base={"step_id":"exact","action":cause,"expected_effect":effect,"expected_direction":direction,"scope":json.loads(scope)}
def gid(): return "{}/plan/{}".format(os.environ["AQ_INSTANCE_ID"],uuid.uuid4())
def bind(work_id,global_id,plan):
 with psycopg2.connect(os.environ["AQ_DB_URL"]) as c, c.cursor() as q:
  q.execute("update work set plan_id=%s,plan=%s::jsonb where id=%s",(global_id,json.dumps(plan),work_id))
# Wait through service startup using only typed defer responses, replaying one durable plan ID.
allow_id=gid(); allow_plan={"steps":[base]}; bind(work_ids[0],allow_id,allow_plan)
for _ in range(20):
 allow=authorize_plan(allow_id,work_ids[0],allow_plan,timeout=.5)
 if allow.disposition=="allow": break
 time.sleep(.25)
assert allow.disposition=="allow" and allow.selected and allow.governed and allow.authorization_id,allow
opposite={**base,"expected_direction":{"toward":"away","away":"toward","neutral":"toward"}[direction]}
block_id=gid(); block_plan={"steps":[opposite]}; bind(work_ids[1],block_id,block_plan)
block=authorize_plan(block_id,work_ids[1],block_plan)
assert block.disposition=="block" and not block.may_act and block.authorization_id,block
uncovered={**base,"action":"no-such-action-"+str(uuid.uuid4())}
abstain_id=gid(); abstain_plan={"steps":[uncovered]}; bind(work_ids[2],abstain_id,abstain_plan)
abstain=authorize_plan(abstain_id,work_ids[2],abstain_plan)
assert abstain.disposition=="abstain" and abstain.may_act and not abstain.selected and abstain.authorization_id,abstain
bad=dict(os.environ); bad["AQ_GOVERNANCE_DECISION_TOKEN"]="wrong"
denied=authorize_plan(gid(),work_ids[3],{"steps":[uncovered]},env=bad)
assert denied.disposition=="defer" and denied.reason_code=="governance_unauthorized",denied
print("built-narrow-plan-authority-ok")
' >/dev/null || { docker rm -f "$GOV_ID" >/dev/null 2>&1 || true; echo "C4 CONTROL RED: built narrow plan authority failed" >&2; cleanup; exit 1; }
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
  -e PYTHONPATH=/app -e AQ_GOVERNED_FEEDBACK=1 -e AQ_GOVERNANCE_URL="http://$GOV_NAME:8090" \
  -e AQ_C4_CONTROL_DSN="postgresql://aq_owner:${AQ_DB_OWNER_PASSWORD}@postgres:5432/aq" \
  --entrypoint python app /app/scripts/prove_m4_pre_act_boundary.py >/dev/null \
  || { docker rm -f "$GOV_ID" >/dev/null 2>&1 || true; echo "C4 CONTROL RED: built pre-ACT loop proof failed" >&2; cleanup; exit 1; }
docker rm -f "$GOV_ID" >/dev/null 2>&1 || true

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
