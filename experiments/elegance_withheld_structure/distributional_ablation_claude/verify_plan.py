#!/usr/bin/env python3
import hashlib,json
from pathlib import Path
h=Path(__file__).resolve().parent
p=h/"plan.json"; d=json.loads(p.read_text())
assert d["frozen_before_generation"] and d["n_per_cell"]==15 and d["total_generations"]==135 and len(d["mechanisms"])==3
assert not (h/"results").exists()
assert hashlib.sha256(p.read_bytes()).hexdigest()==(h/"plan_sha256.txt").read_text().strip()
assert (h/"source_plan_sha256.txt").read_text().strip()=="ae67fcab4bffbffd953fb0a0661adf5fcf5423dfe70999dd6c6f97a38bb8f340"
ctx=(h/d["contexts_file"]).resolve(); assert hashlib.sha256(ctx.read_bytes()).hexdigest()==d["contexts_sha256"]
pm=json.loads((h/"prompt_manifest.json").read_text()); assert pm["frozen_before_generation"] and len(pm["prompts"])==9
for row in pm["prompts"]:
 fp=h/row["file"]; assert hashlib.sha256(fp.read_bytes()).hexdigest()==row["sha256"]
print("PASS: Claude distributional replication preregistered and 9 source prompts byte-frozen")
print("cases=3 conditions=3 n=15 total=135")
print("plan_sha256="+hashlib.sha256(p.read_bytes()).hexdigest())
print("source_plan_sha256="+(h/"source_plan_sha256.txt").read_text().strip())
print("prompt_manifest_sha256="+hashlib.sha256((h/"prompt_manifest.json").read_bytes()).hexdigest())
