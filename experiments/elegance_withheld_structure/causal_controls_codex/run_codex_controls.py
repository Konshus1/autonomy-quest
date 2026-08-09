#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; FI=HERE.parent/"full_run"/"frozen_inputs"
SCHEMA={"type":"object","properties":{"plan":{"type":"array","items":{"type":"string"}},"mechanisms":{"type":"array","items":{"type":"string"}},"code":{"type":"string"},"trace":{"type":"array","items":{"type":"object","properties":{"source_relation":{"type":"string"},"target_mechanism":{"type":"string"}},"required":["source_relation","target_mechanism"],"additionalProperties":False}}},"required":["plan","mechanisms","code","trace"],"additionalProperties":False}
SUFFIX="\n\nReturn a concise implementation plan naming concrete organizing mechanisms, complete solution.py code, and source-relation to target-mechanism trace entries only if the supplied source directly caused the mechanism; otherwise use an empty trace. Correctness against the public contract is mandatory. Do not discuss the experiment or condition."
def prompt(task,condition,contexts):
 p=task["contract"]
 if condition!="default":
  p += "\n\nSOURCE ANALOGUE (a correct worked source-domain solution; transfer only relations that genuinely fit):\n"+json.dumps(contexts[task["id"]][condition],indent=2)
 return p+SUFFIX
def run(task,condition,repeat,p,schema):
 d=OUT/task["id"]/condition/f"repeat{repeat}"; d.mkdir(parents=True,exist_ok=True); (d/"prompt.txt").write_text(p); (d/"prompt_sha256.txt").write_text(hashlib.sha256(p.encode()).hexdigest()+"\n")
 with tempfile.TemporaryDirectory(prefix=f"elegance-causal-{task['id']}-{condition}-{repeat}-") as td:
  last=Path(td)/"last.json"; cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema),"-o",str(last),p]
  cp=subprocess.run(cmd,text=True,capture_output=True,timeout=900); (d/"codex_stdout.txt").write_text(cp.stdout); (d/"codex_stderr.txt").write_text(cp.stderr)
  if cp.returncode or not last.is_file(): return {"task":task["id"],"condition":condition,"repeat":repeat,"generation_rc":cp.returncode or 1,"test_rc":None}
  raw=last.read_text(); (d/"response.json").write_text(raw); obj=json.loads(raw)
 code=obj["code"]; norm=[]
 if code.endswith("\\n"): code=code[:-2]; norm.append("stripped_terminal_literal_backslash_n")
 (d/"normalization.json").write_text(json.dumps(norm)+"\n"); (d/"plan.json").write_text(json.dumps({k:obj[k] for k in ("plan","mechanisms","trace")},indent=2)+"\n"); (d/"solution.py").write_text(code.rstrip()+"\n")
 test=FI.parent/"tests"/f"test_{task['id'].lower()}.py"; cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True); (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
 return {"task":task["id"],"condition":condition,"repeat":repeat,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr}
def main():
 if OUT.exists(): raise SystemExit("results exists; refusing overwrite")
 plan=json.loads((HERE/"control_plan.json").read_text()); contexts=json.loads((HERE/"contexts.json").read_text()); tasks={x["id"]:x for x in json.loads((FI/"task_contracts.json").read_text())}; OUT.mkdir(); schema=OUT/"output_schema.json"; schema.write_text(json.dumps(SCHEMA)+"\n")
 records=[]
 for cid in plan["cases"]:
  for repeat in range(1,plan["repeats_per_condition"]+1):
   with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    fut={c:ex.submit(run,tasks[cid],c,repeat,prompt(tasks[cid],c,contexts),schema) for c in plan["conditions"]}
    records += [fut[c].result() for c in plan["conditions"]]
   print(json.dumps(records[-3:],indent=2),flush=True)
 (OUT/"manifest.json").write_text(json.dumps({"engine":"codex subscription gpt-5.6-sol","seed_control":False,"records":records},indent=2)+"\n"); print(f"complete cells={len(records)} correct={sum(x['test_rc']==0 for x in records)}")
 return 0 if len(records)==27 and all(x["generation_rc"]==0 for x in records) else 1
if __name__=="__main__": raise SystemExit(main())
