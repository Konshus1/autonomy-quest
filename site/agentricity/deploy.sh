#!/usr/bin/env bash
#
# Deterministic build + deploy for the Agentricity marketing site (ONE isolated Azure ACA app).
#
# This script is HARD-SCOPED to the agentricity-site app. The target app/rg/registry are constants
# below, NOT arguments — so it can never deploy over prod talkingback. Run it from anywhere; it
# resolves the repo root from its own location.
#
#   ./site/agentricity/deploy.sh            # build current commit, deploy, health-check
#   ./site/agentricity/deploy.sh <tag>      # deploy an explicit image tag (must already be built/pushed
#                                           #   if --no-build) — default builds+pushes this tag
#   ./site/agentricity/deploy.sh --smoke    # also POST a real signup smoke test (writes+purges a row)
#
# What it encodes (learned the hard way — see DEPLOY.md "Applied to production"):
#   * amd64 build via buildx (ACA is linux/amd64; a native arm64 build silently misbehaves).
#   * build context = repo root, so schema/033_signups.sql bakes in.
#   * a UNIQUE per-run env stamp is set on every deploy, which FORCES a fresh revision — the only
#     reliable way for an updated secret VALUE (e.g. a rotated SIGNUPS_DB_DSN) to be re-injected.
#   * asserts the required runtime env (SIGNUPS_DB_DSN secret ref + AQ_SIGNUP_TRUSTED_PROXY_HOPS=1).
#
set -euo pipefail

# --------------------------------------------------------------------------- hard-scoped constants
readonly APP="agentricity-site"
readonly RG="agentricity-rg"
readonly ACR="talkingback"                         # registry name (az acr) — shared, image is isolated
readonly REGISTRY="talkingback.azurecr.io"
readonly IMAGE_REPO="agentricity-site"
readonly PLATFORM="linux/amd64"
readonly DOCKERFILE_REL="site/agentricity/Dockerfile"

# Refuse to ever touch prod talkingback, even if someone edits a constant by mistake.
if [[ "$APP" == *"talkingback"* || "$RG" == "talkingback-rg" ]]; then
  echo "SAFETY ABORT: this script must never target talkingback ($APP / $RG)." >&2
  exit 2
fi

# --------------------------------------------------------------------------- args
DO_BUILD=1
DO_SMOKE=0
TAG=""
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --smoke)    DO_SMOKE=1 ;;
    --*)        echo "unknown flag: $arg" >&2; exit 2 ;;
    *)          TAG="$arg" ;;
  esac
done

# --------------------------------------------------------------------------- locate repo root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "$DOCKERFILE_REL" ]]; then
  echo "ABORT: $DOCKERFILE_REL not found from repo root $REPO_ROOT" >&2
  exit 2
fi

# Default the tag to the current commit sha (short). Warn on a dirty tree so the tag is honest.
if [[ -z "$TAG" ]]; then
  TAG="$(git rev-parse --short HEAD)"
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "WARN: working tree is dirty; image tag '$TAG' will not fully reflect what is built." >&2
  fi
fi
readonly IMAGE="$REGISTRY/$IMAGE_REPO:$TAG"

echo "==> target : $APP ($RG)"
echo "==> image  : $IMAGE   (platform $PLATFORM)"
echo "==> build  : $([[ $DO_BUILD == 1 ]] && echo yes || echo 'no (deploy existing tag)')"

# --------------------------------------------------------------------------- preflight
command -v az     >/dev/null || { echo "ABORT: az CLI not found" >&2; exit 2; }
command -v docker >/dev/null || { echo "ABORT: docker not found" >&2; exit 2; }
az account show >/dev/null 2>&1 || { echo "ABORT: not logged in to Azure (az login)" >&2; exit 2; }

# --------------------------------------------------------------------------- build + push (amd64)
if [[ $DO_BUILD == 1 ]]; then
  echo "==> az acr login -n $ACR"
  az acr login -n "$ACR" >/dev/null
  echo "==> buildx build --platform $PLATFORM --push"
  docker buildx build --platform "$PLATFORM" -f "$DOCKERFILE_REL" -t "$IMAGE" --push .
fi

# --------------------------------------------------------------------------- deploy (forces fresh rev)
# A unique per-run stamp guarantees a new revision every deploy, so a rotated secret VALUE is picked
# up (an `az containerapp update --image` alone will not re-inject an unchanged secret ref, and a
# `revision restart` is unreliable for secret-value changes — see DEPLOY.md).
STAMP="${TAG}-$(date +%s)"
echo "==> deploy image + DEPLOY_STAMP=$STAMP (forces a fresh revision)"
az containerapp update -n "$APP" -g "$RG" \
  --image "$IMAGE" \
  --set-env-vars "DEPLOY_STAMP=$STAMP" "AQ_SIGNUP_TRUSTED_PROXY_HOPS=1" \
  -o none

# --------------------------------------------------------------------------- assert required wiring
echo "==> verifying runtime wiring"
DSN_REF="$(az containerapp show -n "$APP" -g "$RG" \
  --query "properties.template.containers[0].env[?name=='SIGNUPS_DB_DSN'].secretRef | [0]" -o tsv 2>/dev/null || true)"
if [[ -z "$DSN_REF" || "$DSN_REF" == "None" ]]; then
  echo "WARN: SIGNUPS_DB_DSN secret ref is NOT set — signups will fall back to a NON-durable in-memory" >&2
  echo "      store. Set it: az containerapp secret set -n $APP -g $RG --secrets signups-db-dsn=<writer-dsn>" >&2
  echo "      then re-run this script so a fresh revision injects it." >&2
else
  echo "    SIGNUPS_DB_DSN -> secretref:$DSN_REF   (durable store)"
fi

# --------------------------------------------------------------------------- health check
FQDN="$(az containerapp show -n "$APP" -g "$RG" --query "properties.configuration.ingress.fqdn" -o tsv)"
echo "==> waiting for health at https://$FQDN/healthz"
if curl -fsS --retry 20 --retry-all-errors --retry-delay 4 --max-time 20 "https://$FQDN/healthz" >/dev/null; then
  echo "    healthz OK"
else
  echo "ABORT: health check never went green" >&2
  exit 1
fi

# --------------------------------------------------------------------------- optional signup smoke
if [[ $DO_SMOKE == 1 ]]; then
  echo "==> signup smoke test (writes then purges one row)"
  PROBE="deploy-smoke-$(openssl rand -hex 4)@agentricity.com"
  RESP="$(curl -fsS --max-time 20 -X POST "https://$FQDN/api/signup" \
    -H 'content-type: application/json' -d "{\"email\":\"$PROBE\",\"source\":\"deploy-smoke\"}")"
  echo "    response: $RESP"
  case "$RESP" in
    *'"status":"recorded"'*) echo "    durable write OK — remember to purge $PROBE from aq_signups" ;;
    *) echo "WARN: smoke signup did not record durably: $RESP" >&2 ;;
  esac
fi

REV="$(az containerapp revision list -n "$APP" -g "$RG" \
  --query "reverse(sort_by([].{n:name,c:properties.createdTime}, &c))[0].n" -o tsv 2>/dev/null || true)"
echo "==> DONE. $APP is serving $IMAGE"
echo "    revision: ${REV:-?}"
echo "    url:      https://$FQDN/"
