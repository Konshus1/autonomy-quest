#!/usr/bin/env bash
# Push, and PROVE the remote actually moved.
#
# `git push && echo pushed` prints "pushed" when the pipeline succeeds — which is not the same as
# the remote having advanced. I told a collaborating agent on another machine that a fix was pushed,
# it wasn't where they were looking, and they lost a cycle re-running old code. They were right to
# refuse to trust my success message.
#
# A success message is a claim. The remote SHA is the fact.
set -euo pipefail
cd "$(dirname "$0")/.."

BEFORE="$(git rev-parse origin/main 2>/dev/null || echo none)"
git push origin main
git fetch -q origin
AFTER="$(git rev-parse origin/main)"
LOCAL="$(git rev-parse HEAD)"

if [ "$AFTER" != "$LOCAL" ]; then
  printf '\033[31mPUSH DID NOT LAND.\033[0m local=%s  origin/main=%s\n' "${LOCAL:0:7}" "${AFTER:0:7}" >&2
  exit 1
fi
printf '\033[32mpushed and VERIFIED\033[0m  origin/main: %s -> %s  (%s)\n' \
  "${BEFORE:0:7}" "${AFTER:0:7}" "$(git log -1 --format=%s | head -c 56)"
