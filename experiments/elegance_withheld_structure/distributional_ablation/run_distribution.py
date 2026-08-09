#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, subprocess, sys, tempfile, time
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; ROOT=HERE.parent; FI=ROOT/"full_run"/"frozen_inputs"
SCHEMA={"type":"object","properties":{"plan":{"type":"array","items":{"type":"string"}},"mechanisms":{"type":"array","items":{"type":"string"}},"code":{"type":"string"},"trace":{"type":"array","items":{"type":"object","properties":{"source_relation":{"type":"string"},"target_mechanism":{"type":"string"}},"required":["source_relation","target_mechanism"],"additionalProperties":False}}},"required":["plan","mechanisms","code","trace"],"additionalProperties":False}
SUFFIX="\n\nReturn a concise implementation plan naming concrete organizing mechanisms, complete solution.py code, and source-relation to target-mechanism trace entries only if the supplied source directly caused the mechanism; otherwise use an empty trace. Correctness against the public contract is mandatory. Do not discuss the experiment or condition."
def make_prompt(task,condition,contexts):
 p=task["contract"]
 if condition!="default": p += "\n\nSOURCE ANALOGUE (a correct worked source-domain solution; transfer only relations that genuinely fit):\n"+json.dumps(contexts[task["id"]][condition],indent=2)
 return p+SUFFIX
def run_one(task,condition,sample,prompt,schema):
 d=OUT/task["id"]/condition/f"sample{sample:02d}"; d.mkdir(parents=True,exist_ok=True); (d/"prompt.txt").write_text(prompt); (d/"prompt_sha256.txt").write_text(hashlib.sha256(prompt.encode()).hexdigest()+"\n")
 for attempt in range(1,3):
  with tempfile.TemporaryDirectory(prefix=f"elegance-dist-{task['id']}-{condition}-{sample}-") as td:
   last=Path(td)/"last.json"; cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema),"-o",str(last),prompt]
   try: cp=subprocess.run(cmd,text=True,capture_output=True,timeout=900)
   except subprocess.TimeoutExpired as exc:
    (d/f"codex_stderr_attempt{attempt}.txt").write_text(str(exc)); time.sleep(5); continue
   (d/f"codex_stdout_attempt{attempt}.txt").write_text(cp.stdout); (d/f"codex_stderr_attempt{attempt}.txt").write_text(cp.stderr)
   if cp.returncode==0 and last.is_file():
    try: raw=last.read_text(); obj=json.loads(raw); (d/"response.json").write_text(raw); break
    except Exception as exc: (d/f"parse_error_attempt{attempt}.txt").write_text(str(exc))
  time.sleep(5)
 else: return {"task":task["id"],"condition":condition,"sample":sample,"generation_rc":1,"test_rc":None}
 code=obj["code"]; normalization=[]
 if code.endswith("\\n"): code=code[:-2]; normalization.append("stripped_terminal_literal_backslash_n")
 (d/"normalization.json").write_text(json.dumps(normalization)+"\n"); (d/"plan.json").write_text(json.dumps({k:obj[k] for k in ("plan","mechanisms","trace")},indent=2)+"\n"); (d/"solution.py").write_text(code.rstrip()+"\n")
 test=FI.parent/"tests"/f"test_{task['id'].lower()}.py"; cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True); (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
 row={"task":task["id"],"condition":condition,"sample":sample,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr}; (d/"cell.json").write_text(json.dumps(row,indent=2)+"\n"); return row
def main():
 if OUT.exists(): raise SystemExit("results exists; refusing overwrite")
 plan=json.loads((HERE/"plan.json").read_text()); contexts=json.loads((HERE/plan["contexts_file"]).resolve().read_text()); tasks={x["id"]:x for x in json.loads((FI/"task_contracts.json").read_text())}; OUT.mkdir(); schema=OUT/"output_schema.json"; schema.write_text(json.dumps(SCHEMA)+"\n")
 jobs=[]
 for cid in plan["cases"]:
  for condition in plan["conditions"]:
   p=make_prompt(tasks[cid],condition,contexts)
   for sample in range(1,plan["n_per_cell"]+1): jobs.append((tasks[cid],condition,sample,p,schema))
 records=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
  future={ex.submit(run_one,*job):(job[0]["id"],job[1],job[2]) for job in jobs}
  for i,f in enumerate(concurrent.futures.as_completed(future),1):
   row=f.result(); records.append(row); print(f"{i}/135 "+json.dumps(row),flush=True)
 order={(cid,c,s):i for i,(cid,c,s) in enumerate(( (cid,c,s) for cid in plan["cases"] for c in plan["conditions"] for s in range(1,16) ))}; records.sort(key=lambda r:order[(r["task"],r["condition"],r["sample"])])
 (OUT/"manifest.json").write_text(json.dumps({"engine":plan["engine"],"records":records},indent=2)+"\n"); print(f"complete cells={len(records)} generated={sum(x['generation_rc']==0 for x in records)} correct={sum(x['test_rc']==0 for x in records)}")
 return 0 if len(records)==135 and all(x["generation_rc"]==0 for x in records) else 1
if __name__=="__main__": raise SystemExit(main())
