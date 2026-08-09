#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
p=Path(__file__).with_name("analysis_plan.json"); d=json.loads(p.read_text()); assert d["frozen_before_generation"] and len(d["problems"])==15 and d["arms"]==["direct","semantic","structural","human"] and not Path(__file__).with_name("results").exists(); print("PASS: M5 analysis plan frozen before generation"); print("plan_sha256="+hashlib.sha256(p.read_bytes()).hexdigest())
