#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent; p=h/"plan.json"; d=json.loads(p.read_text()); assert d["frozen_before_generation"] and d["n_per_cell"]>=15 and d["total_generations"]==135 and len(d["mechanisms"])==3 and not (h/"results").exists(); ctx=(h/"../causal_controls/contexts.json").resolve(); assert hashlib.sha256(ctx.read_bytes()).hexdigest()==d["contexts_sha256"]; print("PASS: distributional ablation preregistered"); print("cases=3 conditions=3 n=15 total=135"); print("plan_sha256="+hashlib.sha256(p.read_bytes()).hexdigest())
