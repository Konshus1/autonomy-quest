#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${AQ_HERMETIC_VERIFIER_IMAGE:-aq-hermetic-verifier:close-loop-shadow}"
RECEIPT_LOG="${AQ_SHADOW_RECEIPT_LOG:-/tmp/aq-close-loop-shadow/receipts.jsonl}"

command -v docker >/dev/null 2>&1 || { echo "shadow rig failure: docker is required" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "shadow rig failure: docker daemon is required" >&2; exit 2; }
docker build --pull --tag "$IMAGE" "$ROOT/hermetic_verifier" >/dev/null

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec python scripts/run_close_loop_shadow.py --image "$IMAGE" --receipt-log "$RECEIPT_LOG"
