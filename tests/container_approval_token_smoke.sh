#!/usr/bin/env bash
# Smoke-test the container approval-token on-ramp against a real running container.
#
# This intentionally checks the documented path:
#   docker exec <container> cat /var/run/aq/approval_token
#
# It does not certify the full interview UX. It proves the default container path gives an
# operator a usable approval token and that the token can approve a parked work row end-to-end
# through the UI API.
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE="${AQ_CONTAINER_SMOKE_IMAGE:-autonomy-quest:approval-smoke}"
NAME="${AQ_CONTAINER_SMOKE_NAME:-aq-approval-smoke-$$}"
TOKEN_FILE="${AQ_APPROVAL_TOKEN_FILE:-/var/run/aq/approval_token}"

cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -f container/Dockerfile -t "$IMAGE" .
docker run -d --name "$NAME" -e POSTGRES_PASSWORD=smoke "$IMAGE" >/dev/null

for _ in $(seq 1 120); do
  if docker exec "$NAME" /app/container-healthcheck.sh >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$NAME" /app/container-healthcheck.sh >/dev/null

token="$(docker exec "$NAME" cat "$TOKEN_FILE" | tr -d '[:space:]')"
test -n "$token"
mode="$(docker exec "$NAME" stat -c '%a' "$TOKEN_FILE")"
test "$mode" = "600"

wid="$(docker exec "$NAME" psql -U aq -d aq -tAX -c \
  "INSERT INTO work (kind, summary, rationale, status)
   VALUES ('test','container approval smoke','seeded parked work','awaiting_human')
   RETURNING id;" | awk '/^[0-9]+$/ { print; exit }')"
test -n "$wid"

body="$(docker exec "$NAME" curl -fsS -X POST \
  -H "X-AQ-Approval-Token: $token" \
  "http://127.0.0.1:8080/api/approve/$wid")"
case "$body" in
  *'"approved": true'*|*'"approved":true'*) ;;
  *) echo "approve response did not approve: $body" >&2; exit 1 ;;
esac

row="$(docker exec "$NAME" psql -U aq -d aq -tAX -c \
  "SELECT status || '|' || (approved_at IS NOT NULL)::text FROM work WHERE id=$wid;")"
test "$row" = "pending|true"

echo "PASS container approval token retrieval + approve endpoint"
