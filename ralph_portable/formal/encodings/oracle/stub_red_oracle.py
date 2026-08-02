#!/usr/bin/env python3
"""STUB oracle that REFUTES its encoding (demonstrates the RED path). Deterministic, one JSON line."""
import json, sys
_ = open(sys.argv[1], encoding="utf-8").read()
print(json.dumps({"result": "RED", "reason": "claim not deductively supported (stub refutation)"}))
