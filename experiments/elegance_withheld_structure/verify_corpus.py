#!/usr/bin/env python3
"""M1 audit: frozen selection plus independent semantic verdicts."""
from __future__ import annotations
import json, re, sys
from pathlib import Path
MIN_INCLUDED=15

def norm(v): return re.sub(r"[^a-z0-9]+", " ", str(v).lower()).strip()
def phrase_present(phrase,text): return f" {norm(phrase)} " in f" {norm(text)} "

def audit(path: Path):
    errors=[]
    try: data=json.loads(path.read_text())
    except Exception as exc: return [f"corpus is not valid JSON: {exc}"]
    if data.get("schema_version") != 2: errors.append("schema_version must be 2 (semantic-audit corpus)")
    items=data.get("candidates")
    if not isinstance(items,list): return errors+["candidates must be a list"]
    ids=set(); included=[]; excluded=[]
    for n,item in enumerate(items):
        if not isinstance(item,dict): errors.append(f"candidate[{n}] must be object"); continue
        cid=item.get("id",f"candidate[{n}]")
        if cid in ids: errors.append(f"{cid}: duplicate id")
        ids.add(cid)
        status=item.get("status")
        if status not in {"included","excluded"}: errors.append(f"{cid}: invalid status"); continue
        review=item.get("semantic_review")
        if not isinstance(review,dict): errors.append(f"{cid}: missing independent semantic_review"); continue
        verdict=review.get("verdict")
        expected="include" if status=="included" else "exclude"
        if verdict != expected: errors.append(f"{cid}: status={status} conflicts with semantic review verdict={verdict}")
        if review.get("reviewer") != "elegance-corpus-reviewer": errors.append(f"{cid}: semantic reviewer identity missing/unexpected")
        if len(str(review.get("reason","")).split()) < 8: errors.append(f"{cid}: semantic review reason too short")
        requirement=str(item.get("requirement","")); cues=item.get("forbidden_cues")
        if not requirement: errors.append(f"{cid}: empty requirement")
        if not isinstance(cues,list) or not cues: errors.append(f"{cid}: forbidden_cues must be non-empty"); cues=[]
        hits=[str(cue) for cue in cues if phrase_present(cue,requirement)]
        if status=="included":
            included.append(item)
            for field in ("domain","title","hidden_structure","human_analogy","admission_rationale"):
                if not item.get(field): errors.append(f"{cid}: missing {field}")
            if hits: errors.append(f"{cid}: included prompt leaks forbidden mechanism cue(s): {', '.join(hits)}")
            if len(str(item.get("admission_rationale","")).split()) < 20: errors.append(f"{cid}: rationale too short to establish alternatives")
            if len(str(item.get("human_analogy","")).split()) < 12: errors.append(f"{cid}: hand analogy too short")
            if "standard librar" not in requirement.lower() and "stdlib" not in requirement.lower(): errors.append(f"{cid}: stdlib executable boundary absent")
        else:
            excluded.append(item)
            if not item.get("exclusion_reason"): errors.append(f"{cid}: missing exclusion_reason")
    required=max(MIN_INCLUDED,int(data.get("minimum_included",0)))
    if len(included)<required: errors.append(f"only {len(included)} included problems; need at least {required}")
    if len({i.get('domain') for i in included})<10: errors.append("included corpus spans fewer than 10 domains")
    if not excluded: errors.append("no exclusions preserve the selection boundary")
    return errors

def main():
    path=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name("corpus_candidates.json")
    errors=audit(path)
    if errors:
        print("FAIL: M1 semantic admission audit")
        for e in errors: print(f"- {e}")
        return 1
    data=json.loads(path.read_text()); inc=[x for x in data['candidates'] if x['status']=='included']; exc=[x for x in data['candidates'] if x['status']=='excluded']
    print("PASS: M1 semantic admission audit")
    print(f"included={len(inc)} excluded={len(exc)} domains={len({x['domain'] for x in inc})}")
    print("included_ids="+",".join(x['id'] for x in inc))
    print("reviewer=elegance-corpus-reviewer")
    return 0
if __name__=='__main__': raise SystemExit(main())
