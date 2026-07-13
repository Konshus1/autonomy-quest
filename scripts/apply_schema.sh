#!/usr/bin/env bash
# Apply the schema, honouring the graph choice the interview made. Idempotent.
#
# ONE implementation, used by install.sh AND testbox/sync.sh. It was duplicated, and the copies
# disagreed: sync applied 001_init.sql verbatim on a graph:none instance, where the AGE lines
# cannot work, and reported "did not apply cleanly". Two places implementing the same rule is one
# place implementing it wrong.
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck source=scripts/_env.sh
. "$(dirname "${BASH_SOURCE[0]}")/_env.sh"

: "${AQ_DB_URL:?AQ_DB_URL not set}"
GRAPH="${AQ_GRAPH:-none}"

for f in schema/*.sql; do
  if [ "$GRAPH" = "age" ]; then
    psql "$AQ_DB_URL" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null
  else
    # graph:none — the AGE lines cannot run, and that is a CHOICE, not a failure. Strip them.
    grep -v -E "^(CREATE EXTENSION IF NOT EXISTS age|LOAD 'age'|SET search_path = ag_catalog|SELECT create_graph)" "$f" \
      | psql "$AQ_DB_URL" -q -v ON_ERROR_STOP=1 >/dev/null
  fi
  echo "  applied: $f"
done
