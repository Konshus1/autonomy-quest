#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent; d=json.loads((h/"control_spec.json").read_text()); p=d["pre_registration"]; assert p["frozen_before_generation"] and p["replications"]==3 and p["arms"]==["direct","structural"]; assert len(d["tasks"])==3; assert not (h/"results").exists(); print("PASS: M4 preregistration frozen before generation"); print("spec_sha256="+hashlib.sha256((h/"control_spec.json").read_bytes()).hexdigest()); print("thresholds="+json.dumps(p["thresholds"],sort_keys=True))
