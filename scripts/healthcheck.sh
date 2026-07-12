#!/usr/bin/env bash
# HEALTHY means THE LOOP IS TURNING — not that the processes are up.
#
# A container whose every PID is alive but whose loop stalled hours ago is UNHEALTHY, and
# reporting it healthy is the lie this system must never tell. So we gate on ground truth:
# has a full cycle (acted -> recorded -> LEARNED) completed recently?
set -euo pipefail
: "${AQ_STALL_MINUTES:=180}"
psql -U "${POSTGRES_USER:-aq}" -d "${POSTGRES_DB:-autonomy_quest}" -tAX -c "
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM runs r JOIN learnings l ON l.run_id = r.id
    WHERE r.completed_at > now() - interval '${AQ_STALL_MINUTES} minutes'
  ) THEN 'turning' ELSE 'STALLED' END" | grep -q '^turning$'
