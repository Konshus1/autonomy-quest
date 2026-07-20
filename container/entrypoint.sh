#!/usr/bin/env bash
# One container, one substrate.
#
# The container path is the infra bootstrap: Postgres + Apache AGE + the full schema, ready for a
# coding agent to aim with the interview. It must not start an unaimed autonomous loop, and it must
# not crash-loop on a command that does not exist.
set -euo pipefail

export POSTGRES_USER="${POSTGRES_USER:-aq}"
export POSTGRES_DB="${POSTGRES_DB:-aq}"
export PGDATA="${PGDATA:-/var/lib/postgresql/data}"
export AQ_DB_URL="${AQ_DB_URL:-postgresql://${POSTGRES_USER}@/${POSTGRES_DB}}"
export AQ_GRAPH="${AQ_GRAPH:-age}"
export AQ_UI_PORT="${AQ_UI_PORT:-8080}"
export AQ_UI_BIND="${AQ_UI_BIND:-0.0.0.0}"
export AQ_APPROVAL_TOKEN_FILE="${AQ_APPROVAL_TOKEN_FILE:-/var/run/aq/approval_token}"
if [ -z "${AQ_APPROVAL_TOKEN:-}" ]; then
  AQ_APPROVAL_TOKEN="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  export AQ_APPROVAL_TOKEN
  echo "[aq] generated AQ_APPROVAL_TOKEN for UI approvals."
else
  export AQ_APPROVAL_TOKEN
fi
mkdir -p "$(dirname "$AQ_APPROVAL_TOKEN_FILE")"
chmod 700 "$(dirname "$AQ_APPROVAL_TOKEN_FILE")"
OLD_UMASK="$(umask)"
umask 077
printf '%s\n' "$AQ_APPROVAL_TOKEN" > "$AQ_APPROVAL_TOKEN_FILE"
umask "$OLD_UMASK"
chmod 600 "$AQ_APPROVAL_TOKEN_FILE"
echo "[aq] retrieve the approval token with: docker exec <container> cat $AQ_APPROVAL_TOKEN_FILE"

echo "[aq] starting Postgres substrate as role '${POSTGRES_USER}' database '${POSTGRES_DB}'"
/usr/local/bin/docker-entrypoint.sh "$@" &
PG_PID=$!

echo "[aq] waiting for initialized substrate..."
until /app/container-healthcheck.sh >/dev/null 2>&1; do
  if ! kill -0 "$PG_PID" >/dev/null 2>&1; then
    wait "$PG_PID"
    exit $?
  fi
  sleep 1
done

record_crash() {
  local component="$1"
  local code="$2"
  psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c \
    "INSERT INTO work (kind, summary, rationale, status) VALUES
     ('system', '${component} crashed (exit ${code})',
      'container supervisor restart - investigate', 'abandoned')" \
    >/dev/null 2>&1 || true
}

supervise_ui() {
  while true; do
    echo "[aq] starting UI on :${AQ_UI_PORT}"
    python3 /app/aq.py ui || {
      code=$?
      echo "[aq] UI crashed (exit ${code}); recording and restarting in 5s" >&2
      record_crash "ui" "$code"
      sleep 5
    }
  done
}

supervise_loop() {
  while true; do
    echo "[aq] starting aimed loop"
    python3 /app/aq.py forever || {
      code=$?
      echo "[aq] loop crashed (exit ${code}); recording and restarting in 30s" >&2
      record_crash "loop" "$code"
      sleep 30
    }
  done
}

if [ -f /app/instance.yaml ]; then
  INSTANCE_AIMED=1
else
  INSTANCE_AIMED=0
  # The UI treats "no mission configured yet" as data, not an exception. Give it a readable empty
  # instance file without mistaking that placeholder for an aimed loop configuration.
  printf '{}\n' > /app/instance.yaml
fi

supervise_ui &
UI_SUPERVISOR_PID=$!

if [ "$INSTANCE_AIMED" = "1" ]; then
  supervise_loop &
  LOOP_SUPERVISOR_PID=$!
else
  LOOP_SUPERVISOR_PID=""
  echo "[aq] no /app/instance.yaml mounted; substrate and UI are ready, loop remains idle."
  echo "[aq] next: complete the interview, provide instance.yaml/.env, then run aq.py once|forever."
fi

cleanup() {
  kill "$UI_SUPERVISOR_PID" >/dev/null 2>&1 || true
  if [ -n "$LOOP_SUPERVISOR_PID" ]; then
    kill "$LOOP_SUPERVISOR_PID" >/dev/null 2>&1 || true
  fi
  kill "$PG_PID" >/dev/null 2>&1 || true
}
trap cleanup TERM INT

wait "$PG_PID"
