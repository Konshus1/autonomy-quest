#!/bin/sh
# Final exact composed acceptance for Task 4834 Scope B. No authority is added here.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${AQ_SCOPE_B_M6_TARGET_COMMIT:?set AQ_SCOPE_B_M6_TARGET_COMMIT to the exact full commit SHA}"
echo "$TARGET" | grep -Eq '^[0-9a-f]{40}$' || { echo "M6-RIG-FAIL: target commit is not a full SHA" >&2; exit 2; }
HEAD=$(git -C "$ROOT" rev-parse HEAD)
[ "$TARGET" = "$HEAD" ] || { echo "M6-RIG-FAIL: target $TARGET != HEAD $HEAD" >&2; exit 2; }
git -C "$ROOT" diff --quiet && git -C "$ROOT" diff --cached --quiet   || { echo "M6-RIG-FAIL: tracked worktree differs from $TARGET" >&2; exit 2; }
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ] || { echo "M6-RIG-FAIL: untracked worktree drift is present" >&2; exit 2; }
TREE=$(git -C "$ROOT" rev-parse HEAD^{tree})
PORT="${AQ_C4_PORT:-$((55000 + ($$ % 9000)))}"
LOG="${TMPDIR:-/tmp}/aq-m6-$$.log"
set +e
AQ_C4_PORT="$PORT" "$ROOT/scripts/run_c4_controls.sh" >"$LOG" 2>&1
RC=$?
set -e
cat "$LOG"
[ "$RC" -eq 0 ] || { echo "M6 acceptance underlying exit $RC" >&2; exit "$RC"; }
for MARKER in M6_DEFAULT_FLAG_OFF M6_ACT_CREDENTIAL_DENIAL M6_EVALUATOR_RESTART_RECOVERY 'M6 COMPOSED CHAIN OK'; do
  COUNT=$(grep -Fc "$MARKER" "$LOG" || true)
  [ "$COUNT" = "1" ] || { echo "M6-RIG-FAIL: marker '$MARKER' count was $COUNT" >&2; exit 2; }
done
grep -q '^C4 OK: 30/30 principal-isolation controls ran against real principals and passed.$' "$LOG"   || { echo "M6-RIG-FAIL: exact principal summary absent" >&2; exit 2; }
rm -f "$LOG"
echo "M6 ACCEPTANCE OK: commit=$HEAD tree=$TREE fresh exact principals, grounded block/allow/use/outcome/restart/demotion, ACT credential denial, default-off"
