#!/usr/bin/env bash
# Proves the loop can ACTUALLY get a model to answer — whichever executor the interview chose.
#
# Not "the key is set". Not "the binary is on PATH". A real round-trip. An executor that is
# configured but cannot reach a model will stall the loop on its first turn, and we would
# rather know now than at 3am.
set -euo pipefail
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"
$PY - <<'PYEOF'
import json
from runner.config import Instance
from runner.executor import build

inst = Instance.load("instance.yaml")
ex = build(inst)

SCHEMA = {
    "type": "object",
    "properties": {"alive": {"type": "boolean"}, "engine": {"type": "string"}},
    "required": ["alive", "engine"],
    "additionalProperties": False,
}
reply, usage = ex.run(
    "Reply with alive=true and the name of the engine you are. Nothing else.", SCHEMA
)
if not reply.get("alive"):
    raise SystemExit("executor answered, but not affirmatively")
mode = inst.engine.mode
cost = f"${usage.cost_usd}" + (" (subscription — the plan already paid)" if mode == "subscription" else "")
print(f"{mode} mode via {reply.get('engine','?')} — {usage.tokens_in + usage.tokens_out} tokens, {cost}")
PYEOF
