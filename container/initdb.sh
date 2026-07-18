#!/usr/bin/env bash
# Runs inside the official Postgres init hook when a fresh volume is first created.
set -euo pipefail

echo "[aq] applying full schema (incl. Apache AGE)..."
for f in /app/schema/*.sql; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f" >/dev/null
  echo "[aq] applied: ${f#/app/}"
done
echo "[aq] schema ready"
