"""Harder Track-C pass: failure-structure analogy and falsifier generation.

Explicit CLI only. Unwired from AQ. DeepSeek is used for generation/judging;
Ollama nomic-embed-text provides the frozen local semantic retrieval baseline.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Any

import experiment as core

HERE = Path(__file__).resolve().parent
ARMS = ("direct", "semantic", "structural")


def _post_json(url: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def ollama_embeddings(texts: list[str], model: str = "nomic-embed-text") -> list[list[float]]:
    data = _post_json("http://localhost:11434/api/embed", {"model": model, "input": texts})
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError("Ollama embedding response has wrong shape")
    return vectors


def cosine(a: list[float], b: list[float]) -> float:
    denom = math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / denom if denom else 0.0


def semantic_retrieve(target_text: str, sources: list[dict[str, Any]], model: str = "nomic-embed-text") -> dict[str, Any]:
    texts = [target_text] + [x["surface"] for x in sources]
    vectors = ollama_embeddings(texts, model)
    scored = [(cosine(vectors[0], vectors[i+1]), x["id"], x) for i, x in enumerate(sources)]
    scored.sort(key=lambda row: (-row[0], row[1]))
    score, _, source = scored[0]
    return {"source": source, "score": round(score, 6), "embedding_model": model,
            "ranking": [{"source_id": sid, "score": round(s, 6)} for s, sid, _ in scored]}


def normalize_receipt_corpus(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    return {"name": "receipt_failure", "sources": data["sources"] + data["lures"],
            "defect_source_ids": [x["id"] for x in data["sources"]], "targets": data["targets"],
            "require_cross_domain": True, "sha256": core.sha256_json(data)}


def normalize_watchdog_corpus(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    sources = []
    for row in data["analogy_kb"]:
        sources.append({"id": row["id"], "label": "watchdog_case", "domain": row["domain"],
                        "surface": row["summary"], "roles": row["roles"],
                        "relations": [x[:3] for x in row["observed_relations"]],
                        "transfer_relations": [x[:3] for x in row["transfer_relations"]],
                        "transferred_lesson": row["summary"]})
    targets = [{"id": x["id"], "provenance": "curated homogeneous Watchdog control",
                "domain": x["domain"], "problem": x["problem"], "roles": x["roles"],
                "relations": [r[:3] for r in x["observed_relations"]]} for x in data["problems"]]
    return {"name": "watchdog", "sources": sources, "defect_source_ids": [x["id"] for x in sources],
            "targets": targets, "require_cross_domain": False, "sha256": core.sha256_json(data)}


def _call_json(client: core.DeepSeekClient, system: str, user: str, *, temperature: float = 0.25) -> tuple[Any, dict[str, Any]]:
    call = client.json_call(system, user, temperature=temperature, max_tokens=2200)
    return call["parsed"], {k: call[k] for k in ("raw_content", "usage", "request_sha256", "response_id")}


def _candidate_text(parsed: Any) -> str:
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        raise ValueError("candidate response is not an object")
    text = parsed.get("candidate_text") or parsed.get("answer")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"candidate_text missing: {parsed!r}")
    return text.strip()


def direct_candidate(client: core.DeepSeekClient, target: dict[str, Any]) -> dict[str, Any]:
    system = ("Propose a falsifiable way to evaluate the claim. Return JSON only with candidate_text, "
              "classification (receipt_defect|not_receipt_defect|uncertain), and falsifying_result. "
              "The candidate must say what direct observation could prove the claim false. <=180 words.")
    parsed, call = _call_json(client, system, target["problem"])
    return {"candidate_text": _candidate_text(parsed), "model_json": parsed, "call": call}


def semantic_candidate(client: core.DeepSeekClient, target: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    source = retrieval["source"]
    system = ("Use the retrieved case as ordinary semantic retrieval context and propose a falsifiable check. "
              "Do not invent or describe a role/relation mapping. Return JSON only with candidate_text, "
              "classification, and falsifying_result. <=180 words.")
    user = f"TARGET:\n{target['problem']}\n\nSEMANTICALLY RETRIEVED CASE:\n{source['surface']}"
    parsed, call = _call_json(client, system, user)
    return {"candidate_text": _candidate_text(parsed), "model_json": parsed, "call": call,
            "retrieval": {k:v for k,v in retrieval.items() if k != "source"}, "source_id": source["id"],
            "source_label": source["label"]}


def structural_candidate(client: core.DeepSeekClient, target: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    library = [{"id": x["id"], "domain": x["domain"], "surface": x["surface"], "roles": x["roles"],
                "relations": x["relations"], "transfer_relations": x.get("transfer_relations")} for x in sources]
    system = ("Perform Gentner-style structure mapping, not topical similarity. Select exactly one source whose "
              "directed relational structure best matches the target. Return JSON only with: source_id; "
              "role_correspondences (array of {source_role,target_role,why}, a one-to-one partial bijection: no role repeated); "
              "relation_correspondences (array of {source_relation,target_relation,why}, where each relation is the exact "
              "three-string array copied from its graph); transferred_candidate_inference (a falsifier derived through the "
              "map and not stated in the target); candidate_text (self-contained <=180 words, without mentioning this "
              "experiment); classification; falsifying_result. Use >=2 role and >=2 relation correspondences. Never invent "
              "a target role or relation. If a source observer/check has no target counterpart, leave it unmapped and instantiate "
              "it only in transferred_candidate_inference.")
    user = "TARGET:\n" + json.dumps({"problem": target["problem"], "roles": target["roles"],
              "relations": target["relations"], "domain": target["domain"]}) + "\n\nSOURCE LIBRARY:\n" + json.dumps(library)
    parsed, call = _call_json(client, system, user)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        raise ValueError("structural response is not an object")
    result = {"candidate_text": _candidate_text(parsed), "model_json": parsed, "call": call,
              "source_id": parsed.get("source_id"),
              "role_correspondences": parsed.get("role_correspondences", []),
              "relation_correspondences": parsed.get("relation_correspondences", []),
              "transferred_candidate_inference": parsed.get("transferred_candidate_inference")}
    return canonicalize_structural_mapping(target, sources, result)




def canonicalize_structural_mapping(target: dict[str, Any], sources: list[dict[str, Any]], structural: dict[str, Any]) -> dict[str, Any]:
    """Drop invented/non-bijective correspondence rows without inventing replacements."""
    source = next((x for x in sources if x["id"] == structural.get("source_id")), None)
    if source is None:
        return structural
    cleaned = copy.deepcopy(structural)
    dropped: list[dict[str, Any]] = []
    roles_out = []; source_seen: set[str] = set(); target_seen: set[str] = set()
    for row in cleaned.get("role_correspondences", []):
        sr, tr = row.get("source_role"), row.get("target_role")
        if sr not in source["roles"] or tr not in target["roles"] or sr in source_seen or tr in target_seen:
            dropped.append({"kind":"role","row":row}); continue
        source_seen.add(sr); target_seen.add(tr); roles_out.append(row)
    role_map = {row["source_role"]: row["target_role"] for row in roles_out}
    source_rel = {tuple(x) for x in source["relations"]}; target_rel = {tuple(x) for x in target["relations"]}
    rel_out = []
    for row in cleaned.get("relation_correspondences", []):
        sr, tr = row.get("source_relation"), row.get("target_relation")
        topology_ok = (isinstance(sr, list) and isinstance(tr, list) and len(sr) == 3 and len(tr) == 3
                       and role_map.get(sr[0]) == tr[0] and role_map.get(sr[2]) == tr[2])
        if not isinstance(sr,list) or not isinstance(tr,list) or tuple(sr) not in source_rel or tuple(tr) not in target_rel or not topology_ok:
            dropped.append({"kind":"relation","row":row}); continue
        rel_out.append(row)
    cleaned["role_correspondences"] = roles_out
    cleaned["relation_correspondences"] = rel_out
    cleaned["dropped_invalid_correspondences"] = dropped
    return cleaned

def validate_structural_mapping(target: dict[str, Any], source: dict[str, Any], structural: dict[str, Any]) -> list[str]:
    """Machine-check references and one-to-one mapping; semantic validity remains an audit task."""
    errors: list[str] = []
    roles = structural.get("role_correspondences")
    relations = structural.get("relation_correspondences")
    inference = structural.get("transferred_candidate_inference")
    if not isinstance(roles, list) or len(roles) < 2:
        errors.append("fewer than two role correspondences")
        roles = []
    source_seen: set[str] = set()
    target_seen: set[str] = set()
    for row in roles:
        sr, tr = row.get("source_role"), row.get("target_role")
        if sr not in source["roles"]: errors.append(f"unknown source role: {sr}")
        if tr not in target["roles"]: errors.append(f"unknown target role: {tr}")
        if sr in source_seen: errors.append(f"repeated source role: {sr}")
        if tr in target_seen: errors.append(f"repeated target role: {tr}")
        source_seen.add(sr); target_seen.add(tr)
    if not isinstance(relations, list) or len(relations) < 2:
        errors.append("fewer than two relation correspondences")
        relations = []
    role_map = {row.get("source_role"): row.get("target_role") for row in roles}
    source_relations = {tuple(x) for x in source["relations"]}
    target_relations = {tuple(x) for x in target["relations"]}
    for row in relations:
        sr, tr = row.get("source_relation"), row.get("target_relation")
        if not isinstance(sr, list) or tuple(sr) not in source_relations:
            errors.append(f"source relation not copied from graph: {sr!r}")
        if not isinstance(tr, list) or tuple(tr) not in target_relations:
            errors.append(f"target relation not copied from graph: {tr!r}")
        if isinstance(sr, list) and isinstance(tr, list) and len(sr) == 3 and len(tr) == 3:
            if role_map.get(sr[0]) != tr[0] or role_map.get(sr[2]) != tr[2]:
                errors.append(f"relation endpoints do not follow role map: {sr!r} -> {tr!r}")
    if not isinstance(inference, str) or not inference.strip():
        errors.append("missing transferred candidate inference")
    return errors

def audit_mapping(client: core.DeepSeekClient, target: dict[str, Any], source: dict[str, Any], structural: dict[str, Any]) -> dict[str, Any]:
    system = ("Audit a proposed cross-domain structure map. Ignore prose style and desired experiment outcome. "
              "Return JSON only with valid (boolean), role_validity (0-4), relation_preservation (0-4), "
              "inference_follows (0-4), and reason. valid may be true only if >=2 role correspondences are coherent, "
              ">=2 directed relations are preserved, and the candidate inference follows through the mapping.")
    payload = {"target": target, "source": source,
               "role_correspondences": structural["role_correspondences"],
               "relation_correspondences": structural["relation_correspondences"],
               "transferred_candidate_inference": structural["transferred_candidate_inference"]}
    parsed, call = _call_json(client, system, json.dumps(payload), temperature=0.0)
    if isinstance(parsed, list): parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("valid"), bool):
        raise ValueError(f"invalid audit response: {parsed!r}")
    return {"result": parsed, "call": call}


def scramble_mapping(structural: dict[str, Any]) -> dict[str, Any]:
    broken = copy.deepcopy(structural)
    roles = broken.get("role_correspondences", [])
    if len(roles) >= 2:
        targets = [x.get("target_role") for x in roles]
        targets = targets[1:] + targets[:1]
        for row, target_role in zip(roles, targets): row["target_role"] = target_role
    relations = broken.get("relation_correspondences", [])
    relations.reverse()
    broken["transferred_candidate_inference"] = "The receipt itself proves the destination effect; no independent observation is needed."
    return broken


def judge_candidates(client: core.DeepSeekClient, target: dict[str, Any], candidates: dict[str, dict[str, Any]], repeat: int) -> dict[str, Any]:
    order = list(ARMS)
    seed = int(hashlib.sha256(f"failure:{target['id']}:{repeat}".encode()).hexdigest()[:16], 16)
    random.Random(seed).shuffle(order)
    labels = {arm: f"X{idx+1}" for idx, arm in enumerate(order)}
    rows = [{"candidate_id": labels[a], "candidate_text": candidates[a]["candidate_text"]} for a in order]
    system = ("Blindly judge candidate falsifiers. Return JSON only as an object with scores: an array containing "
              "candidate_id, usefulness, actionability, defect_recognition (integers 1-5), and reason. "
              "Usefulness=5 means the check directly observes the claimed effect, can genuinely return negative, "
              "is identity/scope bound, and would prevent a false success; 1 means circular or irrelevant. "
              "Do not reward analogy language or verbosity.")
    parsed, call = _call_json(client, system, json.dumps({"problem":target["problem"],"candidates":rows}), temperature=0.0)
    score_rows = parsed if isinstance(parsed, list) else parsed.get("scores") if isinstance(parsed, dict) else None
    if not isinstance(score_rows, list): raise ValueError(f"judge scores missing: {parsed!r}")
    lookup = {str(x.get("candidate_id")):x for x in score_rows}
    scores = {}
    for arm in ARMS:
        row = lookup.get(labels[arm])
        if row is None: raise ValueError(f"judge omitted {labels[arm]}")
        scores[arm] = {m:int(row[m]) for m in ("usefulness","actionability","defect_recognition")}
        scores[arm]["reason"] = str(row.get("reason",""))
        if any(not 1 <= scores[arm][m] <= 5 for m in ("usefulness","actionability","defect_recognition")):
            raise ValueError("judge score outside 1..5")
    return {"repeat":repeat,"labels":labels,"scores":scores,"call":call}


def run_one_corpus(client: core.DeepSeekClient, corpus: dict[str, Any], judge_repeats: int,
                   checkpoint: Path | None = None) -> dict[str, Any]:
    done = {}
    if checkpoint and checkpoint.exists():
        done = {r["problem_id"]:r for r in json.loads(checkpoint.read_text()).get("records",[])}
    source_by_id = {x["id"]:x for x in corpus["sources"]}
    for target in corpus["targets"]:
        if target["id"] in done: continue
        semantic = semantic_retrieve(target["problem"], corpus["sources"])
        candidates = {"direct":direct_candidate(client,target),
                      "semantic":semantic_candidate(client,target,semantic)}
        candidates["structural"] = structural_candidate(client,target,corpus["sources"])
        sid = candidates["structural"]["source_id"]
        if sid not in source_by_id: raise ValueError(f"unknown structural source {sid!r}")
        machine_errors = validate_structural_mapping(target, source_by_id[sid], candidates["structural"])
        if machine_errors: raise ValueError(f"invalid structural mapping for {target['id']}: {machine_errors}")
        audit = audit_mapping(client,target,source_by_id[sid],candidates["structural"])
        judgments = [judge_candidates(client,target,candidates,r) for r in range(judge_repeats)]
        done[target["id"]] = {"problem_id":target["id"],"provenance":target["provenance"],"domain":target["domain"],
                              "problem":target["problem"],"candidates":candidates,"mapping_audit":audit,
                              "judgments":judgments}
        if checkpoint: checkpoint.write_text(json.dumps({"records":list(done.values())},indent=2)+"\n")
    records=[done[x["id"]] for x in corpus["targets"]]
    summary=core.summarize(records)
    recognition=sum(r["candidates"]["structural"]["source_id"] in corpus["defect_source_ids"] and
                    r["mapping_audit"]["result"]["valid"] for r in records)
    semantic_defects=sum(r["candidates"]["semantic"]["source_id"] in corpus["defect_source_ids"] for r in records)
    return {"name":corpus["name"],"corpus_sha256":corpus["sha256"],"model":client.model,
            "semantic_embedding_model":"nomic-embed-text","judge_model":client.model,
            "records":records,"summary":summary,
            "retrieval_metrics":{"structural_valid_defect_retrievals":recognition,"n":len(records),
                                 "semantic_defect_retrievals":semantic_defects}}


def run_scrambled_control(client: core.DeepSeekClient, real_record: dict[str, Any], corpus: dict[str, Any]) -> dict[str, Any]:
    target=next(x for x in corpus["targets"] if x["id"]==real_record["problem_id"])
    sid=real_record["candidates"]["structural"]["source_id"]
    source=next(x for x in corpus["sources"] if x["id"]==sid)
    broken=scramble_mapping(real_record["candidates"]["structural"])
    machine_errors = validate_structural_mapping(target, source, broken)
    audit=audit_mapping(client,target,source,broken)
    return {"problem_id":target["id"],"broken_mapping":broken,"machine_errors":machine_errors,"audit":audit,
            "passed_negative_control": bool(machine_errors) and audit["result"]["valid"] is False}


def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",required=True); ap.add_argument("--judge-repeats",type=int,default=3)
    args=ap.parse_args(); out=Path(args.output); client=core.DeepSeekClient("deepseek-v4-flash")
    receipt=normalize_receipt_corpus(HERE/"receipt_failure_cases.json")
    watchdog=normalize_watchdog_corpus(HERE/"watchdog_cases.json")
    receipt_result=run_one_corpus(client,receipt,args.judge_repeats,out.with_suffix(".receipt.checkpoint.json"))
    real=next(r for r in receipt_result["records"] if r["problem_id"] == "ralph_empty_input_ambiguity")
    scrambled=run_scrambled_control(client,real,receipt)
    watchdog_result=run_one_corpus(client,watchdog,args.judge_repeats,out.with_suffix(".watchdog2.checkpoint.json"))
    watchdog_gain=any(x["significant_positive_gain"] for x in watchdog_result["summary"]["structural_minus_baseline"].values())
    valid_mappings=all(r["mapping_audit"]["result"]["valid"] for r in receipt_result["records"])
    if not scrambled["passed_negative_control"]: conclusion="invalid_scrambled_mapping_accepted"
    elif watchdog_gain: conclusion="invalid_watchdog_gain"
    elif valid_mappings: conclusion="measured"  # sign is in numeric deltas; null/negative is valid
    else: conclusion="invalid_mapping_audit"
    result={"experiment":"failure_structure_analogy","created_at_epoch":int(time.time()),"conclusion":conclusion,
            "receipt_failure":receipt_result,"scrambled_mapping_control":scrambled,"watchdog_control":watchdog_result,
            "limitations":["N=5 held-out receipt problems and N=8 Watchdog problems; this is a bounded probe, not #2406 clearance.",
                           "Source/target role graphs are human-labelled; automatic graph induction is not measured.",
                           "DeepSeek V4 Flash both generates and judges; candidate identities/order are blinded but family bias remains.",
                           "The local nomic-embed-text baseline measures surface semantic retrieval; structural transfer gets richer relational context by design."]}
    out.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"output":str(out),"conclusion":conclusion,
      "receipt_deltas":{k:v["mean_delta"] for k,v in receipt_result["summary"]["structural_minus_baseline"].items()},
      "receipt_retrieval":receipt_result["retrieval_metrics"],"scrambled_rejected":scrambled["passed_negative_control"],
      "watchdog_deltas":{k:v["mean_delta"] for k,v in watchdog_result["summary"]["structural_minus_baseline"].items()}},indent=2))

if __name__=="__main__": main()
