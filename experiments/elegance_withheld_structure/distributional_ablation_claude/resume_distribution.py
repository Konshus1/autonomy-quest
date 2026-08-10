#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, importlib.util, json, os, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; ROOT=HERE.parent; FI=ROOT/"full_run"/"frozen_inputs"
EXPECTED_PROMPT_MANIFEST_SHA="436c651eaf097db781ac977a0927210c77a47189484c0426914f76ed29ec3ed1"

def env_subscription():
 e=os.environ.copy();e.pop("ANTHROPIC_API_KEY",None);e.pop("ANTHROPIC_API_KEY_AUTOMATION",None);return e

def validate(obj:Any)->None:
 if not isinstance(obj,dict) or set(obj)!={"plan","mechanisms","code","trace"}:raise ValueError("top level mismatch")
 if not isinstance(obj["plan"],list) or not all(isinstance(x,str) for x in obj["plan"]):raise ValueError("plan mismatch")
 if not isinstance(obj["mechanisms"],list) or not all(isinstance(x,str) for x in obj["mechanisms"]):raise ValueError("mechanisms mismatch")
 if not isinstance(obj["code"],str) or not isinstance(obj["trace"],list):raise ValueError("code/trace mismatch")
 for x in obj["trace"]:
  if not isinstance(x,dict) or set(x)!={"source_relation","target_mechanism"} or not all(isinstance(x[k],str) for k in x):raise ValueError("trace row mismatch")

def extract(wrapper):
 if not isinstance(wrapper,dict) or wrapper.get("is_error") is not False:raise ValueError("Claude wrapper error")
 obj=wrapper.get("structured_output")
 if obj is None and isinstance(wrapper.get("result"),str):obj=json.loads(wrapper["result"])
 validate(obj);return obj

def resume_one(old:dict[str,Any],schema_text:str)->dict[str,Any]:
 case,condition,sample=old["task"],old["condition"],old["sample"]
 d=OUT/case/condition/f"sample{sample:02d}"; prompt=(d/"prompt.txt").read_text();obj=None;wrapper=None
 for attempt in (3,4):
  with tempfile.TemporaryDirectory(prefix=f"elegance-dist-claude-resume-{case}-{condition}-{sample}-") as td:
   cmd=["claude","-p",prompt,"--output-format","json","--json-schema",schema_text,"--no-session-persistence","--safe-mode","--tools",""]
   try:
    cp=subprocess.run(cmd,cwd=td,text=True,capture_output=True,timeout=900,env=env_subscription());stdout,stderr,rc=cp.stdout,cp.stderr,cp.returncode
   except subprocess.TimeoutExpired as exc:stdout,stderr,rc=exc.stdout or "",exc.stderr or str(exc),124
   (d/f"claude_stdout_attempt{attempt}.txt").write_text(stdout);(d/f"claude_stderr_attempt{attempt}.txt").write_text(stderr)
   (d/f"transport_attempt{attempt}.json").write_text(json.dumps({"returncode":rc,"resume_after_subscription_reset":True,"command":["claude","-p","<FROZEN_PROMPT>","--output-format","json","--json-schema","<FROZEN_SCHEMA>","--no-session-persistence","--safe-mode","--tools",""]},indent=2)+"\n")
   if rc==0:
    try:
     wrapper=json.loads(stdout);obj=extract(wrapper);(d/"response.json").write_text(json.dumps(obj,indent=2)+"\n")
     (d/"claude_receipt.json").write_text(json.dumps({k:wrapper.get(k) for k in ("session_id","modelUsage","usage","subtype","is_error","total_cost_usd")},indent=2)+"\n");break
    except Exception as exc:(d/f"parse_error_attempt{attempt}.txt").write_text(repr(exc)+"\n")
  time.sleep(5)
 if obj is None:return {"task":case,"condition":condition,"sample":sample,"generation_rc":1,"test_rc":None,"resume_exhausted":True}
 code=obj["code"];normalization=[]
 if code.endswith("\\n"):code=code[:-2];normalization.append("stripped_terminal_literal_backslash_n")
 (d/"normalization.json").write_text(json.dumps(normalization)+"\n");(d/"plan.json").write_text(json.dumps({k:obj[k] for k in ("plan","mechanisms","trace")},indent=2)+"\n");(d/"solution.py").write_text(code.rstrip()+"\n")
 test=FI.parent/"tests"/f"test_{case.lower()}.py";cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True)
 (d/"test_stdout.txt").write_text(cp.stdout);(d/"test_stderr.txt").write_text(cp.stderr)
 row={"task":case,"condition":condition,"sample":sample,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr,"resumed_after_subscription_reset":True}
 (d/"cell.json").write_text(json.dumps(row,indent=2)+"\n");return row

def main()->int:
 if hashlib.sha256((HERE/"prompt_manifest.json").read_bytes()).hexdigest()!=EXPECTED_PROMPT_MANIFEST_SHA:raise SystemExit("frozen prompt manifest changed")
 manifest=json.loads((OUT/"manifest.json").read_text());old_records=manifest["records"];unfilled=[r for r in old_records if r.get("generation_rc")!=0]
 if len(unfilled)!=67:raise SystemExit(f"expected exactly 67 authorized unfilled cells, got {len(unfilled)}")
 for r in unfilled:
  d=OUT/r["task"]/r["condition"]/f"sample{r['sample']:02d}";(d/"cell_pre_resume.json").write_text((d/"cell.json").read_text())
  sha=(d/"prompt_sha256.txt").read_text().strip()
  if sha!=hashlib.sha256((d/"prompt.txt").read_bytes()).hexdigest():raise SystemExit(f"prompt SHA mismatch {d}")
 auth=subprocess.run(["claude","auth","status"],text=True,capture_output=True,env=env_subscription());auth_obj=json.loads(auth.stdout)
 if auth.returncode or auth_obj.get("authMethod")!="claude.ai" or not auth_obj.get("subscriptionType") or auth_obj.get("apiKeySource"):raise SystemExit("not subscription auth")
 (OUT/"resume_subscription_auth_receipt.json").write_text(json.dumps(auth_obj,indent=2)+"\n")
 schema=json.loads((OUT/"output_schema.json").read_text());schema_text=json.dumps(schema,separators=(",",":"));new=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
  fs={ex.submit(resume_one,r,schema_text):(r["task"],r["condition"],r["sample"]) for r in unfilled}
  for i,f in enumerate(concurrent.futures.as_completed(fs),1):
   try:row=f.result()
   except Exception as exc:
    c,k,s=fs[f];row={"task":c,"condition":k,"sample":s,"generation_rc":1,"test_rc":None,"runner_error":repr(exc),"resume_exhausted":True}
   new.append(row);print(f"{i}/{len(unfilled)} "+json.dumps(row),flush=True)
 by={(r["task"],r["condition"],r["sample"]):r for r in old_records};by.update({(r["task"],r["condition"],r["sample"]):r for r in new})
 plan=json.loads((HERE/"plan.json").read_text());records=[by[(c,k,s)] for c in plan["cases"] for k in plan["conditions"] for s in range(1,16)]
 models={};sessions=[]
 for c in plan["cases"]:
  for k in plan["conditions"]:
   for s in range(1,16):
    p=OUT/c/k/f"sample{s:02d}"/"claude_receipt.json"
    if p.is_file():
     rr=json.loads(p.read_text());sessions.append(rr.get("session_id"))
     for model in (rr.get("modelUsage") or {}):models[model]=models.get(model,0)+1
 manifest["records"]=records;manifest["resolved_model_invocation_counts"]=models;manifest["distinct_session_ids"]=len(set(sessions));manifest["resume_after_reset"]={"authorized":True,"unfilled_before":67,"subscription_auth":auth_obj,"new_successes":sum(r["generation_rc"]==0 for r in new),"still_unfilled":sum(r["generation_rc"]!=0 for r in new),"attempt_numbers":[3,4],"prompt_manifest_sha256":EXPECTED_PROMPT_MANIFEST_SHA}
 (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
 print(f"resume complete total_filled={sum(r['generation_rc']==0 for r in records)} total_correct={sum(r.get('test_rc')==0 for r in records)} models={models}")
 return 0 if all(r["generation_rc"]==0 for r in records) else 1
if __name__=="__main__":raise SystemExit(main())
