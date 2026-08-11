#!/usr/bin/env bash
# DEPLOY-HYGIENE liveness probe (#4834). Poll the management API /health and ASSERT the loop is
# actually cycling — a real DECIDE cycle completed within the freshness threshold — not merely that
# the process is up. Cron/monitor-friendly: exits 0 iff cycling, non-zero otherwise, and prints a
# one-line reason so an alert has something to say.
#
#   scripts/check_loop_alive.sh                 # checks http://127.0.0.1:8090/health
#   AQ_HEALTH_URL=http://127.0.0.1:8090/health scripts/check_loop_alive.sh
#
# Exit codes:  0 cycling  |  2 up-but-not-cycling (the ~27h-down signal)  |  3 /health unreachable
#
# NOTE: a DELIBERATELY hibernating loop (stopped, waiting for a human) also reads not-cycling by
# design — that is a human-acknowledged stop, distinct from a crash. Cross-check the `heartbeat`
# table state (hibernating vs turning) or the hibernation record before paging on a known stop.
set -euo pipefail

url="${AQ_HEALTH_URL:-http://127.0.0.1:8090/health}"

body="$(curl -fsS --max-time 10 "$url" 2>/dev/null)" || {
  echo "LOOP CHECK: FAIL — /health unreachable at $url"
  exit 3
}

python3 - "$body" <<'PY'
import json, sys
try:
    h = json.loads(sys.argv[1])
except Exception as e:
    print(f"LOOP CHECK: FAIL — /health returned non-JSON ({e})")
    sys.exit(3)

cycling = bool(h.get("cycling"))
secs = h.get("seconds_since_last_cycle")
thr = h.get("cycling_threshold_seconds")
sha = h.get("git_sha") or "unknown"
last = h.get("last_cycle_at") or "never"

if cycling:
    print(f"LOOP CHECK: OK — cycling (last_cycle_at={last}, {secs}s ago, sha={sha})")
    sys.exit(0)

if secs is None:
    print(f"LOOP CHECK: FAIL — no cycle heartbeat recorded yet (last_cycle_at={last}, sha={sha})")
else:
    print(f"LOOP CHECK: FAIL — up but NOT cycling: last cycle {secs}s ago > {thr}s threshold "
          f"(sha={sha})")
sys.exit(2)
PY
