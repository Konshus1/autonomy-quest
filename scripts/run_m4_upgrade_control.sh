#!/bin/sh
# Exact existing-volume control: initialize a pre-evaluator schema, then migrate with current image.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="aqupgrade$(date +%s 2>/dev/null || echo x)$$"
STATE="${AQ_STATE_DIR:-${TMPDIR:-/tmp}/aq-upgrade-$PROJ}"
OLD="$STATE/old"
STAGE="$STATE/pre-m5-schema"
OVERRIDE="$STATE/old-volume.yml"
cleanup() { AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" down -v >/dev/null 2>&1 || true; }
trap cleanup INT TERM EXIT
mkdir -p "$OLD" "$STAGE"
git -C "$ROOT" archive a9bceb46 schema | tar -x -C "$OLD"
for file in "$ROOT"/schema/[0-9][0-9][0-9]_*.sql; do
  number=$(basename "$file" | cut -c1-3)
  [ "$number" -le 23 ] && cp "$file" "$STAGE/"
done
cat >"$OVERRIDE" <<EOF
services:
  postgres:
    volumes:
      - aq-postgres-data:/var/lib/postgresql/data
      - $OLD/schema:/docker-entrypoint-initdb.d:ro
  migrate:
    volumes:
      - $STAGE:/app/schema:ro
EOF
# Generate an upgrade-capable secret file, then initialize only Postgres from the old schema.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" config >/dev/null
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" up -d --no-deps postgres >/dev/null
for i in $(seq 1 60); do docker exec "$PROJ-postgres-1" pg_isready -U aq_owner -d aq >/dev/null 2>&1 && break; sleep 1; done
docker exec "$PROJ-postgres-1" pg_isready -U aq_owner -d aq >/dev/null 2>&1 || { echo "M4-UPGRADE-RIG-FAIL: old database not ready" >&2; exit 2; }
OLD_ROLES=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "select count(*) from pg_roles where rolname='aq_evaluator'")
[ "$OLD_ROLES" = "0" ] || { echo "M4-UPGRADE-RIG-FAIL: historical schema unexpectedly has evaluator role" >&2; exit 2; }
# Upgrade first through M4, create a real pre-M5 completed checked run, then apply M5.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" build migrate governance evaluator >/dev/null
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T migrate >/dev/null
docker exec -i "$PROJ-postgres-1" psql -U aq_owner -d aq -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
WITH w AS (
  INSERT INTO work(kind,summary,rationale,status,plan_id,plan)
  VALUES('upgrade-m4','upgrade','pre-M5 checked completion','done',
    'urn:uuid:11111111-1111-4111-8111-111111111111/plan/22222222-2222-4222-8222-222222222222',
    '{"steps":[]}'::jsonb) RETURNING id,plan_id,plan
), a AS (
  INSERT INTO aq_control.plan_authorization(global_plan_id,work_id,request_plan,request_digest,
    disposition,reason_code,reason,may_act,selected,governed,authorized_by)
  SELECT plan_id,id,plan,encode(sha256(convert_to(plan::text,'UTF8')),'hex'),
    'abstain','no_applicable_promoted_governor','upgrade fixture',true,false,false,'upgrade-fixture'
  FROM w RETURNING authorization_id,work_id
)
INSERT INTO runs(work_id,plan_authorization_id,completed_at,outcome,succeeded)
SELECT work_id,authorization_id,now()-interval '1 day','pre-M5 completed',false FROM a;
SQL
cat >"$OVERRIDE" <<EOF
services:
  postgres:
    volumes:
      - aq-postgres-data:/var/lib/postgresql/data
      - $OLD/schema:/docker-entrypoint-initdb.d:ro
  migrate:
    volumes:
      - $ROOT/schema:/app/schema:ro
EOF
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T migrate >/dev/null
BACKFILLED=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "select count(*) from plan_outcome_evaluation_outbox o join runs r on r.id=o.run_id where r.outcome='pre-M5 completed'")
[ "$BACKFILLED" = "1" ] || { echo "M5-UPGRADE-CONTROL-RED: pre-M5 checked completion was not backfilled" >&2; exit 1; }
CHECK=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "
select (select count(*) from pg_roles where rolname in ('aq_loop','aq_actor','aq_governance','aq_evaluator','aq_control_owner'))=5
 and not (select rolcanlogin from pg_roles where rolname='aq_control_owner')
 and has_function_privilege('aq_governance','aq_control.authorize_plan(text,bigint,jsonb)','EXECUTE')
 and has_function_privilege('aq_evaluator','aq_control.attest_and_apply_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)','EXECUTE')
 and not has_table_privilege('aq_governance','causal_principle_transition','INSERT')
 and not has_table_privilege('aq_governance','causal_principle_plan_usage','INSERT')
 and has_function_privilege('aq_loop','aq_control.register_mined_principle(text,text,text,bigint[],bigint[])','EXECUTE')
 and has_function_privilege('aq_evaluator','aq_control.attest_and_apply_plan_outcome(bigint,jsonb)','EXECUTE')
 and has_function_privilege('aq_evaluator','aq_control.pending_plan_outcome_runs(integer)','EXECUTE')
 and not has_function_privilege('aq_governance','aq_control.attest_and_apply_plan_outcome(bigint,jsonb)','EXECUTE')")
if [ "$CHECK" != "t" ]; then
  docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "select
    has_function_privilege('aq_loop','aq_control.register_mined_principle(text,text,text,bigint[],bigint[])','EXECUTE'),
    has_function_privilege('aq_evaluator','aq_control.attest_and_apply_plan_outcome(bigint,jsonb)','EXECUTE'),
    has_function_privilege('aq_governance','aq_control.attest_and_apply_plan_outcome(bigint,jsonb)','EXECUTE'),
    has_table_privilege('aq_governance','causal_principle_transition','INSERT'),
    has_table_privilege('aq_governance','causal_principle_plan_usage','INSERT')" >&2
  echo "M4-UPGRADE-CONTROL-RED: role or ACL upgrade incomplete" >&2; exit 1
fi
for SERVICE in governance evaluator; do
 AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T --entrypoint python "$SERVICE" -c "from management.api.app import store,causal; assert store.backing=='postgres'; print('ok')" >/dev/null \
  || { echo "M4-UPGRADE-CONTROL-RED: $SERVICE import failed after upgrade" >&2; exit 1; }
done
echo "M4/M5 UPGRADE OK: a9bceb46 existing volume gained all five principals, final ACLs, and built service imports."
