#!/usr/bin/env python3
from __future__ import annotations
import json, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
OUT=HERE/"results"
SCHEMA={"type":"object","properties":{"code":{"type":"string"}},"required":["code"],"additionalProperties":False}

def run_one(task,arm,schema_path):
    d=OUT/task["id"]/arm; d.mkdir(parents=True,exist_ok=True)
    prompt=task["requirement"]+"\n\n"+task["contract"]
    if arm=="structural":
        spec=json.loads((HERE/"control_spec.json").read_text())
        ctx={"analogue":spec["structural_analogy"],"role_correspondences":task["map"]}
        prompt += "\n\nMACHINE-RETRIEVED CROSS-DOMAIN ANALOGUE AND EXPLICIT MAP:\n"+json.dumps(ctx,indent=2)+"\nTransfer useful relations, not vocabulary."
    prompt += "\nReturn a complete solution.py as the schema code string. Do not emit literal backslash-n characters after the final Python statement."
    (d/"prompt.txt").write_text(prompt)
    with tempfile.TemporaryDirectory(prefix=f"elegance-m4-{task['id']}-{arm}-") as td:
        last=Path(td)/"last.json"
        cmd=["codex","exec","--ephemeral","--ignore-rules","--skip-git-repo-check","-s","read-only","-C",td,"--output-schema",str(schema_path),"-o",str(last),prompt]
        cp=subprocess.run(cmd,text=True,capture_output=True,timeout=600)
        (d/"codex_stdout.txt").write_text(cp.stdout); (d/"codex_stderr.txt").write_text(cp.stderr)
        if cp.returncode: return {"task":task["id"],"arm":arm,"generation_rc":cp.returncode,"test_rc":None}
        raw=last.read_text(); (d/"response.json").write_text(raw)
        code=json.loads(raw)["code"]; normalization=[]
        if code.endswith("\\n"):
            code=code[:-2]; normalization.append("stripped_terminal_literal_backslash_n")
        (d/"normalization.json").write_text(json.dumps(normalization)+"\n"); (d/"solution.py").write_text(code.rstrip()+"\n")
    test=HERE/f"test_{task['test']}.py"
    cp=subprocess.run([sys.executable,str(test),str(d/"solution.py")],text=True,capture_output=True)
    (d/"test_stdout.txt").write_text(cp.stdout); (d/"test_stderr.txt").write_text(cp.stderr)
    return {"task":task["id"],"arm":arm,"generation_rc":0,"test_rc":cp.returncode,"test_output":cp.stdout+cp.stderr}

def main():
    if OUT.exists(): raise SystemExit("results already exist; frozen run refuses overwrite")
    spec=json.loads((HERE/"control_spec.json").read_text()); OUT.mkdir(); schema=OUT/"code_schema.json"; schema.write_text(json.dumps(SCHEMA)+"\n")
    records=[]
    for task in spec["tasks"]:
        for arm in spec["pre_registration"]["arms"]: records.append(run_one(task,arm,schema))
    (OUT/"manifest.json").write_text(json.dumps({"engine":"codex subscription","records":records},indent=2)+"\n")
    print(json.dumps(records,indent=2)); return 0 if all(r["generation_rc"]==0 and r["test_rc"]==0 for r in records) else 1
if __name__=="__main__": raise SystemExit(main())
