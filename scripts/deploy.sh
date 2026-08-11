#!/usr/bin/env bash
# ONE documented command to (re)deploy the autonomy-quest live loop (#4834 DEPLOY-HYGIENE).
#
# Before this existed the deploy was undocumented and reverse-engineered, the image carried no
# version, and "health" meant process-up. That is how the live loop drifted 27h behind main and sat
# DOWN ~27h (crash-looping on missing codex auth) with nobody noticing. This script makes the
# deploy reproducible and ASSERTS a fresh heartbeat — a real DECIDE cycle — before declaring success.
#
# WHAT IT DOES
#   1. Fetches and checks out a target ref CLEAN in the build-context worktree.
#   2. Rebuilds the compose project with GIT_SHA stamped into the image (build-arg -> ENV -> /health).
#   3. Waits for the one-shot `migrate` service to complete successfully.
#   4. Asserts /health reports the deployed SHA AND a FRESH heartbeat (cycling within threshold).
#      Fails loudly if the loop is up but not cycling after a timeout.
#
# USAGE
#   scripts/deploy.sh [TARGET_REF]         # TARGET_REF default: origin/main
#
# CONFIG (env, with live-stack defaults):
#   AQ_DEPLOY_WORKTREE   build-context worktree              (default: /Users/kevincthomas/src/aq-wt-mergetrain)
#   COMPOSE_PROJECT_NAME compose project                     (default: aq-seed-c2f4834)
#   AQ_SEED_STATE        seed state dir (secrets + overlays) (default: ~/.local/state/autonomy-quest/seed-c2f4834)
#   AQ_DEPLOY_OVERLAYS   extra compose -f overlays, space-sep (default: ports.yml + selfcorr-override.yml if present)
#   AQ_HEALTH_URL        management API /health              (default: http://127.0.0.1:8090/health)
#   AQ_DEPLOY_HEALTH_TIMEOUT  seconds to wait for a fresh cycle (default: 300)
#   AQ_DEPLOY_ALLOW_DIRTY=1   permit reset --hard over uncommitted tracked changes (default: refuse)
#
# CODEX DEVICE-AUTH GOTCHA (read this):
#   The loop drives the Codex CLI and CRASH-LOOPS without a valid device login. Auth lives in the
#   `aq-codex-auth` named volume and SURVIVES redeploys — but a first deploy, a wiped volume, or an
#   expired token means the loop will never cycle and this script will fail at the heartbeat gate.
#   Fix by logging in INSIDE the app container (interactive device flow, token stays in the volume):
#       docker exec -it ${COMPOSE_PROJECT_NAME:-aq-seed-c2f4834}-app-1 codex login --device-auth
#   then re-run this script. See DEPLOY.md.
set -euo pipefail

TARGET_REF="${1:-origin/main}"
WORKTREE="${AQ_DEPLOY_WORKTREE:-/Users/kevincthomas/src/aq-wt-mergetrain}"
PROJECT="${COMPOSE_PROJECT_NAME:-aq-seed-c2f4834}"
SEED_STATE="${AQ_SEED_STATE:-$HOME/.local/state/autonomy-quest/seed-c2f4834}"
SECRET_FILE="$SEED_STATE/compose-secrets"
HEALTH_URL="${AQ_HEALTH_URL:-http://127.0.0.1:8090/health}"
HEALTH_TIMEOUT="${AQ_DEPLOY_HEALTH_TIMEOUT:-300}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

[ -d "$WORKTREE/.git" ] || [ -f "$WORKTREE/.git" ] || die "build-context worktree not found: $WORKTREE"
[ -f "$SECRET_FILE" ] || die "compose secret file not found: $SECRET_FILE (run scripts/compose-with-secrets.sh once, or set AQ_SEED_STATE)"

cd "$WORKTREE"

# --- 1. fetch + checkout clean ------------------------------------------------
log "fetching origin"
git fetch --quiet origin

if ! git diff --quiet HEAD -- 2>/dev/null; then
  if [ "${AQ_DEPLOY_ALLOW_DIRTY:-0}" = 1 ]; then
    log "WARNING: uncommitted tracked changes present; AQ_DEPLOY_ALLOW_DIRTY=1 so resetting --hard"
  else
    die "worktree $WORKTREE has uncommitted tracked changes; commit/stash them or set AQ_DEPLOY_ALLOW_DIRTY=1"
  fi
fi

RESOLVED="$(git rev-parse --verify "${TARGET_REF}^{commit}")" || die "cannot resolve ref: $TARGET_REF"
log "checking out $TARGET_REF ($RESOLVED)"
git checkout --quiet --detach "$RESOLVED"
git reset --quiet --hard "$RESOLVED"

GIT_SHA="$(git rev-parse HEAD)"
export GIT_SHA
log "deploying GIT_SHA=$GIT_SHA"

# --- 2. assemble the compose overlays ----------------------------------------
# Base compose + ports overlay + (by default) the self-correction flag overlay, mirroring the live
# stack. Override the extra overlays with AQ_DEPLOY_OVERLAYS (space-separated absolute paths).
if [ -n "${AQ_DEPLOY_OVERLAYS:-}" ]; then
  # shellcheck disable=SC2206
  OVERLAYS=($AQ_DEPLOY_OVERLAYS)
else
  OVERLAYS=("$SEED_STATE/ports.yml")
  [ -f "$SEED_STATE/selfcorr-override.yml" ] && OVERLAYS+=("$SEED_STATE/selfcorr-override.yml")
fi

COMPOSE_FLAGS=(-f docker-compose.yml)
for f in "${OVERLAYS[@]}"; do
  [ -f "$f" ] || die "compose overlay not found: $f"
  COMPOSE_FLAGS+=(-f "$f")
done

# --- 3. rebuild (GIT_SHA flows via docker-compose.yml build.args) ------------
log "rebuilding compose project '$PROJECT' (this stamps the image and restarts services)"
AQ_COMPOSE_SECRET_FILE="$SECRET_FILE" COMPOSE_PROJECT_NAME="$PROJECT" \
  scripts/compose-with-secrets.sh "${COMPOSE_FLAGS[@]}" up -d --build

# --- 4. wait for the one-shot migrate service to finish successfully ---------
log "waiting for migrate to complete"
MIGRATE_CID="$(AQ_COMPOSE_SECRET_FILE="$SECRET_FILE" COMPOSE_PROJECT_NAME="$PROJECT" \
  scripts/compose-with-secrets.sh "${COMPOSE_FLAGS[@]}" ps -aq migrate | head -n1)"
if [ -n "$MIGRATE_CID" ]; then
  MIGRATE_CODE="$(docker wait "$MIGRATE_CID")"
  [ "$MIGRATE_CODE" = 0 ] || { docker logs --tail 40 "$MIGRATE_CID" >&2 || true; die "migrate failed (exit $MIGRATE_CODE)"; }
  log "migrate completed (exit 0)"
else
  log "WARNING: migrate container not found; depends_on gate should still have enforced it"
fi

# --- 5. assert /health: right SHA AND a FRESH heartbeat ----------------------
log "asserting /health at $HEALTH_URL (SHA match + a fresh cycle within ${HEALTH_TIMEOUT}s)"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
last_reason="never got a /health response"
while [ "$(date +%s)" -lt "$deadline" ]; do
  if body="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null)"; then
    verdict="$(GIT_SHA="$GIT_SHA" python3 - "$body" <<'PY'
import json, os, sys
want = os.environ["GIT_SHA"]
try:
    h = json.loads(sys.argv[1])
except Exception as e:
    print(f"WAIT non-JSON /health ({e})"); sys.exit(0)
got = h.get("git_sha")
if got != want:
    print(f"WAIT deployed sha not live yet (health={got} want={want[:12]})"); sys.exit(0)
if h.get("cycling"):
    print(f"OK sha={got[:12]} cycling last={h.get('last_cycle_at')} ({h.get('seconds_since_last_cycle')}s ago)")
    sys.exit(0)
secs = h.get("seconds_since_last_cycle")
if secs is None:
    print("WAIT sha live but no cycle heartbeat yet (loop still starting / first cycle in flight)")
else:
    print(f"WAIT sha live but last cycle {secs}s ago > {h.get('cycling_threshold_seconds')}s")
sys.exit(0)
PY
)"
    case "$verdict" in
      OK*) log "$verdict"; log "DEPLOY OK — $GIT_SHA is live and cycling"; exit 0 ;;
      *)   last_reason="${verdict#WAIT }" ;;
    esac
  else
    last_reason="/health unreachable at $HEALTH_URL (services still starting?)"
  fi
  sleep 5
done

cat >&2 <<EOF
[deploy] ERROR: heartbeat gate FAILED after ${HEALTH_TIMEOUT}s.
[deploy]   last status: $last_reason
[deploy]   The image is deployed but the loop is not confirmed cycling. The usual cause is the
[deploy]   Codex device-auth gotcha — the loop crash-loops without a valid login:
[deploy]       docker exec -it ${PROJECT}-app-1 codex login --device-auth
[deploy]   Then re-run this script. Also check:  docker logs --tail 80 ${PROJECT}-app-1
EOF
exit 1
