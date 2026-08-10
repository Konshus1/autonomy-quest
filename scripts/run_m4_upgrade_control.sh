#!/bin/sh
# Exact existing-volume control: initialize a pre-evaluator schema, then migrate with current image.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="aqupgrade$(date +%s 2>/dev/null || echo x)$$"
STATE="${AQ_STATE_DIR:-${TMPDIR:-/tmp}/aq-upgrade-$PROJ}"
OLD="$STATE/old"
OVERRIDE="$STATE/old-volume.yml"
cleanup() { AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" down -v >/dev/null 2>&1 || true; }
trap cleanup INT TERM EXIT
mkdir -p "$OLD"
git -C "$ROOT" archive a9bceb46 schema | tar -x -C "$OLD"
cat >"$OVERRIDE" <<EOF
services:
  postgres:
    volumes:
      - aq-postgres-data:/var/lib/postgresql/data
      - $OLD/schema:/docker-entrypoint-initdb.d:ro
EOF
# Generate an upgrade-capable secret file, then initialize only Postgres from the old schema.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" config >/dev/null
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" up -d --no-deps postgres >/dev/null
for i in $(seq 1 60); do docker exec "$PROJ-postgres-1" pg_isready -U aq_owner -d aq >/dev/null 2>&1 && break; sleep 1; done
docker exec "$PROJ-postgres-1" pg_isready -U aq_owner -d aq >/dev/null 2>&1 || { echo "M4-UPGRADE-RIG-FAIL: old database not ready" >&2; exit 2; }
OLD_ROLES=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "select count(*) from pg_roles where rolname='aq_evaluator'")
[ "$OLD_ROLES" = "0" ] || { echo "M4-UPGRADE-RIG-FAIL: historical schema unexpectedly has evaluator role" >&2; exit 2; }
# Run the current migration image against that existing named volume. Init hooks do not rerun.
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" build migrate governance evaluator >/dev/null
AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T migrate >/dev/null
CHECK=$(docker exec "$PROJ-postgres-1" psql -U aq_owner -d aq -Atqc "
select (select count(*) from pg_roles where rolname in ('aq_loop','aq_actor','aq_governance','aq_evaluator','aq_control_owner'))=5
 and not (select rolcanlogin from pg_roles where rolname='aq_control_owner')
 and has_function_privilege('aq_governance','aq_control.authorize_plan(text,bigint,jsonb)','EXECUTE')
 and has_function_privilege('aq_evaluator','aq_control.attest_grounding_observation(uuid,text,text,text,text,text,text,double precision,double precision,jsonb)','EXECUTE')
 and not has_table_privilege('aq_governance','causal_principle_transition','INSERT')
 and not has_table_privilege('aq_governance','causal_principle_plan_usage','INSERT')")
[ "$CHECK" = "t" ] || { echo "M4-UPGRADE-CONTROL-RED: role or ACL upgrade incomplete" >&2; exit 1; }
for SERVICE in governance evaluator; do
 AQ_STATE_DIR="$STATE" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJ" -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" run --rm --no-deps -T --entrypoint python "$SERVICE" -c "from management.api.app import store,causal; assert store.backing=='postgres'; print('ok')" >/dev/null \
  || { echo "M4-UPGRADE-CONTROL-RED: $SERVICE import failed after upgrade" >&2; exit 1; }
done
echo "M4 UPGRADE OK: a9bceb46 existing volume gained all five principals, final ACLs, and built service imports."
