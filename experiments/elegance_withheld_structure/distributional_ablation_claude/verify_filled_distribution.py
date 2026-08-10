#!/usr/bin/env python3
"""Audit every filled Claude generation while preserving preregistered unfilled cells."""
from __future__ import annotations
import argparse,hashlib,json,re,subprocess,sys
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent
SESSION_RE=re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

def load(p:Path)->Any:return json.loads(p.read_text())
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def validate(obj:Any)->bool:
    if not isinstance(obj,dict) or set(obj)!={"plan","mechanisms","code","trace"}:return False
    if not isinstance(obj["plan"],list) or not all(isinstance(x,str) for x in obj["plan"]):return False
    if not isinstance(obj["mechanisms"],list) or not all(isinstance(x,str) for x in obj["mechanisms"]):return False
    if not isinstance(obj["code"],str) or not isinstance(obj["trace"],list):return False
    return all(isinstance(x,dict) and set(x)=={"source_relation","target_mechanism"} and all(isinstance(x[k],str) for k in x) for x in obj["trace"])

def audit(study:Path)->list[str]:
    fail=[]
    def check(ok:bool,msg:str):
        if not ok:fail.append(msg)
    plan=load(study/"plan.json"); manifest=load(study/"results/manifest.json"); pm=load(study/"prompt_manifest.json")
    check(sha(study/"plan.json")== (study/"plan_sha256.txt").read_text().strip(),"plan freeze SHA mismatch")
    auth=load(study/"results/subscription_auth_receipt.json")
    check(auth.get("authMethod")=="claude.ai" and bool(auth.get("subscriptionType")) and not auth.get("apiKeySource"),"not subscription-only auth")
    expected=[(c,k,s) for c in plan["cases"] for k in plan["conditions"] for s in range(1,16)]
    rows=manifest.get("records",[]); keys=[(r.get("task"),r.get("condition"),r.get("sample")) for r in rows]
    check(len(rows)==135 and len(set(keys))==135 and set(keys)==set(expected),"manifest does not preserve exact 135-cell coverage")
    by={k:r for k,r in zip(keys,rows)}; pby={(r["case"],r["condition"]):r for r in pm["prompts"]}; sessions=set(); filled=0; unfilled=0
    for case,condition,sample in expected:
        row=by[(case,condition,sample)]; d=study/"results"/case/condition/f"sample{sample:02d}"
        prompt=d/"prompt.txt"; frozen=study/pby[(case,condition)]["file"]
        check(prompt.is_file() and prompt.read_bytes()==frozen.read_bytes(),f"{case}/{condition}/{sample}: prompt differs from byte-frozen source")
        check((d/"prompt_sha256.txt").is_file() and (d/"prompt_sha256.txt").read_text().strip()==sha(frozen),f"{case}/{condition}/{sample}: prompt receipt mismatch")
        check((d/"cell.json").is_file() and load(d/"cell.json")==row,f"{case}/{condition}/{sample}: cell/manifest mismatch")
        if row.get("generation_rc")==0:
            filled+=1; needed=["response.json","plan.json","solution.py","normalization.json","test_stdout.txt","test_stderr.txt","claude_receipt.json"]
            if any(not (d/x).is_file() for x in needed): check(False,f"{case}/{condition}/{sample}: filled artifact missing files"); continue
            obj=load(d/"response.json"); check(validate(obj),f"{case}/{condition}/{sample}: response violates frozen schema")
            code=obj["code"]; norm=[]
            if code.endswith("\\n"):code=code[:-2];norm.append("stripped_terminal_literal_backslash_n")
            check((d/"solution.py").read_text()==code.rstrip()+"\n",f"{case}/{condition}/{sample}: normalized solution mismatch")
            check(load(d/"normalization.json")==norm,f"{case}/{condition}/{sample}: normalization receipt mismatch")
            check(load(d/"plan.json")=={k:obj[k] for k in ("plan","mechanisms","trace")},f"{case}/{condition}/{sample}: plan artifact mismatch")
            rr=load(d/"claude_receipt.json"); sid=rr.get("session_id"); check(isinstance(sid,str) and SESSION_RE.fullmatch(sid) is not None,f"{case}/{condition}/{sample}: invalid session id")
            if isinstance(sid,str):sessions.add(sid)
            check(bool(rr.get("modelUsage")) and all(str(v.get("canonicalModel",k)).startswith("claude-") and v.get("provider")=="firstParty" for k,v in rr.get("modelUsage",{}).items()),f"{case}/{condition}/{sample}: invalid Claude model receipt")
            test=study.parent/"full_run/tests"/f"test_{case.lower()}.py"; cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True)
            check(cp.returncode==row.get("test_rc"),f"{case}/{condition}/{sample}: recomputed test rc mismatch")
        else:
            unfilled+=1; check(row.get("test_rc") is None,f"{case}/{condition}/{sample}: unfilled cell incorrectly scored")
    check(len(sessions)==filled,f"Claude successful sessions not distinct ({len(sessions)}/{filled})")
    return fail

def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--study",type=Path,default=HERE);a=ap.parse_args();study=a.study.resolve();f=audit(study)
    if f:
        print("FAIL: filled Claude generation audit");[print("- "+x) for x in f];return 1
    m=load(study/"results/manifest.json");filled=sum(r["generation_rc"]==0 for r in m["records"]);correct=sum(r.get("test_rc")==0 for r in m["records"])
    print("PASS: filled Claude generation audit");print(f"planned=135 filled={filled} unfilled={135-filled} correct_among_filled={correct}");return 0
if __name__=="__main__":raise SystemExit(main())
