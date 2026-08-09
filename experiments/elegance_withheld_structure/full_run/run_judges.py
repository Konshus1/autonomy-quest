#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures,hashlib,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; J=OUT/"judgments"; ARMS=("direct","semantic","structural","human")
SCHEMA={"type":"object","properties":{"ranking":{"type":"array","items":{"type":"string"}},"rationale":{"type":"string"}},"required":["ranking","rationale"],"additionalProperties":False}
def labels(cid):
 ordered=sorted(ARMS,key=lambda a:hashlib.sha256(f"{cid}:{a}".encode()).hexdigest()); return {arm:chr(65+i) for i,arm in enumerate(ordered)}
def judge(task,correct,schema):
 cid=task["id"]; lm=labels(cid); choices=[]
 for arm in correct: choices.append({"label":lm[arm],"code":(OUT/cid/arm/"solution.py").read_text()})
 choices.sort(key=lambda x:x["label"])
 prompt="All implementations below passed the same frozen tests for this public contract. Rank every label from the one you would most rather maintain to least. Judge clarity, local reasoning, change cost, and unnecessary concepts; do not guess how an implementation was prompted.\n\nCONTRACT:\n"+task["contract"]+"\n\nBLINDED IMPLEMENTATIONS:\n"+"\n\n".join(f"LABEL {x['label']}:\n```python\n{x['code']}\n```" for x in choices)
 d=J/cid; d.mkdir(parents=True,exist_ok=True); (d/"prompt.txt").write_text(prompt); (d/"label_map.json").write_text(json.dumps(lm,indent=2)+"\n")
 with tempfile.TemporaryDirectory(prefix=f"elegance-judge-{cid}-") as td:
  last=Path(td)/"last.json"; cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema),"-o",str(last),prompt]; cp=subprocess.run(cmd,text=True,capture_output=True,timeout=900)
  (d/"codex_stdout.txt").write_text(cp.stdout); (d/"codex_stderr.txt").write_text(cp.stderr)
  if cp.returncode or not last.is_file(): return {"task":cid,"rc":cp.returncode or 1}
  raw=last.read_text(); (d/"response.json").write_text(raw); obj=json.loads(raw); expected={x["label"] for x in choices}
  obj["ranking"]=[x.removeprefix("LABEL ").strip() for x in obj["ranking"]]
  if set(obj["ranking"])!=expected or len(obj["ranking"])!=len(expected): return {"task":cid,"rc":2,"error":"invalid ranking"}
  inverse={v:k for k,v in lm.items()}; (d/"decoded.json").write_text(json.dumps({"ranking":[inverse[x] for x in obj["ranking"]],"rationale":obj["rationale"]},indent=2)+"\n"); return {"task":cid,"rc":0,"labels":[x["label"] for x in choices]}
def main():
 if J.exists(): raise SystemExit("judgments exists; refusing overwrite")
 tasks=json.loads((HERE/"frozen_inputs"/"task_contracts.json").read_text()); manifest=json.loads((OUT/"manifest.json").read_text()); correct={t["id"]:[] for t in tasks}
 for r in manifest["records"]:
  if r["test_rc"]==0: correct[r["task"]].append(r["arm"])
 J.mkdir(); schema=J/"schema.json"; schema.write_text(json.dumps(SCHEMA)+"\n"); eligible=[t for t in tasks if len(correct[t["id"]])>=2]; unjudgeable=[{"task":t["id"],"correct_arms":correct[t["id"]]} for t in tasks if len(correct[t["id"]])<2]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex: records=list(ex.map(lambda t:judge(t,correct[t["id"]],schema),eligible))
 (J/"manifest.json").write_text(json.dumps({"records":records,"unjudgeable":unjudgeable},indent=2)+"\n"); print(json.dumps({"records":records,"unjudgeable":unjudgeable},indent=2)); return 0 if all(x["rc"]==0 for x in records) else 1
if __name__=="__main__": raise SystemExit(main())
