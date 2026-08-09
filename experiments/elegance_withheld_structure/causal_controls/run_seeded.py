#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures,hashlib,json,os,subprocess,sys,time,urllib.error,urllib.request
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; FI=HERE.parent/"full_run"/"frozen_inputs"
URL=os.environ.get("DEEPSEEK_BASE_URL","https://api.deepseek.com").rstrip("/")+"/chat/completions"; KEY=os.environ.get("DEEPSEEK_API_KEY")
SYSTEM="You are the planning and implementation component in a controlled software-design system. Return one JSON object only with keys plan (array of concise steps), mechanisms (array of concrete organizing mechanisms), code (complete solution.py string), and trace (array of objects with source_relation and target_mechanism; empty when no source directly caused a mechanism). Correctness against the supplied public contract is mandatory. Do not mention experimental conditions."
def request(payload):
 body=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode(); last=None
 for attempt in range(4):
  req=urllib.request.Request(URL,data=body,method="POST",headers={"Authorization":"Bearer "+KEY,"Content-Type":"application/json"})
  try:
   with urllib.request.urlopen(req,timeout=300) as r: env=json.loads(r.read())
   return env,hashlib.sha256(body).hexdigest()
  except Exception as exc: last=exc; time.sleep(2**attempt)
 raise RuntimeError(last)
def run_cell(task,condition,repeat,context,plan):
 cid=task["id"]; d=OUT/cid/condition/f"repeat{repeat}"; d.mkdir(parents=True,exist_ok=True)
 prompt=task["contract"]
 if context is not None: prompt += "\n\nSOURCE ANALOGUE (a solved source-domain problem; transfer only relations that genuinely fit):\n"+json.dumps(context,indent=2)
 prompt += "\n\nProduce a concise plan and the complete solution now."
 payload={"model":plan["model"],"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}],"temperature":plan["temperature"],"seed":plan["seed"],"max_tokens":8000,"response_format":{"type":"json_object"},"thinking":{"type":"disabled"}}
 (d/"request.json").write_text(json.dumps(payload,indent=2)+"\n")
 try: env,rhash=request(payload)
 except Exception as exc: return {"task":cid,"condition":condition,"repeat":repeat,"request_rc":1,"test_rc":None,"error":str(exc)}
 (d/"response_envelope.json").write_text(json.dumps(env,indent=2)+"\n"); content=env["choices"][0]["message"]["content"]; (d/"raw_content.txt").write_text(content); (d/"request_sha256.txt").write_text(rhash+"\n")
 try: obj=json.loads(content)
 except Exception as exc: return {"task":cid,"condition":condition,"repeat":repeat,"request_rc":2,"test_rc":None,"error":f"json parse {exc}"}
 (d/"plan.json").write_text(json.dumps({"plan":obj.get("plan"),"mechanisms":obj.get("mechanisms"),"trace":obj.get("trace")},indent=2)+"\n"); code=obj.get("code","");
 if code.endswith("\\n"): code=code[:-2]
 (d/"solution.py").write_text(code.rstrip()+"\n")
 test=HERE.parent/"full_run"/"tests"/f"test_{cid.lower()}.py"; cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],capture_output=True,text=True); (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
 return {"task":cid,"condition":condition,"repeat":repeat,"request_rc":0,"test_rc":cp.returncode,"response_id":env.get("id"),"system_fingerprint":env.get("system_fingerprint"),"usage":env.get("usage")}
def main():
 if not KEY: raise SystemExit("DEEPSEEK_API_KEY missing")
 if OUT.exists(): raise SystemExit("results exists; refusing overwrite")
 plan=json.loads((HERE/"control_plan.json").read_text()); contexts=json.loads((HERE/"contexts.json").read_text()); tasks={x["id"]:x for x in json.loads((FI/"task_contracts.json").read_text())}; OUT.mkdir(); jobs=[]
 for cid in plan["cases"]:
  for condition in plan["conditions"]:
   ctx=None if condition=="default" else contexts[cid]["matched" if condition=="matched" else "irrelevant"]
   for repeat in (1,2): jobs.append((tasks[cid],condition,repeat,ctx,plan))
 with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex: records=list(ex.map(lambda a:run_cell(*a),jobs))
 (OUT/"manifest.json").write_text(json.dumps({"records":records},indent=2)+"\n"); print(json.dumps(records,indent=2)); return 0 if all(x["request_rc"]==0 for x in records) else 1
if __name__=="__main__": raise SystemExit(main())
