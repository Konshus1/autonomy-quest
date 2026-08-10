#!/usr/bin/env bash
# Run the Scope-B PostgreSQL trust-boundary control in a fresh exact-Compose database.
# Missing Docker/PostgreSQL/migration/principal inputs are hard failures; there is no skip path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TARGET_COMMIT="${AQ_SCOPE_B_TARGET_COMMIT:-$(git rev-parse HEAD)}"
RESOLVED_TARGET="$(git rev-parse "${TARGET_COMMIT}^{commit}")"
ACTUAL_HEAD="$(git rev-parse HEAD)"
if [[ "$TARGET_COMMIT" != "$RESOLVED_TARGET" ]]; then
  printf 'M0 exact-ref error: target must be the full commit SHA (got %s, resolved %s)\n' \
    "$TARGET_COMMIT" "$RESOLVED_TARGET" >&2
  exit 2
fi

# The production/build inputs must equal TARGET_COMMIT. For baseline M0 only the two checked-in
# control files may be injected into that target tree; all other tracked or untracked drift fails.
SUT_PATHS=(
  .dockerignore docker-compose.yml requirements.txt aq.py
  container management runner ui schema data scripts ralph_portable workflows
  templates/running-a-business/instance.yaml
)
TREE_DRIFT="$({
  git diff --name-only "$TARGET_COMMIT" -- "${SUT_PATHS[@]}"
  git ls-files --others --exclude-standard -- "${SUT_PATHS[@]}"
} | LC_ALL=C sort -u)"
unexpected=()
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    scripts/prove_scope_b_governance_compose.sh|scripts/scope_b_governance_harness.py) ;;
    *) unexpected+=("$path") ;;
  esac
done <<<"$TREE_DRIFT"
if (( ${#unexpected[@]} != 0 )); then
  printf 'M0 exact-ref error: production/build drift from %s:\n' "$TARGET_COMMIT" >&2
  printf '  %s\n' "${unexpected[@]}" >&2
  exit 2
fi

PROJECT="aqscopebm0-${TARGET_COMMIT:0:8}-$(python3 -c 'import secrets; print(secrets.token_hex(6))')"
STATE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aq-scope-b-m0.XXXXXX")"
RECEIPT="${AQ_SCOPE_B_RECEIPT:-$ROOT/artifacts/scope_b/m0_first_red_${TARGET_COMMIT:0:8}.log}"
mkdir -p "$(dirname "$RECEIPT")"

compose() {
  AQ_STATE_DIR="$STATE_DIR" "$ROOT/scripts/compose-with-secrets.sh" -p "$PROJECT" "$@"
}
cleanup() {
  primary_rc=$?
  trap - EXIT INT TERM
  set +e
  compose down -v --remove-orphans >/dev/null 2>&1
  cleanup_rc=$?
  set -e
  if [[ "$cleanup_rc" == "0" ]]; then
    rm -rf "$STATE_DIR"
  else
    printf 'Scope-B cleanup ERROR: project=%s state_dir=%s cleanup_exit=%s\n' \
      "$PROJECT" "$STATE_DIR" "$cleanup_rc" >&2
    printf 'Recovery: AQ_STATE_DIR=%q %q -p %q down -v --remove-orphans\n' \
      "$STATE_DIR" "$ROOT/scripts/compose-with-secrets.sh" "$PROJECT" >&2
  fi
  if [[ -f "$RECEIPT" ]]; then
    printf 'cleanup_exit=%s\n' "$cleanup_rc" >>"$RECEIPT"
  fi
  if [[ "$primary_rc" == "0" && "$cleanup_rc" != "0" ]]; then
    exit 2
  fi
  exit "$primary_rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The random namespace must still be empty; attaching to any existing stack is forbidden.
docker info >/dev/null
preexisting_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
preexisting_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
if [[ -n "$preexisting_containers" || -n "$preexisting_volumes" ]]; then
  printf 'M0 isolation error: generated project namespace already exists: %s\n' "$PROJECT" >&2
  exit 2
fi

# Generate a wrapper-owned secret file. It is deleted only after successful Compose cleanup.
compose config >/dev/null
CONFIG_SHA256="$(compose config | python3 -c \
  'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
file_sha256() {
  python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}
HARNESS_SHA256="$(file_sha256 "$ROOT/scripts/scope_b_governance_harness.py")"
WRAPPER_SHA256="$(file_sha256 "$ROOT/scripts/prove_scope_b_governance_compose.sh")"
# shellcheck disable=SC1090
set -a
. "$STATE_DIR/compose-secrets"
set +a

export AQ_SCOPE_B_TARGET_COMMIT="$TARGET_COMMIT"
export AQ_SCOPE_B_OWNER_DSN="postgresql://aq_owner:${AQ_DB_OWNER_PASSWORD}@postgres:5432/aq"
export AQ_SCOPE_B_LOOP_DSN="postgresql://aq_loop:${AQ_LOOP_DB_PASSWORD}@postgres:5432/aq"
export AQ_SCOPE_B_ACTOR_DSN="postgresql://aq_actor:${AQ_ACTOR_DB_PASSWORD}@postgres:5432/aq"
export AQ_SCOPE_B_GOVERNANCE_DSN="postgresql://aq_governance:${AQ_GOVERNANCE_DB_PASSWORD}@postgres:5432/aq"
export AQ_SCOPE_B_EVALUATOR_DSN="postgresql://aq_evaluator:${AQ_EVALUATOR_DB_PASSWORD}@postgres:5432/aq"

printf 'Scope-B M0: building target production tree %s plus injected control %s\n' \
  "$TARGET_COMMIT" "$HARNESS_SHA256"
compose build governance evaluator migrate
IMAGE_ID="$(docker image inspect -f '{{.Id}}' "${PROJECT}-governance" 2>/dev/null || true)"
if [[ -z "$IMAGE_ID" ]]; then
  printf 'M0 image error: governance image id is empty\n' >&2
  exit 2
fi
compose up -d --wait postgres
compose run --rm migrate

{
  printf 'SCOPE_B_M0_RECEIPT_V2\n'
  printf 'target_commit=%s\n' "$TARGET_COMMIT"
  printf 'working_head=%s\n' "$ACTUAL_HEAD"
  printf 'production_tree=target_commit_plus_injected_scope_b_control\n'
  printf 'project=%s\n' "$PROJECT"
  printf 'compose_config_sha256=%s\n' "$CONFIG_SHA256"
  printf 'governance_image_id=%s\n' "$IMAGE_ID"
  printf 'probe_sha256=%s\n' "$HARNESS_SHA256"
  printf 'wrapper_sha256=%s\n' "$WRAPPER_SHA256"
  printf 'started_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$RECEIPT"

set +e
compose run --rm --no-deps --entrypoint python \
  -e AQ_SCOPE_B_TARGET_COMMIT \
  -e AQ_SCOPE_B_OWNER_DSN \
  -e AQ_SCOPE_B_LOOP_DSN \
  -e AQ_SCOPE_B_ACTOR_DSN \
  -e AQ_SCOPE_B_GOVERNANCE_DSN \
  -e AQ_SCOPE_B_EVALUATOR_DSN \
  governance /app/scripts/scope_b_governance_harness.py 2>&1 | tee -a "$RECEIPT"
control_rc=${PIPESTATUS[0]}
set -e
printf 'control_exit=%s\n' "$control_rc" | tee -a "$RECEIPT"
printf 'finished_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$RECEIPT"
exit "$control_rc"
