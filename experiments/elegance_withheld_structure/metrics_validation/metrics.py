#!/usr/bin/env python3
from __future__ import annotations
import ast,json,sys
from pathlib import Path
PUBLIC={"base_renderer","subject","redact","legal","record","make_renderer"}
BRANCH=(ast.If,ast.For,ast.AsyncFor,ast.While,ast.IfExp,ast.comprehension,ast.ExceptHandler,ast.Match)
def measure(path):
    text=Path(path).read_text(); tree=ast.parse(text)
    lines=sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))
    cyclomatic=1
    for node in ast.walk(tree):
        if isinstance(node,BRANCH): cyclomatic+=1
        elif isinstance(node,ast.BoolOp): cyclomatic+=max(0,len(node.values)-1)
    top=[n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
    concepts=sum(1 for n in top if n not in PUBLIC)+sum(1 for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name not in top)
    imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports += [a.name.split('.')[0] for a in n.names]
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module.split('.')[0])
    std=set(getattr(sys,"stdlib_module_names",())); dependencies=sorted({x for x in imports if x not in std})
    return {"lines":lines,"cyclomatic":cyclomatic,"new_concepts":concepts,"dependencies":dependencies}
if __name__=="__main__": print(json.dumps(measure(sys.argv[1]),sort_keys=True))
