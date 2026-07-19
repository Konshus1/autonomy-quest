#!/usr/bin/env bash
# Container health = the initialized substrate is available. The autonomous loop is started later,
# after a coding agent has interviewed the human and aimed this instance.
set -euo pipefail

user="${POSTGRES_USER:-aq}"
db="${POSTGRES_DB:-aq}"

pg_isready -q --username "$user" --dbname "$db"

psql --username "$user" --dbname "$db" -tAX -v ON_ERROR_STOP=1 <<'SQL' | grep -qx "ok"
SELECT CASE WHEN
  EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age')
  AND EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'autonomy_quest')
  AND to_regclass('public.work') IS NOT NULL
  AND to_regclass('public.runs') IS NOT NULL
  AND to_regclass('public.learnings') IS NOT NULL
  AND to_regclass('public.bb_notes') IS NOT NULL
  AND to_regclass('public.hibernation') IS NOT NULL
  AND to_regclass('public.heartbeat') IS NOT NULL
  AND to_regclass('public.shared_learnings') IS NOT NULL
  THEN 'ok' ELSE 'not-ready' END;
SQL
