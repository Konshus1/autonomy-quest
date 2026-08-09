#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, re, subprocess, sys, tempfile, time
from pathlib import Path
HERE=Path(__file__).resolve().parent; INPUTS=HERE/"frozen_inputs"; OUT=HERE/"results"
ARMS=("direct","semantic","structural","human")
SCHEMA={"type":"object","properties":{"plan":{"type":"array","items":{"type":"string"}},"code":{"type":"string"},"trace":{"type":"array","items":{"type":"object","properties":{"source_relation":{"type":"string"},"target_mechanism":{"type":"string"}},"required":["source_relation","target_mechanism"],"additionalProperties":False}}},"required":["plan","code","trace"],"additionalProperties":False}
def contexts():
 return (json.loads((INPUTS/"semantic_contexts.json").read_text()),json.loads((INPUTS/"structural_contexts.json").read_text()),json.loads((INPUTS/"human_maps.json").read_text()))
def prompt_for(task,arm,ctxs,corpus):
 sem,st,hm=ctxs; cid=task["id"]; p=task["contract"]
 if arm=="semantic": p += "\n\nNEAREST-NEIGHBOR CODE RETRIEVAL (use only if helpful):\n"+json.dumps(sem[cid],indent=2)
 elif arm=="structural": p += "\n\nMACHINE-RETRIEVED CROSS-DOMAIN ANALOGUE WITH EXPLICIT CORRESPONDENCES:\n"+json.dumps(st[cid],indent=2)+"\nTransfer useful relations, not surface vocabulary."
 elif arm=="human":
  item=next(x for x in corpus["candidates"] if x.get("id")==cid)
  p += "\n\nHUMAN-SUPPLIED CROSS-DOMAIN ANALOGUE WITH EXPLICIT CORRESPONDENCES:\n"+json.dumps({"analogue":item["human_analogy"],**hm[cid]},indent=2)+"\nTransfer useful relations, not surface vocabulary."
 p += "\n\nReturn: (1) a concise implementation plan naming its mechanisms, (2) complete solution.py code, and (3) source-relation to target-mechanism trace entries only when a source context above directly caused that mechanism; otherwise an empty trace. Do not discuss the experiment or arm. Correctness is mandatory. Do not emit literal backslash-n characters after the final Python statement."
 return p
def run_cell(task,arm,prompt,schema):
 d=OUT/task["id"]/arm; d.mkdir(parents=True,exist_ok=True); (d/"prompt.txt").write_text(prompt); (d/"prompt_sha256.txt").write_text(hashlib.sha256(prompt.encode()).hexdigest()+"\n")
 last_error=None
 for attempt in range(1,3):
  with tempfile.TemporaryDirectory(prefix=f"elegance-m5-{task['id']}-{arm}-") as td:
   last=Path(td)/"last.json"; cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema),"-o",str(last),prompt]
   cp=subprocess.run(cmd,text=True,capture_output=True,timeout=900); (d/f"codex_stdout_attempt{attempt}.txt").write_text(cp.stdout); (d/f"codex_stderr_attempt{attempt}.txt").write_text(cp.stderr)
   if cp.returncode==0 and last.is_file():
    try: raw=last.read_text(); obj=json.loads(raw); (d/"response.json").write_text(raw); break
    except Exception as exc: last_error=f"parse: {exc}"
   else: last_error=f"codex rc={cp.returncode}"
  time.sleep(5)
 else: return {"task":task["id"],"arm":arm,"generation_rc":1,"test_rc":None,"error":last_error}
 code=obj["code"]; normalization=[]
 if code.endswith("\\n"): code=code[:-2]; normalization.append("stripped_terminal_literal_backslash_n")
 (d/"normalization.json").write_text(json.dumps(normalization)+"\n"); (d/"plan.json").write_text(json.dumps({"plan":obj["plan"],"trace":obj["trace"]},indent=2)+"\n"); (d/"solution.py").write_text(code.rstrip()+"\n")
 test=HERE/"tests"/f"test_{task['id'].lower()}.py"; cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True)
 (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
 return {"task":task["id"],"arm":arm,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr}
def main():
 if OUT.exists(): raise SystemExit("results exists; refusing overwrite")
 tasks=json.loads((INPUTS/"task_contracts.json").read_text()); corpus=json.loads((HERE.parent/"corpus_candidates.json").read_text()); ctxs=contexts(); OUT.mkdir(); schema=OUT/"output_schema.json"; schema.write_text(json.dumps(SCHEMA)+"\n")
 records=[]
 for task in tasks:
  with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
   futs={arm:ex.submit(run_cell,task,arm,prompt_for(task,arm,ctxs,corpus),schema) for arm in ARMS}
   records += [futs[arm].result() for arm in ARMS]
  print(json.dumps(records[-4:],indent=2),flush=True)
 manifest={"engine":"codex subscription","records":records}; (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
 print(f"complete cells={len(records)} correct={sum(r.get('test_rc')==0 for r in records)}")
 return 0 if len(records)==60 and all(r["generation_rc"]==0 for r in records) else 1
if __name__=="__main__": raise SystemExit(main())
