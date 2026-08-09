#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
project="aq-c4-test-$$"
state="$(mktemp -d "${TMPDIR:-/tmp}/aq-c4-test.XXXXXX")"
cleanup() {
  AQ_STATE_DIR="$state" scripts/compose-with-secrets.sh -p "$project" -f docker-compose.test.yml down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$state"
}
trap cleanup EXIT INT TERM
AQ_STATE_DIR="$state" scripts/compose-with-secrets.sh -p "$project" -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from c4-tests
