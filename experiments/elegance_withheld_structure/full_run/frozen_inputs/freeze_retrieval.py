#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
def toks(s): return set(re.findall(r"[a-z][a-z0-9_]{2,}",s.lower()))
def semantic(contract):
 q=toks(contract); ranked=[]
 for p in ROOT.rglob("*.py"):
  if "experiments" in p.parts or ".git" in p.parts: continue
  try: text=p.read_text(errors="ignore")
  except OSError: continue
  if len(text)>40000: text=text[:40000]
  t=toks(text); score=len(q&t)/(len(q|t) or 1); ranked.append((score,str(p.relative_to(ROOT)),text[:3500]))
 score,path,excerpt=max(ranked,key=lambda x:(x[0],x[1]))
 return {"path":path,"score":score,"excerpt_sha256":hashlib.sha256(excerpt.encode()).hexdigest(),"excerpt":excerpt}
def structural(data,target):
 q=set(target["query_relation_tags"]); ranked=[]
 for src in data["source_library"]:
  tags=set(src["relation_tags"]); score=len(q&tags)/(len(q|tags) or 1); ranked.append((score,src["id"],src))
 score,_,src=sorted(ranked,key=lambda x:(-x[0],x[1]))[0]
 pairs=[]
 for k in sorted(set(src["source_role_map"])&set(target["target_role_map"])):
  pairs.append({"abstract_role":k,"source_role":src["source_role_map"][k],"target_role":target["target_role_map"][k]})
 return {"source_id":src["id"],"source_domain":src["domain"],"score":score,"description":src["description"],"relation_tags":src["relation_tags"],"role_correspondences":pairs}
def main():
 tasks=json.loads((HERE/"task_contracts.json").read_text()); sdata=json.loads((HERE/"structural_retrieval_inputs.json").read_text()); targets={x["id"]:x for x in sdata["targets"]}
 sem={x["id"]:semantic(x["contract"]) for x in tasks}; st={x["id"]:structural(sdata,targets[x["id"]]) for x in tasks}
 (HERE/"semantic_contexts.json").write_text(json.dumps(sem,indent=2)+"\n"); (HERE/"structural_contexts.json").write_text(json.dumps(st,indent=2)+"\n")
 print(f"PASS: retrieval frozen semantic={len(sem)} structural={len(st)}")
 for cid in st: print(f"{cid} semantic={sem[cid]['path']} structural={st[cid]['source_id']} score={st[cid]['score']:.3f} roles={len(st[cid]['role_correspondences'])}")
if __name__=="__main__": main()
