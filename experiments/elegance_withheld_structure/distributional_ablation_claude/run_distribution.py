#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
OUT=HERE/"results"
ROOT=HERE.parent
FI=ROOT/"full_run"/"frozen_inputs"
SCHEMA={"type":"object","properties":{"plan":{"type":"array","items":{"type":"string"}},"mechanisms":{"type":"array","items":{"type":"string"}},"code":{"type":"string"},"trace":{"type":"array","items":{"type":"object","properties":{"source_relation":{"type":"string"},"target_mechanism":{"type":"string"}},"required":["source_relation","target_mechanism"],"additionalProperties":False}}},"required":["plan","mechanisms","code","trace"],"additionalProperties":False}


def subscription_env() -> dict[str,str]:
    env=os.environ.copy()
    env.pop("ANTHROPIC_API_KEY",None)
    env.pop("ANTHROPIC_API_KEY_AUTOMATION",None)
    return env


def validate(obj: Any) -> None:
    if not isinstance(obj,dict) or set(obj)!={"plan","mechanisms","code","trace"}:
        raise ValueError("top-level response does not exactly match frozen schema")
    if not isinstance(obj["plan"],list) or not all(isinstance(x,str) for x in obj["plan"]):
        raise ValueError("plan is not an array of strings")
    if not isinstance(obj["mechanisms"],list) or not all(isinstance(x,str) for x in obj["mechanisms"]):
        raise ValueError("mechanisms is not an array of strings")
    if not isinstance(obj["code"],str):
        raise ValueError("code is not a string")
    if not isinstance(obj["trace"],list):
        raise ValueError("trace is not an array")
    for row in obj["trace"]:
        if not isinstance(row,dict) or set(row)!={"source_relation","target_mechanism"} or not all(isinstance(row[k],str) for k in row):
            raise ValueError("trace row does not exactly match frozen schema")


def prompt_index(plan: dict[str,Any]) -> dict[tuple[str,str],tuple[Path,str]]:
    manifest=json.loads((HERE/"prompt_manifest.json").read_text())
    if not manifest.get("frozen_before_generation"):
        raise SystemExit("prompt manifest not frozen")
    index={}
    for row in manifest["prompts"]:
        p=HERE/row["file"]
        actual=hashlib.sha256(p.read_bytes()).hexdigest()
        if actual!=row["sha256"]:
            raise SystemExit(f"frozen prompt SHA mismatch: {p}")
        index[(row["case"],row["condition"])]=(p,actual)
    expected={(c,k) for c in plan["cases"] for k in plan["conditions"]}
    if set(index)!=expected:
        raise SystemExit("frozen prompt manifest does not cover exactly 9 case/condition cells")
    return index


def extract_response(wrapper: Any) -> dict[str,Any]:
    if not isinstance(wrapper,dict) or wrapper.get("is_error") is not False:
        raise ValueError("Claude CLI wrapper reports error or is malformed")
    obj=wrapper.get("structured_output")
    if obj is None and isinstance(wrapper.get("result"),str):
        obj=json.loads(wrapper["result"])
    validate(obj)
    return obj


def run_one(task: dict[str,Any],condition: str,sample: int,prompt: str,prompt_sha: str,schema_text: str) -> dict[str,Any]:
    d=OUT/task["id"]/condition/f"sample{sample:02d}"
    d.mkdir(parents=True,exist_ok=True)
    (d/"prompt.txt").write_text(prompt)
    (d/"prompt_sha256.txt").write_text(prompt_sha+"\n")
    obj=None; wrapper=None
    for attempt in range(1,3):  # exact original policy: at most two total attempts
        with tempfile.TemporaryDirectory(prefix=f"elegance-dist-claude-{task['id']}-{condition}-{sample}-") as td:
            cmd=["claude","-p",prompt,"--output-format","json","--json-schema",schema_text,
                 "--no-session-persistence","--safe-mode","--tools",""]
            try:
                cp=subprocess.run(cmd,cwd=td,text=True,capture_output=True,timeout=900,env=subscription_env())
                stdout,stderr,rc=cp.stdout,cp.stderr,cp.returncode
            except subprocess.TimeoutExpired as exc:
                stdout,stderr,rc=exc.stdout or "",exc.stderr or str(exc),124
            (d/f"claude_stdout_attempt{attempt}.txt").write_text(stdout)
            (d/f"claude_stderr_attempt{attempt}.txt").write_text(stderr)
            (d/f"transport_attempt{attempt}.json").write_text(json.dumps({"returncode":rc,"command":["claude","-p","<FROZEN_PROMPT>","--output-format","json","--json-schema","<FROZEN_SCHEMA>","--no-session-persistence","--safe-mode","--tools",""]},indent=2)+"\n")
            if rc==0:
                try:
                    wrapper=json.loads(stdout)
                    obj=extract_response(wrapper)
                    (d/"response.json").write_text(json.dumps(obj,indent=2)+"\n")
                    (d/"claude_receipt.json").write_text(json.dumps({k:wrapper.get(k) for k in ("session_id","modelUsage","usage","subtype","is_error","total_cost_usd")},indent=2)+"\n")
                    break
                except Exception as exc:
                    (d/f"parse_error_attempt{attempt}.txt").write_text(repr(exc)+"\n")
        time.sleep(5)
    if obj is None:
        row={"task":task["id"],"condition":condition,"sample":sample,"generation_rc":1,"test_rc":None}
        (d/"cell.json").write_text(json.dumps(row,indent=2)+"\n")
        return row
    code=obj["code"]; normalization=[]
    if code.endswith("\\n"):
        code=code[:-2]; normalization.append("stripped_terminal_literal_backslash_n")
    (d/"normalization.json").write_text(json.dumps(normalization)+"\n")
    (d/"plan.json").write_text(json.dumps({k:obj[k] for k in ("plan","mechanisms","trace")},indent=2)+"\n")
    (d/"solution.py").write_text(code.rstrip()+"\n")
    test=FI.parent/"tests"/f"test_{task['id'].lower()}.py"
    cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True)
    (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
    row={"task":task["id"],"condition":condition,"sample":sample,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr}
    (d/"cell.json").write_text(json.dumps(row,indent=2)+"\n")
    return row


def main() -> int:
    if OUT.exists(): raise SystemExit("results exists; refusing overwrite")
    plan=json.loads((HERE/"plan.json").read_text())
    recorded=(HERE/"plan_sha256.txt").read_text().strip()
    actual=hashlib.sha256((HERE/"plan.json").read_bytes()).hexdigest()
    if actual!=recorded: raise SystemExit("plan changed after freeze")
    auth=subprocess.run(["claude","auth","status"],text=True,capture_output=True,env=subscription_env())
    if auth.returncode!=0: raise SystemExit("Claude subscription auth preflight failed: "+auth.stderr)
    auth_obj=json.loads(auth.stdout)
    if auth_obj.get("authMethod")!="claude.ai" or not auth_obj.get("subscriptionType") or auth_obj.get("apiKeySource"):
        raise SystemExit("refusing non-subscription Claude authentication: "+auth.stdout)
    prompts=prompt_index(plan)
    tasks={x["id"]:x for x in json.loads((FI/"task_contracts.json").read_text())}
    OUT.mkdir()
    (OUT/"subscription_auth_receipt.json").write_text(json.dumps(auth_obj,indent=2)+"\n")
    (OUT/"output_schema.json").write_text(json.dumps(SCHEMA)+"\n")
    schema_text=json.dumps(SCHEMA,separators=(",",":"))
    jobs=[]
    for cid in plan["cases"]:
        for condition in plan["conditions"]:
            p,sha=prompts[(cid,condition)]; prompt=p.read_text()
            for sample in range(1,plan["n_per_cell"]+1):
                jobs.append((tasks[cid],condition,sample,prompt,sha,schema_text))
    records=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        future={ex.submit(run_one,*job):(job[0]["id"],job[1],job[2]) for job in jobs}
        for i,f in enumerate(concurrent.futures.as_completed(future),1):
            try: row=f.result()
            except Exception as exc:
                case,condition,sample=future[f]
                row={"task":case,"condition":condition,"sample":sample,"generation_rc":1,"test_rc":None,"runner_error":repr(exc)}
            records.append(row); print(f"{i}/{len(jobs)} "+json.dumps(row),flush=True)
    order={(cid,c,s):i for i,(cid,c,s) in enumerate(( (cid,c,s) for cid in plan["cases"] for c in plan["conditions"] for s in range(1,plan["n_per_cell"]+1) ))}
    records.sort(key=lambda r:order[(r["task"],r["condition"],r["sample"])])
    models={}; sessions=[]
    for case in plan["cases"]:
        for condition in plan["conditions"]:
            for sample in range(1,plan["n_per_cell"]+1):
                receipt=OUT/case/condition/f"sample{sample:02d}"/"claude_receipt.json"
                if receipt.is_file():
                    rr=json.loads(receipt.read_text()); sessions.append(rr.get("session_id"))
                    for model,usage in (rr.get("modelUsage") or {}).items(): models[model]=models.get(model,0)+1
    manifest={"engine":plan["engine"],"plan_sha256":actual,"prompt_manifest_sha256":hashlib.sha256((HERE/"prompt_manifest.json").read_bytes()).hexdigest(),"subscription_auth":auth_obj,"resolved_model_invocation_counts":models,"distinct_session_ids":len(set(sessions)),"records":records}
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(f"complete cells={len(records)} generated={sum(x['generation_rc']==0 for x in records)} correct={sum(x['test_rc']==0 for x in records)} models={models}")
    return 0 if len(records)==plan["total_generations"] and all(x["generation_rc"]==0 for x in records) else 1

if __name__=="__main__": raise SystemExit(main())
