#!/usr/bin/env python3
from __future__ import annotations
import json,math,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent; OUT=HERE/"results"; ARMS=("direct","semantic","structural","human")
def wilson(w,n,z=1.96):
 if not n:return [None,None]
 p=w/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d; return [c-h,c+h]
def summarize(task_ids,scores):
 smap={(x["task"],x["arm"]):x for x in scores}; out={}
 for arm in ARMS[1:]:
  pairs=[]; wins=losses=0
  for cid in task_ids:
   a=smap[(cid,"direct")]; b=smap[(cid,arm)]
   if a["correct"] and b["correct"]:
    diff={k:b["metrics"][k]-a["metrics"][k] for k in ("lines","cyclomatic","new_concepts")}; diff["dependency_delta"]=len(b["metrics"]["dependencies"])-len(a["metrics"]["dependencies"]); pairs.append({"task":cid,"differences":diff})
    dec=OUT/"judgments"/cid/"decoded.json"
    if dec.exists():
     ranking=json.loads(dec.read_text())["ranking"]
     if arm in ranking and "direct" in ranking:
      if ranking.index(arm)<ranking.index("direct"): wins+=1
      else: losses+=1
  med={k:(statistics.median(x["differences"][k] for x in pairs) if pairs else None) for k in ("lines","cyclomatic","new_concepts","dependency_delta")}
  mechanical=sum(med[k]<0 for k in ("lines","cyclomatic","new_concepts"))>=2 and med["dependency_delta"]<=0 if pairs else False
  preference=wins>losses
  out[arm]={"eligible_pairs":len(pairs),"correctness_gated_pairs":pairs,"median_differences":med,"mechanical_support":mechanical,"maintainability":{"wins":wins,"losses":losses,"win_rate":wins/(wins+losses) if wins+losses else None,"wilson95":wilson(wins,wins+losses)},"beats_direct_primary_rule":bool(mechanical and preference)}
 return out
def main():
 tasks=json.loads((HERE/"frozen_inputs"/"task_contracts.json").read_text()); ids=[x["id"] for x in tasks]; scores=json.loads((OUT/"mechanical_scores.json").read_text()); manifest=json.loads((OUT/"manifest.json").read_text())
 correctness={arm:sum(r["arm"]==arm and r["test_rc"]==0 for r in manifest["records"]) for arm in ARMS}; classical=[x for x in ids if x.startswith("C")]; broad=[x for x in ids if x.startswith("B")]
 result={"cells":len(manifest["records"]),"correctness_by_arm":correctness,"all_problems":summarize(ids,scores),"broad_withheld_stratum":summarize(broad,scores),"classical_high_prior_stratum":summarize(classical,scores),"primary_outcome":"null"}
 if result["all_problems"]["human"]["beats_direct_primary_rule"] and not result["all_problems"]["structural"]["beats_direct_primary_rule"]: result["primary_outcome"]="retrieval_mapping_bottleneck"
 elif result["all_problems"]["human"]["beats_direct_primary_rule"] or result["all_problems"]["structural"]["beats_direct_primary_rule"]: result["primary_outcome"]="at_least_one_analogy_arm_beats_direct"
 result["creative_jump_claim"]="not established: Codex cells have no same-seed analogue-removal ablation"
 (OUT/"analysis.json").write_text(json.dumps(result,indent=2)+"\n"); print(json.dumps(result,indent=2))
if __name__=="__main__": main()
