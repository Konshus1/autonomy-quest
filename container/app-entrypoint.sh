#!/usr/bin/env bash
set -euo pipefail

export AQ_DB_URL="${AQ_DB_URL:?AQ_DB_URL is required}"
export AQ_UI_BIND="${AQ_UI_BIND:-0.0.0.0}"
export AQ_UI_PORT="${AQ_UI_PORT:-8080}"
export AQ_MGMT_BIND="${AQ_MGMT_BIND:-0.0.0.0}"
export AQ_MGMT_PORT="${AQ_MGMT_PORT:-8090}"
export AQ_APPROVAL_TOKEN_FILE="${AQ_APPROVAL_TOKEN_FILE:-/var/run/aq/approval_token}"

mkdir -p "$(dirname "$AQ_APPROVAL_TOKEN_FILE")"
if [ -z "${AQ_APPROVAL_TOKEN:-}" ]; then
  AQ_APPROVAL_TOKEN="$(openssl rand -hex 24)"
  export AQ_APPROVAL_TOKEN
fi
umask 077
printf '%s
' "$AQ_APPROVAL_TOKEN" > "$AQ_APPROVAL_TOKEN_FILE"

# An unaimed install still has honest status/management surfaces.  Existence alone is not
# aimedness: the generated `{}` placeholder survives a container restart.  Validate the mission
# fields on every boot so a restart can never turn that placeholder into a crash-looping mission.
INSTANCE_AIMED=0
if [ -f /app/instance.yaml ] && python - <<'PY'
import pathlib, yaml
raw = yaml.safe_load(pathlib.Path("/app/instance.yaml").read_text()) or {}
mission = raw.get("mission") or {}
measure = mission.get("measure") or {}
raise SystemExit(0 if str(mission.get("objective") or "").strip() and str(measure.get("what") or "").strip() else 1)
PY
then
  INSTANCE_AIMED=1
elif [ ! -e /app/instance.yaml ]; then
  printf '{}\n' > /app/instance.yaml
fi

# Seed the acting agent's standing instructions into its workspace (#4834). The act phase runs
# codex in $AQ_ACT_WORKSPACE (default /workspace, a durable VOLUME) and reads AGENTS.md there for
# standing instructions — so the template must be placed into the volume at runtime, not baked
# into the image. Idempotent (never overwrite an operator- or agent-authored AGENTS.md) and
# FAIL-SAFE: a seeding failure must never break the entrypoint or the loop, so the whole block is
# guarded and can only ever log.
seed_agent_workspace() {
  local ws="${AQ_ACT_WORKSPACE:-/workspace}"
  local template="${AQ_AGENTS_TEMPLATE:-/app/agent_skills/AGENTS.md}"
  local target="$ws/AGENTS.md"
  if [ ! -d "$ws" ]; then
    echo "[aq] agent workspace $ws absent; skipping AGENTS.md seeding" >&2
    return 0
  fi
  if [ -e "$target" ]; then
    echo "[aq] $target already present; leaving it untouched" >&2
    return 0
  fi
  if [ ! -f "$template" ]; then
    echo "[aq] AGENTS.md template $template missing; skipping seeding" >&2
    return 0
  fi
  if cp "$template" "$target" 2>/dev/null; then
    echo "[aq] seeded agent instructions into $target"
  else
    echo "[aq] could not seed $target (non-fatal); continuing" >&2
  fi
  return 0
}
seed_agent_workspace || true

wait_for_db() {
  python - <<'PY'
import os, time, psycopg2
last = None
for _ in range(60):
    try:
        with psycopg2.connect(os.environ["AQ_DB_URL"]) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                assert cur.fetchone()[0] == 1
        raise SystemExit(0)
    except Exception as exc:
        last = exc
        time.sleep(1)
raise SystemExit(f"database unavailable: {last}")
PY
}
# Schema ownership lives in the one-shot Compose migrate service, never in this container. The
# runtime process receives only aq_loop; ACT receives only the still-narrower aq_actor credential.
wait_for_db

supervise() {
  local name="$1"; shift
  while true; do
    echo "[aq] starting $name"
    "$@" || code=$?
    code="${code:-0}"
    if [ "$name" = loop ] && [ "${AQ_LOOP_AUTORESTART:-1}" = 0 ]; then
      echo "[aq] loop exited ($code); autorestart disabled so observability surfaces remain up" >&2
      return "$code"
    fi
    echo "[aq] $name exited ($code); restarting in 5s" >&2
    sleep 5
    unset code
  done
}

supervise status-ui python /app/aq.py ui &
STATUS_PID=$!
supervise management-api python -m uvicorn management.api.app:app \
  --host "$AQ_MGMT_BIND" --port "$AQ_MGMT_PORT" --app-dir /app &
MGMT_PID=$!

if [ "$INSTANCE_AIMED" = 1 ]; then
  supervise loop python /app/aq.py forever &
  LOOP_PID=$!
else
  LOOP_PID=""
  echo "[aq] no user mission mounted; UI is ready and the loop remains idle"
fi

cleanup() {
  kill "$STATUS_PID" "$MGMT_PID" ${LOOP_PID:+"$LOOP_PID"} >/dev/null 2>&1 || true
}
trap cleanup TERM INT
# The observability surfaces outlive the loop. A stopped loop must remain visible instead of
# taking down the very UI that should report it stopped.
wait -n "$STATUS_PID" "$MGMT_PID"
exit 1
