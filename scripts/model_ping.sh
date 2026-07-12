#!/usr/bin/env bash
# Proves the model gateway can complete a REAL call. Not "the key is set" — a completion,
# round-tripped, with the provider's own token count. A gateway that is up but cannot reach
# a model will stall the loop on its first turn, and we would rather know now.
set -euo pipefail
python3 - <<'PY'
from runner.config import Instance
from runner.gateway import Gateway
inst = Instance.load("instance.yaml")
gw = Gateway(inst.models)
text, usage = gw._call("cheap", system="Reply with the single word: alive", user="ping")
if not text.strip():
    raise SystemExit("model returned an empty completion")
print(f"{usage.tokens_in + usage.tokens_out} tokens, ${usage.cost_usd}")
PY
