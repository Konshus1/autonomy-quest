#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=HERE/"results"

def tokens(s): return set(re.findall(r"[a-z][a-z0-9_]{2,}",s.lower()))
def semantic_context(query):
    q=tokens(query); ranked=[]
    for path in ROOT.rglob("*.py"):
        if "elegance_withheld_structure" in path.parts or ".git" in path.parts: continue
        try: text=path.read_text(errors="ignore")
        except OSError: continue
        if len(text)>40000: text=text[:40000]
        ts=tokens(text); score=len(q&ts)/(len(q|ts) or 1)
        ranked.append((score,str(path.relative_to(ROOT)),text[:3500]))
    score,path,text=max(ranked,key=lambda x:(x[0],x[1]))
    return {"path":path,"score":score,"sha256":hashlib.sha256(text.encode()).hexdigest(),"excerpt":text}
def structural_context():
    d=json.loads((HERE/"structural_sources.json").read_text()); q=set(d["target_relations"])
    scored=[]
    for s in d["sources"]:
        r=set(s["relations"]); scored.append((len(q&r)/(len(q|r) or 1),s["id"],s))
    score,_,src=max(scored,key=lambda x:(x[0],x[1]))
    mapping={src["roles"][k]:v for k,v in d["target_roles"].items() if k in src["roles"]}
    return {"source_id":src["id"],"source_domain":src["domain"],"score":score,"description":src["description"],"relations":src["relations"],"role_correspondences":mapping}
def prompts():
    corpus=json.loads((HERE.parent/"corpus_candidates.json").read_text()); item=next(x for x in corpus["candidates"] if x["id"]=="C05")
    base=item["requirement"]+"\n\n"+(HERE/"contract.txt").read_text()
    sem=semantic_context(base); st=structural_context()
    human={"source_domain":"parcel handling","description":item["human_analogy"],"role_correspondences":{"parcel":"rendered message","wrapping station":"capability","added wrapping/label/sleeve":"text or metadata augmentation","parcel handoff shape":"renderer result contract"},"relations":["parcel passes through selected stations in order","each station independently adds a layer or trace","all stations accept and emit a parcel","repeating the same completed station is skipped"]}
    suffix='''\nReturn a complete solution.py implementation. Do not discuss the experiment. Simplicity is welcome but correctness against the public contract is mandatory.'''
    return {
      "direct":base+suffix,
      "semantic":base+"\n\nSEMANTICALLY RETRIEVED CODE CONTEXT (use only if helpful):\n"+json.dumps(sem,indent=2)+suffix,
      "structural":base+"\n\nMACHINE-RETRIEVED CROSS-DOMAIN ANALOGUE AND EXPLICIT MAP:\n"+json.dumps(st,indent=2)+"\nTransfer the useful organizing relations, not surface vocabulary."+suffix,
      "human":base+"\n\nHUMAN-SUPPLIED CROSS-DOMAIN ANALOGUE AND EXPLICIT MAP:\n"+json.dumps(human,indent=2)+"\nTransfer the useful organizing relations, not surface vocabulary."+suffix,
    }, {"semantic":sem,"structural":st,"human":human}
def run_arm(name,prompt,schema):
    arm=OUT/name; arm.mkdir(parents=True,exist_ok=True); (arm/"prompt.txt").write_text(prompt)
    with tempfile.TemporaryDirectory(prefix=f"elegance-{name}-") as td:
        last=Path(td)/"last.json"
        cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema),"-o",str(last),prompt]
        cp=subprocess.run(cmd,text=True,capture_output=True,timeout=600)
        (arm/"codex_stdout.txt").write_text(cp.stdout); (arm/"codex_stderr.txt").write_text(cp.stderr)
        if cp.returncode: return {"arm":name,"generation_rc":cp.returncode,"test_rc":None}
        raw=last.read_text(); (arm/"response.json").write_text(raw)
        code=json.loads(raw)["code"]
        normalization=[]
        # Strip only Codex's occasional terminal transport artifact (two literal characters).
        if code.endswith("\\n"):
            code=code[:-2]
            normalization.append("stripped_terminal_literal_backslash_n")
        (arm/"normalization.json").write_text(json.dumps(normalization)+"\n")
        (arm/"solution.py").write_text(code.rstrip()+"\n")
    test=subprocess.run([sys.executable,str(HERE/"test_solution.py"),str(arm/"solution.py")],text=True,capture_output=True)
    (arm/"test_stdout.txt").write_text(test.stdout); (arm/"test_stderr.txt").write_text(test.stderr)
    return {"arm":name,"generation_rc":0,"test_rc":test.returncode,"test_output":test.stdout+test.stderr}
def main():
    OUT.mkdir(exist_ok=True)
    ps,contexts=prompts(); (OUT/"contexts.json").write_text(json.dumps(contexts,indent=2)+"\n")
    schema=OUT/"code_schema.json"; schema.write_text(json.dumps({"type":"object","properties":{"code":{"type":"string"}},"required":["code"],"additionalProperties":False}))
    # Sequential calls avoid subscription-rate bursts and keep each arm isolated.
    results=[run_arm(name,ps[name],schema) for name in ("direct","semantic","structural","human")]
    manifest={"problem_id":"C05","engine":"codex subscription","arms":results}
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    print(json.dumps(manifest,indent=2)); return 0 if all(x["generation_rc"]==0 and x["test_rc"]==0 for x in results) else 1
if __name__=="__main__": raise SystemExit(main())
