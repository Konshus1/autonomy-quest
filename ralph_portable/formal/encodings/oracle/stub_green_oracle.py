#!/usr/bin/env python3
"""STUB oracle (placeholder for hrf-2878's real RED/GREEN oracle). Emits GREEN iff the encoding
contains its integrity constraint marker (a trivial but real check). One JSON line, deterministic,
network-free, no subprocess. Replaced by a clingo-backed oracle later."""
import json, sys
payload = open(sys.argv[1], encoding="utf-8").read()
ok = ":- action(pin_dependency_versions), not holds(build_reproducible)." in payload
print(json.dumps({"result": "GREEN" if ok else "RED",
                  "reason": "integrity constraint present" if ok else "missing integrity constraint"}))
