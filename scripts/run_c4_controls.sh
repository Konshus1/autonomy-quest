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
AQ_GOVERNED_FEEDBACK= AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" config --format json >"$STATE/default-config.json" \
  || rig_fail "could not render default Compose config"
DEFAULT_FLAG=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"]["app"]["environment"]["AQ_GOVERNED_FEEDBACK"])' "$STATE/default-config.json")
[ "$DEFAULT_FLAG" = "0" ] || rig_fail "AQ_GOVERNED_FEEDBACK defaulted to $DEFAULT_FLAG instead of 0"
echo "M6_DEFAULT_FLAG_OFF"


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
RUNTIME_FILES="management/api/app.py management/api/principle_governance.py runner/db.py runner/loop.py runner/executor.py schema/024_checked_lifecycle_withdrawal.sql scripts/prove_m4_pre_act_boundary.py"
HOST_RUNTIME_SHA=$(cd "$ROOT" && python3 -c 'import hashlib,sys; h=hashlib.sha256(); [h.update(open(p,"rb").read()) for p in sys.argv[1:]]; print(h.hexdigest())' $RUNTIME_FILES)
for SERVICE in app governance evaluator; do
  IMAGE_SHA=$(AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
    --entrypoint python "$SERVICE" -c \
    "import hashlib;print(hashlib.sha256(open('/app/management/api/app.py','rb').read()).hexdigest())" \
    2>/dev/null) || rig_fail "$SERVICE image could not execute Python"
  [ "$IMAGE_SHA" = "$HOST_APP_SHA" ] || rig_fail "$SERVICE image source hash is stale"
  IMAGE_RUNTIME_SHA=$(AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
    --entrypoint python "$SERVICE" -c \
    'import hashlib,sys; h=hashlib.sha256(); [h.update(open("/app/"+p,"rb").read()) for p in sys.argv[1:]]; print(h.hexdigest())' $RUNTIME_FILES 2>/dev/null) \
    || rig_fail "$SERVICE runtime manifest could not be hashed"
  [ "$IMAGE_RUNTIME_SHA" = "$HOST_RUNTIME_SHA" ] || rig_fail "$SERVICE runtime manifest is stale"
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
if ! echo "$OUT" | grep -qE "^30 passed in [0-9]+([.][0-9]+)?s$"; then
  [ "$RC" -ne 0 ] && { echo "C4 CONTROL RED" >&2; cleanup; exit 1; }
  rig_fail "expected exactly 30 controls to run; summary was: $(echo "$OUT" | tail -1)"
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
echo "M6_ACT_CREDENTIAL_DENIAL"
# The production miner runs with aq_loop and must register only provisional SQL-derived evidence.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
  --entrypoint python app -c '
from management.api.app import causal
result=causal.mine_from_mission_loop()
assert result["mined"]>=1
print("production-checked-miner-ok",result["mined"])
' >/dev/null || { docker rm -f "$GOV_ID" >/dev/null 2>&1 || true; echo "C4 CONTROL RED: production checked miner failed" >&2; cleanup; exit 1; }
docker rm -f "$GOV_ID" >/dev/null 2>&1 || true

# Recover the mandatory allowed-run outcome on evaluator startup, then prove restart is one-shot.
PRE_RECOVERY=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "
  select count(*) from aq_control.plan_outcome_application a
  join runs r on r.id=a.run_id join work w on w.id=r.work_id where w.kind='exact-m4'")
[ "$PRE_RECOVERY" = "0" ] || rig_fail "exact outcome was already consumed before evaluator startup"
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" up -d --no-deps evaluator >/dev/null \
  || rig_fail "evaluator startup recovery service failed"
RECOVERED=0
for i in $(seq 1 60); do
  RECOVERED=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "
    select count(*) from aq_control.plan_outcome_application a
    join runs r on r.id=a.run_id join work w on w.id=r.work_id where w.kind='exact-m4'")
  [ "$RECOVERED" = "1" ] && break
  sleep 1
done
[ "$RECOVERED" = "1" ] || rig_fail "evaluator startup did not recover exact grounded-plan outcome"
BEFORE_RESTART=$(docker inspect "$PROJ-evaluator-1" --format '{{.State.StartedAt}}')
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" restart evaluator >/dev/null \
  || rig_fail "evaluator restart failed"
sleep 2
AFTER_RESTART=$(docker inspect "$PROJ-evaluator-1" --format '{{.State.StartedAt}}:{{.State.Running}}')
[ "$AFTER_RESTART" != "$BEFORE_RESTART:true" ] && [ "${AFTER_RESTART##*:}" = "true" ] \
  || rig_fail "evaluator container did not complete a real restart"
RESTARTED=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "
  select count(*) from aq_control.plan_outcome_application a
  join runs r on r.id=a.run_id join work w on w.id=r.work_id where w.kind='exact-m4'")
[ "$RESTARTED" = "1" ] || rig_fail "evaluator restart duplicated or lost exact outcome application"
echo "M6_EVALUATOR_RESTART_RECOVERY"

# Re-consume one acquisition receipt through the production evaluator image. The operation is a
# replay by now, so it must return the already-staged edge without creating a second application.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" \
  -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T \
  -e AQ_C4_CONTROL_DSN="postgresql://aq_owner:${AQ_DB_OWNER_PASSWORD}@postgres:5432/aq" \
  --entrypoint python evaluator -c '
import os, psycopg2, uuid
from management.api.principle_governance import PgGovernedPrincipleLifecycle
lifecycle=PgGovernedPrincipleLifecycle(os.environ["AQ_GOVERNANCE_DB_URL"],init_schema=False)
with psycopg2.connect(os.environ["AQ_C4_CONTROL_DSN"]) as c,c.cursor() as q:
 q.execute("""select a.edge_id from aq_control.evidence_application a
 join aq_control.evidence_event e using(event_id) join work w on w.id=e.work_id
 where e.event_kind=%s and w.summary=%s""",("acquisition_completed","organic-grounding"))
 row=q.fetchone(); assert row is not None
 edge=row[0]
with psycopg2.connect(os.environ["AQ_DB_URL"]) as c, c.cursor() as q:
 q.execute("select o.run_id from plan_outcome_evaluation_outbox o join runs r on r.id=o.run_id join work w on w.id=r.work_id where w.kind=%s order by o.run_id desc limit 1",("exact-m4",))
 outcome_row=q.fetchone()
assert outcome_row is not None
from fastapi.testclient import TestClient
from management.api.app import app
client=TestClient(app)
assert client.post("/api/causal/governance/attest-plan-outcome",json={"run_id":outcome_row[0]},headers={"x-aq-evaluator-trigger-token":"wrong"}).status_code==403
response=client.post("/api/causal/governance/attest-plan-outcome",json={"run_id":outcome_row[0]},headers={"x-aq-evaluator-trigger-token":os.environ["AQ_EVALUATOR_TRIGGER_TOKEN"]})
assert response.status_code==200,response.text
outcome=response.json(); assert outcome["resolved"] is True
backlog=lifecycle.consume_pending_plan_outcomes(100); assert "consumed" in backlog
# This exact used promotion was independently grounded in the earlier five-principal controls.
with psycopg2.connect(os.environ["AQ_C4_CONTROL_DSN"]) as c,c.cursor() as q:
 q.execute("""select g.promotion_transition_id,t.cause,t.effect,t.scope::text,o.expected_direction
 from runs r join work w on w.id=r.work_id
 join aq_control.plan_authorization_governor g on g.authorization_id=r.plan_authorization_id
 join causal_principle_transition t on t.id=g.promotion_transition_id
 join aq_control.grounding_promotion gp on gp.promotion_transition_id=t.id
 join lateral (select expected_direction from aq_control.grounding_observation x
   where x.observation_id=any(gp.support_observation_ids) order by x.observation_id limit 1) o on true
 where r.id=%s and w.kind=%s""",(outcome_row[0],"exact-m4"))
 governed=q.fetchone(); assert governed is not None
 promotion_id,cause,effect,scope,expected_direction=governed
 q.execute("select count(*) from aq_control.plan_authorization_outcome where run_id=%s and promotion_transition_id=%s",(outcome_row[0],promotion_id))
 assert q.fetchone()[0]==1
context=lifecycle.register_execution_context(
 {"instance_id":f"urn:uuid:{uuid.uuid4()}","environment_id":"m6-post-use-refutation",
  "domain":f"m6-{uuid.uuid4()}","mission_id":"scope-b-m6","harness":"m6/v1"},
 {"source":"m6-independent-evaluator","receipt":f"context:{uuid.uuid4()}"})
refutation=lifecycle.attest_grounding_observation(
 (cause,effect,scope),context_id=context,test_kind="support",
 evidence_ref=f"post-use-refutation:{uuid.uuid4()}",expected_direction=expected_direction,
 observed_delta=-2.0,noise_tolerance=.1,
 evidence_payload={"source":"m6-independent-evaluator","receipt":f"refutation:{uuid.uuid4()}"})
assert refutation["automatic_demotion"] is True and refutation["status"]=="demoted"
from runner.db import Db
relations=Db(os.environ["AQ_DB_URL"],graph="none",connect_retries=1).known_plan_relations()
assert not [r for r in relations if r.get("promotion_transition_id")==promotion_id]
with psycopg2.connect(os.environ["AQ_C4_CONTROL_DSN"]) as c,c.cursor() as q:
 q.execute("select count(*) from aq_control.grounding_demotion where promotion_transition_id=%s",(promotion_id,))
 assert q.fetchone()[0]==1
print("production-evaluator-consumer-ok",edge,outcome_row[0],backlog["consumed"])
' >/dev/null || { echo "C4 CONTROL RED: production evaluator consumer or composed chain failed" >&2; cleanup; exit 1; }
echo "M6 COMPOSED CHAIN OK"

# Interviewed missions must mount the exact same read-only instance into the evaluator image.
CUSTOM_INSTANCE="$STATE/custom-instance.yaml"
sed -e 's/what: "count of active paying customers"/what: "custom_evaluator_mount_probe"/' \
    -e 's/where: "select count(distinct customer_id) from subscriptions where status='"'"'active'"'"'"/where: "SELECT 7"/' \
    -e 's/target: 20/target: 7/' \
    "$ROOT/templates/running-a-business/instance.yaml" >"$CUSTOM_INSTANCE"
AQ_INSTANCE_FILE="$CUSTOM_INSTANCE" AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ"   -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" -f "$ROOT/docker-compose.mission.yml"   run --rm --no-deps -T --entrypoint python evaluator -c '
import os,psycopg2
from runner.config import Instance
inst=Instance.load("/app/instance.yaml")
assert inst.mission.measure.what=="custom_evaluator_mount_probe"
with psycopg2.connect(os.environ["AQ_DB_URL"]) as c,c.cursor() as q:
 q.execute(inst.mission.measure.where); value=q.fetchone()[0]
assert value==7 and inst.mission.measure.satisfied(value,inst.mission.measure.target)
' >/dev/null || { echo "C4 CONTROL RED: evaluator did not receive interviewed mission measure" >&2; cleanup; exit 1; }

cleanup
echo "C4 OK: 30/30 principal-isolation controls ran against real principals and passed."
