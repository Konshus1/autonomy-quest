#!/usr/bin/env zsh
# Launch living manager ccf-4407-ralph-to-aq with the #4407 goal prompt.
# Usage:
#   ./launch_ccf_4407.sh            # spawn
#   ./launch_ccf_4407.sh --register # spawn (if needed) + register bb_session_board via local API/psql best-effort
set -euo pipefail

NAME="ccf-4407-ralph-to-aq"
AQ="/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port"
PROMPT="$AQ/docs/checkpoints/task_4407/launch/ccf-4407-ralph-to-aq.GOAL.md"
SESSION="${DISPATCH_TMUX_SESSION:-cc agents}"
TASK_ID=4407

if [[ ! -f "$PROMPT" ]]; then
  echo "ERROR: goal prompt missing: $PROMPT" >&2
  exit 2
fi
if [[ ! -d "$AQ" ]]; then
  echo "ERROR: AQ worktree missing: $AQ" >&2
  exit 2
fi

# shellcheck disable=SC1090
source ~/.zshrc >/dev/null 2>&1 || true

if ! typeset -f dispatch_worker >/dev/null 2>&1; then
  if [[ -f "$HOME/misc/setup/dispatch/dispatch_worker.zsh" ]]; then
    source "$HOME/misc/setup/dispatch/dispatch_worker.zsh"
  else
    echo "ERROR: dispatch_worker not available" >&2
    exit 2
  fi
fi

if tmux list-windows -t "=$SESSION" -F '#{window_name}' 2>/dev/null | grep -Fxq "$NAME"; then
  echo "Window already exists: ${SESSION}:$NAME"
  echo "Resume with: tmux select-window -t '${SESSION}:$NAME'"
  echo "Or ccwr $NAME (from inside tmux)"
else
  echo "Spawning living manager via dispatch_worker..."
  DISPATCH_TMUX_SESSION="$SESSION" dispatch_worker claude "$NAME" "$PROMPT" "$AQ"
fi

echo "Waiting for pane to start..."
sleep 6
echo "---- pane tail ----"
tmux capture-pane -p -t "${SESSION}:${NAME}" 2>/dev/null | tail -40 || true
echo "---- end ----"
echo "Verify BB #353: prompt should be SUBMITTED (working), not staged at composer."

if [[ "${1:-}" == "--register" ]]; then
  echo "Registering bb_session_board living-manager flags (best effort)..."
  python3 - <<'PY'
import json, os, sys
try:
    import psycopg2
except ImportError:
    print("psycopg2 missing; skip board register", file=sys.stderr)
    sys.exit(0)
uri = os.environ.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
if not uri:
    # common local fallback via shell env after sourcing
    print("No SQLALCHEMY_DATABASE_URI; skip board register", file=sys.stderr)
    sys.exit(0)
# normalize SQLAlchemy URI if needed
if uri.startswith("postgresql+psycopg2://"):
    uri = uri.replace("postgresql+psycopg2://", "postgresql://", 1)
conn = psycopg2.connect(uri)
conn.autocommit = True
cur = conn.cursor()
meta = {
    "session_role": "manager",
    "keep_open_living_manager": True,
    "goal_prompt": "/Users/kevincthomas/src/autonomy-quest-wt-3981-causal-port/docs/checkpoints/task_4407/launch/ccf-4407-ralph-to-aq.GOAL.md",
    "task_id": 4407,
    "kit": "ralph_to_aq",
}
cur.execute(
    """
    INSERT INTO bb_session_board (session_name, agent_type, status, persistent, task_id, metadata, last_activity_at, updated_at)
    VALUES (%s, 'claude_code', 'active', true, %s, %s::jsonb, NOW(), NOW())
    ON CONFLICT (session_name) DO UPDATE SET
      status='active',
      persistent=true,
      task_id=EXCLUDED.task_id,
      metadata = COALESCE(bb_session_board.metadata, '{}'::jsonb) || EXCLUDED.metadata,
      last_activity_at=NOW(),
      updated_at=NOW()
    RETURNING id, session_name, persistent
    """,
    ("ccf-4407-ralph-to-aq", 4407, json.dumps(meta)),
)
row = cur.fetchone()
print("bb_session_board:", row)
cur.close()
conn.close()
PY
fi

echo "DONE. Attach: tmux attach -t '$SESSION' \\; select-window -t '$NAME'"
