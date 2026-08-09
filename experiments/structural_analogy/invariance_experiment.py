"""Track-C invariance classification experiment over the frozen 14-case corpus."""
from __future__ import annotations
import argparse, hashlib, itertools, json, math, random, statistics, time
from pathlib import Path
from typing import Any
import experiment as core
import failure_experiment as failure

HERE=Path(__file__).resolve().parent
ARMS=("direct","semantic","structural")
CLASSES={"invariant","broad","domain_specific"}

def load_corpus(path:Path)->dict[str,Any]:
    d=json.loads(path.read_text())
    ids={c["case_id"] for c in d["cases"]}
    if len(d["cases"])!=14 or len(ids)!=14: raise ValueError("requires 14 unique cases")
    for r in d["rules"]:
        if set(r["applies_to"])-ids: raise ValueError(f"unknown gold case in {r['rule_id']}")
        if r["gold_support_count"]!=len(r["applies_to"]): raise ValueError("gold count mismatch")
        expected="invariant" if len(r["applies_to"])==14 else ("broad" if len(r["applies_to"])>=3 else "domain_specific")
        if r["gold_class"]!=expected: raise ValueError("gold class mismatch")
    return d

def semantic_top_k(rule_text:str,cases:list[dict[str,Any]],k:int=3)->dict[str,Any]:
    texts=[rule_text]+[c["surface"] for c in cases]
    vectors=failure.ollama_embeddings(texts,"nomic-embed-text")
    ranked=sorted([(failure.cosine(vectors[0],vectors[i+1]),c["case_id"],c) for i,c in enumerate(cases)],
                  key=lambda x:(-x[0],x[1]))
    return {"cases":[x[2] for x in ranked[:k]],"ranking":[{"case_id":cid,"score":round(score,6)} for score,cid,_ in ranked],
            "embedding_model":"nomic-embed-text"}

def parse_prediction(parsed:Any)->dict[str,Any]:
    if isinstance(parsed,list): parsed=parsed[0] if parsed else {}
    if not isinstance(parsed,dict): raise ValueError("prediction is not object")
    classification=str(parsed.get("classification","")).lower().replace("-","_").replace(" ","_")
    aliases={"domain_specific_rule":"domain_specific","mid":"broad","general":"broad","universal":"invariant"}
    classification=aliases.get(classification,classification)
    if classification not in CLASSES: raise ValueError(f"invalid class {classification!r}")
    count=int(parsed.get("predicted_support_count"))
    if not 0<=count<=14: raise ValueError("support count outside 0..14")
    reasoning=parsed.get("reasoning") or parsed.get("rationale")
    if not isinstance(reasoning,str) or not reasoning.strip(): raise ValueError("missing reasoning")
    return {"classification":classification,"predicted_support_count":count,"reasoning":reasoning.strip(),"model_json":parsed}

def call_prediction(client:core.DeepSeekClient,rule:dict[str,Any],arm:str,cases:list[dict[str,Any]],semantic:dict[str,Any]|None=None)->dict[str,Any]:
    common=("Classify how widely this candidate rule actually holds in a fixed corpus of 14 failures. Classes: invariant=14/14, "
            "broad=3..13, domain_specific=0..2. Return JSON only with classification, predicted_support_count (0..14), "
            "and concise reasoning. Do not infer class from words like always/never; decide from evidence available to your arm.")
    if arm=="direct":
        user=f"RULE: {rule['text']}\nYou receive no corpus evidence. Make your best calibrated prediction and state uncertainty in reasoning."
    elif arm=="semantic":
        rows=[{"case_id":c["case_id"],"domain":c["domain"],"surface":c["surface"]} for c in semantic["cases"]]
        user=f"RULE: {rule['text']}\nThese are the top 3 surface-semantic neighbors out of 14; the other 11 are unseen:\n{json.dumps(rows)}"
    else:
        rows=[{"case_id":c["case_id"],"system":c["system"],"domain":c["domain"],"surface":c["surface"],
               "roles":c["roles"],"relations":c["relations"]} for c in cases]
        common+=(" Also return per_case as exactly 14 objects {case_id,applies,reason}. Use explicit role/relation comparison "
                 "across unlike domains; predicted_support_count must equal the number with applies=true.")
        user=f"RULE: {rule['text']}\nCROSS-DOMAIN CASE GRAPHS:\n{json.dumps(rows)}"
    call=client.json_call(common,user,temperature=0.15,max_tokens=3500)
    pred=parse_prediction(call["parsed"])
    pred["call"]={k:call[k] for k in ("raw_content","usage","request_sha256","response_id")}
    if arm=="semantic": pred["retrieval"]={"selected_case_ids":[c["case_id"] for c in semantic["cases"]],"ranking":semantic["ranking"],"embedding_model":semantic["embedding_model"]}
    if arm=="structural":
        rows=pred["model_json"].get("per_case")
        if not isinstance(rows,list) or len(rows)!=14: raise ValueError("structural per_case must contain 14 rows")
        lookup={str(x.get("case_id")):x for x in rows}
        if set(lookup)!={c["case_id"] for c in cases}: raise ValueError("structural per_case ids mismatch")
        yes=sum(bool(x.get("applies")) for x in lookup.values())
        if yes!=pred["predicted_support_count"]: raise ValueError(f"structural count {pred['predicted_support_count']} != per_case {yes}")
        pred["per_case"]=rows
    return pred

def judge(client:core.DeepSeekClient,rule:dict[str,Any],predictions:dict[str,dict[str,Any]],repeat:int)->dict[str,Any]:
    order=list(ARMS); random.Random(int(hashlib.sha256(f"inv:{rule['rule_id']}:{repeat}".encode()).hexdigest()[:16],16)).shuffle(order)
    labels={arm:f"P{i+1}" for i,arm in enumerate(order)}
    candidates=[{"candidate_id":labels[a],"classification":predictions[a]["classification"],
                 "predicted_support_count":predictions[a]["predicted_support_count"],"reasoning":predictions[a]["reasoning"]} for a in order]
    system=("Blindly judge invariance analyses against supplied ground truth. Return JSON object with scores array; each row has "
            "candidate_id, usefulness and evidence_quality integers 1..5, and reason. Do not reward verbosity or guess method. "
            "Usefulness=5 requires correct class, close support count, and reasoning aligned with the ground-truth applicability; "
            "1 is materially wrong or misleading.")
    user=json.dumps({"rule":rule["text"],"gold_class":rule["gold_class"],"gold_support_count":rule["gold_support_count"],
                     "gold_applicable_case_ids":rule["applies_to"],"candidates":candidates})
    call=client.json_call(system,user,temperature=0,max_tokens=1600); parsed=call["parsed"]
    rows=parsed if isinstance(parsed,list) else parsed.get("scores") if isinstance(parsed,dict) else None
    if not isinstance(rows,list): raise ValueError("judge scores absent")
    lookup={str(x.get("candidate_id")):x for x in rows}; scores={}
    for arm in ARMS:
        row=lookup.get(labels[arm]);
        if row is None: raise ValueError("judge omitted arm")
        scores[arm]={"usefulness":int(row["usefulness"]),"evidence_quality":int(row["evidence_quality"]),"reason":str(row.get("reason",""))}
        if any(not 1<=scores[arm][m]<=5 for m in ("usefulness","evidence_quality")): raise ValueError("judge score out of range")
    return {"repeat":repeat,"labels":labels,"scores":scores,"call":{k:call[k] for k in ("raw_content","usage","request_sha256","response_id")}}

def mcnemar_exact(a:list[bool],b:list[bool])->dict[str,Any]:
    b_only=sum((not x) and y for x,y in zip(a,b)); a_only=sum(x and (not y) for x,y in zip(a,b)); n=b_only+a_only
    if n==0:return {"structural_only_correct":b_only,"baseline_only_correct":a_only,"discordant":0,"two_sided_p":1.0}
    # exact two-sided binomial at p=.5
    tail=sum(math.comb(n,k) for k in range(0,min(a_only,b_only)+1))/(2**n)
    return {"structural_only_correct":b_only,"baseline_only_correct":a_only,"discordant":n,"two_sided_p":min(1.0,2*tail)}

def summarize(records:list[dict[str,Any]])->dict[str,Any]:
    out={}; correctness={}
    for arm in ARMS:
        correct=[r["predictions"][arm]["classification"]==r["gold_class"] for r in records]; correctness[arm]=correct
        inv_gold=[r["gold_class"]=="invariant" for r in records]; inv_pred=[r["predictions"][arm]["classification"]=="invariant" for r in records]
        tp=sum(g and p for g,p in zip(inv_gold,inv_pred)); fn=sum(g and not p for g,p in zip(inv_gold,inv_pred)); tn=sum(not g and not p for g,p in zip(inv_gold,inv_pred)); fp=sum(not g and p for g,p in zip(inv_gold,inv_pred))
        sensitivity=tp/(tp+fn) if tp+fn else 0; specificity=tn/(tn+fp) if tn+fp else 0
        usefulness=[statistics.mean(j["scores"][arm]["usefulness"] for j in r["judgments"]) for r in records]
        errors=[abs(r["predictions"][arm]["predicted_support_count"]-r["gold_support_count"]) for r in records]
        out[arm]={"class_accuracy":sum(correct)/len(correct),"correct":sum(correct),"n":len(correct),
                  "support_count_mae":statistics.mean(errors),"invariant_sensitivity":sensitivity,
                  "invariant_specificity":specificity,"invariant_balanced_accuracy":(sensitivity+specificity)/2,
                  "mean_judged_usefulness":statistics.mean(usefulness),"per_rule_usefulness":usefulness}
    comparisons={base:{"class_accuracy_delta":out["structural"]["class_accuracy"]-out[base]["class_accuracy"],
                       "support_mae_delta":out["structural"]["support_count_mae"]-out[base]["support_count_mae"],
                       "judged_usefulness":core.paired_stats(out["structural"]["per_rule_usefulness"],out[base]["per_rule_usefulness"],seed=8110+i),
                       "mcnemar":mcnemar_exact(correctness[base],correctness["structural"])} for i,base in enumerate(("direct","semantic"))}
    return {"arms":out,"structural_minus_baseline":comparisons}

def run(output:Path,judge_repeats:int=3)->dict[str,Any]:
    corpus=load_corpus(HERE/"invariance_cases.json"); client=core.DeepSeekClient("deepseek-v4-flash")
    checkpoint=output.with_suffix(".checkpoint.json"); done={}
    if checkpoint.exists():done={r["rule_id"]:r for r in json.loads(checkpoint.read_text()).get("records",[])}
    for rule in corpus["rules"]:
        if rule["rule_id"] in done:continue
        sem=semantic_top_k(rule["text"],corpus["cases"],3)
        preds={arm:call_prediction(client,rule,arm,corpus["cases"],sem) for arm in ARMS}
        judgments=[judge(client,rule,preds,i) for i in range(judge_repeats)]
        done[rule["rule_id"]]={"rule_id":rule["rule_id"],"rule_text":rule["text"],"gold_class":rule["gold_class"],
                               "gold_support_count":rule["gold_support_count"],"gold_applicable_case_ids":rule["applies_to"],
                               "predictions":preds,"judgments":judgments}
        checkpoint.write_text(json.dumps({"records":list(done.values())},indent=2)+"\n")
    records=[done[r["rule_id"]] for r in corpus["rules"]]
    result={"experiment":"cross_domain_invariance","created_at_epoch":int(time.time()),"model":"deepseek-v4-flash",
            "judge_model":"deepseek-v4-flash","semantic_embedding_model":"nomic-embed-text","corpus_sha256":core.sha256_json(corpus),
            "records":records,"summary":summarize(records),
            "limitations":["Human-authored applicability matrix; independently audited before generation.","N=12 rules over one 14-case defect class.","Direct arm intentionally has no corpus evidence; this tests evidence use, not equal information.","Same model family generates and judges; labels and order are blinded."]}
    output.write_text(json.dumps(result,indent=2)+"\n");return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output",required=True);args=ap.parse_args();r=run(Path(args.output))
    print(json.dumps({"output":args.output,"arms":{a:{k:v for k,v in r['summary']['arms'][a].items() if k in ('class_accuracy','support_count_mae','invariant_balanced_accuracy','mean_judged_usefulness')} for a in ARMS},
      "deltas":{b:{"class_accuracy":v['class_accuracy_delta'],"support_mae":v['support_mae_delta'],"usefulness":v['judged_usefulness']['mean_delta'],"mcnemar_p":v['mcnemar']['two_sided_p']} for b,v in r['summary']['structural_minus_baseline'].items()}},indent=2))
if __name__=="__main__":main()
