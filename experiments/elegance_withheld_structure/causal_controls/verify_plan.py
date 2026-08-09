#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent; p=h/"control_plan.json"; d=json.loads(p.read_text()); assert d["frozen_before_calls"] and d["seed"]==424242 and d["repeats_per_condition"]==2 and not (h/"results").exists(); print("PASS: M6 causal/control plan frozen before calls"); print("plan_sha256="+hashlib.sha256(p.read_bytes()).hexdigest())
