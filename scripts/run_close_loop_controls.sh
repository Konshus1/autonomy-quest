#!/usr/bin/env bash
# Build and execute the non-skipping close-loop Docker isolation controls.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
IMAGE="${AQ_CLOSE_LOOP_VERIFIER_IMAGE:-aq-close-loop-verifier:control-$$}"
OWN_IMAGE=0
rig_fail() { printf 'CLOSE-LOOP-SANDBOX-RIG-FAILURE: %s\n' "$*" >&2; exit 2; }
cleanup() {
  if [[ "$OWN_IMAGE" == 1 && "${AQ_KEEP_CLOSE_LOOP_IMAGE:-0}" != 1 ]]; then
    docker image rm -f "$IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null 2>&1 || rig_fail "Docker executable is mandatory (controls may not skip)"
docker info >/dev/null 2>&1 || rig_fail "Docker daemon is mandatory (controls may not skip)"
PY_BIN="${AQ_PY:-python3}"
command -v "$PY_BIN" >/dev/null 2>&1 || rig_fail "missing Python interpreter: $PY_BIN"
"$PY_BIN" -c 'import pytest' >/dev/null 2>&1 || rig_fail "$PY_BIN is missing mandatory pytest"

if [[ -z "${AQ_CLOSE_LOOP_VERIFIER_IMAGE:-}" ]]; then
  OWN_IMAGE=1
  docker build --pull \
    --file "$ROOT/container/verifier.Dockerfile" --tag "$IMAGE" "$ROOT" \
    || rig_fail "pinned verifier image build failed"
else
  docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || rig_fail "supplied verifier image does not exist: $IMAGE"
fi

set +e
OUT=$(AQ_CLOSE_LOOP_VERIFIER_IMAGE="$IMAGE" "$PY_BIN" -m pytest \
  tests/test_close_loop_sandbox.py -q -rs -p no:cacheprovider 2>&1)
RC=$?
set -e
printf '%s\n' "$OUT"

if grep -qiE 'skipped|xfailed|xpassed|deselected' <<<"$OUT"; then
  rig_fail "a mandatory isolation control did not execute cleanly"
fi
if grep -qiE 'error during collection|ModuleNotFoundError|ImportError|no tests ran' <<<"$OUT"; then
  rig_fail "pytest could not collect the mandatory isolation controls"
fi
if [[ "$RC" -ne 0 ]]; then
  printf 'CLOSE-LOOP-SANDBOX CONTROL RED\n' >&2
  exit 1
fi
PASSED=$(grep -oE '[0-9]+ passed' <<<"$OUT" | head -1 | cut -d' ' -f1 || true)
[[ "$PASSED" == 4 ]] || rig_fail "expected exactly 4 mandatory controls, observed ${PASSED:-0}"
printf 'CLOSE-LOOP-SANDBOX OK: 4/4 mandatory controls passed\n'
