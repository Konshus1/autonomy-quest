#!/usr/bin/env bash
# HEALTHY means the loop is doing the RIGHT thing — which is not always "turning".
#
# Three distinct states, and conflating them is how a guard kills the thing it guards:
#
#   turning       a full cycle (acted -> recorded -> LEARNED) completed recently.  HEALTHY
#   hibernating   stuck, stopped spending, waiting for a human. It did the RIGHT
#                 thing. Killing and restarting it just walks it back into the wall.  HEALTHY
#   rate_limited  the plan is used up. Nothing is wrong.                            HEALTHY
#   STALLED       none of the above, and no recent cycle. Something IS wrong.       UNHEALTHY
#
# A hibernating loop completes no cycles. If we gated only on 'a cycle completed recently' — which
# we did — our own healthcheck would report a correctly-waiting loop as dead and restart it.
set -euo pipefail
: "${AQ_STALL_MINUTES:=180}"
PSQL="psql ${AQ_DB_URL:-} -tAX -c"

# 1. Turning? (ground truth: a completed run JOINed to a learning)
TURNING="$($PSQL "select exists (
  select 1 from runs r join learnings l on l.run_id = r.id
  where r.completed_at > now() - interval '${AQ_STALL_MINUTES} minutes')" 2>/dev/null || echo f)"
[ "$TURNING" = "t" ] && { echo "turning"; exit 0; }

# 2. Deliberately waiting? An OPEN hibernation is a correct, healthy state — but only if the
#    process is actually ALIVE, which the heartbeat proves. Hibernation row + no heartbeat = dead.
WAITING="$($PSQL "select exists (
  select 1 from hibernation h
  where h.resumed_at is null
    and exists (select 1 from heartbeat b
                where b.state='hibernating' and b.beat_at > now() - interval '5 minutes'))" 2>/dev/null || echo f)"
[ "$WAITING" = "t" ] && { echo "hibernating (waiting on a human — this is correct, not a fault)"; exit 0; }

# rate_limited is healthy ONLY UNTIL ITS DEADLINE. Without that bound, a loop stuck beating
# rate_limited forever would report healthy forever — it isn't turning, it isn't hibernating, and
# nothing would ever contradict it. Past the deadline and still rate-limited, something is WRONG.
# The comparison is against now() IN THE DATABASE, never a process clock.
RL="$($PSQL "select exists (
  select 1 from heartbeat
  where state='rate_limited'
    and beat_at > now() - interval '30 minutes'
    and retry_until is not null
    and now() < retry_until)" 2>/dev/null || echo f)"
[ "$RL" = "t" ] && { echo "rate_limited (the plan is busy, and it has a deadline; nothing is wrong)"; exit 0; }

echo "STALLED — no cycle in ${AQ_STALL_MINUTES}m, not hibernating, not rate-limited" >&2
exit 1
