#!/usr/bin/env bash
# Wire the blackboard into the resident agent, so it — and any other agent — can ASK the
# instance what it knows instead of starting from zero.
#
# MCP servers do NOT inherit the parent environment. The DB url must be passed explicitly, or
# every tool fails with a missing AQ_DB_URL and the agent concludes the board is broken. (It did.)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run install.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

AGENT="${1:-codex}"
PY="$PWD/.venv/bin/python"
SRV="$PWD/mcp/bb_server.py"

case "$AGENT" in
  codex)
    codex mcp remove autonomy-quest >/dev/null 2>&1 || true
    codex mcp add autonomy-quest \
      --env AQ_DB_URL="$AQ_DB_URL" --env AQ_AGENT_NAME="${AQ_AGENT_NAME:-loop}" \
      -- "$PY" "$SRV"
    ;;
  claude|claude-code)
    claude mcp remove autonomy-quest >/dev/null 2>&1 || true
    claude mcp add autonomy-quest --env AQ_DB_URL="$AQ_DB_URL" -- "$PY" "$SRV"
    ;;
  *) echo "unknown agent: $AGENT (codex | claude)" >&2; exit 1 ;;
esac
echo "[aq] blackboard wired into $AGENT — it can now ask bb_context / bb_search / bb_learnings"
