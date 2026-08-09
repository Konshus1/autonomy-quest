#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent; p=h/"control_plan.json"; d=json.loads(p.read_text()); assert d["frozen_before_calls"] and d["model"]=="codex subscription gpt-5.6-sol" and d["cases"]==["B13","C03","C20"] and d["repeats_per_condition"]==3 and not (h/"results").exists(); print("PASS: Codex causal/control plan frozen before calls"); print("plan_sha256="+hashlib.sha256(p.read_bytes()).hexdigest()); print("strict_claim_gate="+d["strict_claim_gate"])
