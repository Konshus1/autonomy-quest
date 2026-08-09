#!/usr/bin/env python3
from __future__ import annotations
import ast,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"
def measure(path,public):
 text=Path(path).read_text(); tree=ast.parse(text); lines=sum(1 for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#")); cyclomatic=1
 branch=(ast.If,ast.For,ast.AsyncFor,ast.While,ast.IfExp,ast.comprehension,ast.ExceptHandler,ast.Match)
 for n in ast.walk(tree):
  if isinstance(n,branch): cyclomatic+=1
  elif isinstance(n,ast.BoolOp): cyclomatic+=max(0,len(n.values)-1)
 top=[n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
 concepts=sum(n not in public for n in top)+sum(1 for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name not in top)
 imports=[]
 for n in ast.walk(tree):
  if isinstance(n,ast.Import): imports += [a.name.split('.')[0] for a in n.names]
  elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module.split('.')[0])
 deps=sorted({x for x in imports if x not in getattr(sys,'stdlib_module_names',set())})
 return {"lines":lines,"cyclomatic":cyclomatic,"new_concepts":concepts,"dependencies":deps}
def main():
 tasks=json.loads((HERE/"frozen_inputs"/"task_contracts.json").read_text()); public={x["id"]:set(x["public_symbols"]) for x in tasks}; manifest=json.loads((OUT/"manifest.json").read_text()); scores=[]
 for r in manifest["records"]:
  row={"task":r["task"],"arm":r["arm"],"correct":r["test_rc"]==0,"metrics":None}
  if row["correct"]: row["metrics"]=measure(OUT/r["task"]/r["arm"]/"solution.py",public[r["task"]])
  scores.append(row)
 (OUT/"mechanical_scores.json").write_text(json.dumps(scores,indent=2)+"\n"); print(f"PASS: scored cells={len(scores)} correct={sum(x['correct'] for x in scores)}")
if __name__=="__main__": main()
