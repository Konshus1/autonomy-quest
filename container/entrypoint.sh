#!/usr/bin/env bash
# One container, several processes. The rule: NOTHING dies silently.
#
# A process that exits quietly while the container stays "up" is the exact failure mode this
# whole system exists to refuse — it looks alive and isn't. So the loop runner is supervised,
# and every restart is RECORDED, not swallowed.
set -euo pipefail

echo "[aq] starting postgres..."
docker_setup() { :; }
export PGDATA=/var/lib/postgresql/data

# Postgres, via the base image's own entrypoint, in the background.
/usr/local/bin/docker-entrypoint.sh postgres &
PG_PID=$!

echo "[aq] waiting for postgres..."
until pg_isready -q -U "${POSTGRES_USER:-aq}"; do sleep 1; done

# Schema. Idempotent — re-running install must never destroy a live instance.
echo "[aq] applying schema (incl. Apache AGE)..."
psql -U "${POSTGRES_USER:-aq}" -d "${POSTGRES_DB:-autonomy_quest}" -v ON_ERROR_STOP=1 \
     -f /app/schema/001_init.sql || {
  echo "[aq] SCHEMA FAILED — refusing to start the loop on a database we could not prepare." >&2
  exit 1
}

echo "[aq] starting loop runner (supervised)..."
(
  while true; do
    python3 /app/aq.py loop || {
      code=$?
      # Record the crash IN THE DATABASE. A supervisor that restarts silently is how a
      # dead loop hides behind a healthy-looking container.
      echo "[aq] LOOP RUNNER DIED (exit $code) — recording and restarting in 30s" >&2
      psql -U "${POSTGRES_USER:-aq}" -d "${POSTGRES_DB:-autonomy_quest}" -c \
        "INSERT INTO work (kind, summary, rationale, status) VALUES
         ('system','loop runner crashed (exit $code)','supervisor restart — investigate','abandoned')" \
        >/dev/null 2>&1 || true
      sleep 30
    }
  done
) &

wait $PG_PID
