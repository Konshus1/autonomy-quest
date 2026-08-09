"""Offline Track-C experiment: explicit structural analogy vs two baselines.

This module is deliberately unwired.  It never imports the AQ loop, mutates the
mission database, or promotes a principle.  Network calls occur only when this
file is invoked as a CLI with DEEPSEEK_API_KEY set.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ARMS = ("direct", "semantic", "structural")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def load_corpus(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if not data.get("analogy_kb") or not data.get("problems"):
        raise ValueError("corpus requires non-empty analogy_kb and problems")
    return data


def _relation(row: list[str]) -> dict[str, str]:
    if len(row) != 4:
        raise ValueError(f"relation must have [subject,predicate,object,family], got {row!r}")
    return dict(zip(("subject", "predicate", "object", "family"), row))


def structural_match(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any] | None:
    """Find the best topology-preserving role map for observed relation families.

    Transfer relations are excluded from scoring.  They are used only *after*
    retrieval, preventing the target solution from choosing its own source.
    """
    source_rel = [_relation(r) for r in source["observed_relations"]]
    target_rel = [_relation(r) for r in target["observed_relations"]]
    source_role_ids = sorted({r[k] for r in source_rel for k in ("subject", "object")})
    target_role_ids = sorted({r[k] for r in target_rel for k in ("subject", "object")})
    if len(source_role_ids) > len(target_role_ids):
        return None
    best: tuple[int, tuple[str, ...], dict[str, str], list[dict[str, Any]]] | None = None
    for assigned in itertools.permutations(target_role_ids, len(source_role_ids)):
        mapping = dict(zip(source_role_ids, assigned))
        matched: list[dict[str, Any]] = []
        used_target: set[int] = set()
        for sr in source_rel:
            for idx, tr in enumerate(target_rel):
                if idx in used_target:
                    continue
                if (sr["family"] == tr["family"] and
                    mapping[sr["subject"]] == tr["subject"] and
                    mapping[sr["object"]] == tr["object"]):
                    used_target.add(idx)
                    matched.append({"source": sr, "target": tr})
                    break
        candidate = (len(matched), assigned, mapping, matched)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None or best[0] == 0:
        return None
    count, _, mapping, matched = best
    return {
        "score": count / max(len(source_rel), len(target_rel)),
        "matched_relation_count": count,
        "role_id_mapping": mapping,
        "relation_correspondences": matched,
    }


def retrieve_structural(kb: list[dict[str, Any]], target: dict[str, Any], *, require_cross_domain: bool) -> dict[str, Any]:
    ranked: list[tuple[float, int, str, dict[str, Any], dict[str, Any]]] = []
    for source in kb:
        if require_cross_domain and source["domain"] == target["domain"]:
            continue
        match = structural_match(source, target)
        if match:
            ranked.append((match["score"], match["matched_relation_count"], source["id"], source, match))
    if not ranked:
        raise ValueError(f"no structural match for {target['id']}")
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    _, _, _, source, match = ranked[0]
    mapping = match["role_id_mapping"]
    role_correspondences = [
        {
            "source_role": sid,
            "source_description": source["roles"][sid],
            "target_role": tid,
            "target_description": target["roles"][tid],
        }
        for sid, tid in sorted(mapping.items())
    ]
    inferences = []
    for raw in source.get("transfer_relations", []):
        rel = _relation(raw)
        def endpoint(role: str) -> dict[str, str]:
            if role in mapping:
                tid = mapping[role]
                return {"kind": "mapped", "role": tid, "description": target["roles"][tid]}
            return {"kind": "new_analogue", "role": f"NEW_ANALOGUE_OF_{role}", "description": source["roles"][role]}
        inferences.append({
            "source_relation": rel,
            "target_subject": endpoint(rel["subject"]),
            "relation_family": rel["family"],
            "target_object": endpoint(rel["object"]),
        })
    return {
        "source_id": source["id"],
        "source_domain": source["domain"],
        "target_domain": target["domain"],
        "score": match["score"],
        "role_correspondences": role_correspondences,
        "relation_correspondences": match["relation_correspondences"],
        "transferred_candidate_inferences": inferences,
        "source": source,
    }


class DeepSeekClient:
    def __init__(self, model: str = "deepseek-v4-flash", *, timeout: int = 120, max_retries: int = 4):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
        self.key = os.environ.get("DEEPSEEK_API_KEY")
        if not self.key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for the experiment CLI")

    def json_call(self, system: str, user: str, *, temperature: float, max_tokens: int = 900) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        body = _canonical(payload).encode()
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            request = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"
            })
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    envelope = json.loads(response.read())
                content = envelope["choices"][0]["message"]["content"]
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    start, end = content.find("{"), content.rfind("}")
                    if start < 0 or end <= start:
                        raise
                    parsed = json.loads(content[start:end + 1])
                return {
                    "parsed": parsed,
                    "raw_content": content,
                    "usage": envelope.get("usage", {}),
                    "request_sha256": hashlib.sha256(body).hexdigest(),
                    "response_id": envelope.get("id"),
                }
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"model call failed after {self.max_retries} attempts: {last_error}")


def _candidate_prompt(problem: dict[str, Any], arm: str, kb: list[dict[str, Any]], retrieval: dict[str, Any] | None) -> tuple[str, str]:
    system = ("You generate bounded candidate actions for an autonomous-system research problem. "
              "Return JSON only with keys candidate_text (a self-contained answer, <=180 words) and ideas "
              "(array of 1-3 concise candidate actions). Do not mention experimental arms or scoring.")
    base = f"Problem:\n{problem['problem']}\n"
    if arm == "direct":
        return system, base + "Propose up to three useful candidate actions directly."
    if arm == "semantic":
        summaries = [{"id": x["id"], "title": x["title"], "summary": x["summary"]} for x in kb]
        return system, base + ("\nBelow is a case library. Select the case most similar in topic, wording, or task meaning; "
                               "do not perform or describe role/relation mapping. Use it as retrieval context, then propose candidates. "
                               "Also return selected_source_id.\nCASES:\n" + _canonical(summaries))
    assert retrieval is not None
    transfer_view = {
        "source_title": retrieval["source"]["title"],
        "source_summary": retrieval["source"]["summary"],
        "role_correspondences": retrieval["role_correspondences"],
        "relation_correspondences": retrieval["relation_correspondences"],
        "candidate_inferences": retrieval["transferred_candidate_inferences"],
    }
    return system, base + ("\nUse this already-computed cross-domain structural mapping. Instantiate at least one candidate inference "
                           "in the target domain. Keep mapping rationale out of candidate_text; return used_inference_index.\nMAPPING:\n" + _canonical(transfer_view))


def _extract_candidate(call: dict[str, Any]) -> dict[str, Any]:
    p = call["parsed"]
    text = p.get("candidate_text") or p.get("answer")
    if not isinstance(text, str) or not text.strip():
        ideas = p.get("ideas")
        if isinstance(ideas, list) and ideas:
            text = "\n".join(f"- {str(x)}" for x in ideas)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"model response lacks candidate_text: {p!r}")
    return {"candidate_text": text.strip(), "model_json": p, "call": {k: call[k] for k in ("raw_content", "usage", "request_sha256", "response_id")}}


def _judge(client: DeepSeekClient, problem: dict[str, Any], candidates: dict[str, dict[str, Any]], repeat: int) -> dict[str, Any]:
    seed = int(hashlib.sha256(f"{problem['id']}:{repeat}".encode()).hexdigest()[:16], 16)
    order = list(ARMS)
    random.Random(seed).shuffle(order)
    labels = {arm: f"C{idx+1}" for idx, arm in enumerate(order)}
    blinded = [{"candidate_id": labels[arm], "candidate_text": candidates[arm]["candidate_text"]} for arm in order]
    system = ("You are a strict usefulness judge. Evaluate proposed actions without guessing their generation method. "
              "Return JSON only: scores, an array containing exactly one object per candidate with candidate_id, "
              "usefulness, actionability, novelty (integer 1-5 each), and a short reason. "
              "Usefulness anchors: 1 harmful/irrelevant; 2 mostly unusable; 3 plausible but generic; "
              "4 useful and problem-specific; 5 unusually useful, specific, and likely to improve the search for an action.")
    user = "Problem:\n" + problem["problem"] + "\nCandidates (order is randomized):\n" + _canonical(blinded)
    call = client.json_call(system, user, temperature=0.0, max_tokens=1800)
    rows = call["parsed"].get("scores")
    if not isinstance(rows, list):
        raise ValueError(f"judge response lacks scores array: {call['parsed']!r}")
    by_label = {str(x.get("candidate_id")): x for x in rows}
    scores: dict[str, Any] = {}
    for arm in ARMS:
        row = by_label.get(labels[arm])
        if not row:
            raise ValueError(f"judge omitted {labels[arm]}: {rows!r}")
        clean = {"reason": str(row.get("reason", ""))}
        for metric in ("usefulness", "actionability", "novelty"):
            value = int(row[metric])
            if not 1 <= value <= 5:
                raise ValueError(f"judge {metric} outside 1..5: {value}")
            clean[metric] = value
        scores[arm] = clean
    return {"repeat": repeat, "labels": labels, "scores": scores,
            "call": {k: call[k] for k in ("raw_content", "usage", "request_sha256", "response_id")}}


def paired_stats(values_a: list[float], values_b: list[float], *, seed: int = 7403) -> dict[str, Any]:
    if len(values_a) != len(values_b) or not values_a:
        raise ValueError("paired_stats requires equal non-empty vectors")
    diffs = [a - b for a, b in zip(values_a, values_b)]
    observed = statistics.mean(diffs)
    rng = random.Random(seed)
    boot = [statistics.mean([rng.choice(diffs) for _ in diffs]) for _ in range(10000)]
    boot.sort()
    lower = boot[int(0.025 * len(boot))]
    upper = boot[int(0.975 * len(boot)) - 1]
    if len(diffs) <= 20:
        means = [statistics.mean([d * sign for d, sign in zip(diffs, signs)])
                 for signs in itertools.product((-1, 1), repeat=len(diffs))]
    else:
        means = [statistics.mean([d * rng.choice((-1, 1)) for d in diffs]) for _ in range(100000)]
    p_one_sided = (sum(x >= observed - 1e-12 for x in means) + 1) / (len(means) + 1)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else 0.0
    return {
        "n_problems": len(diffs), "mean_delta": round(observed, 6),
        "median_delta": round(statistics.median(diffs), 6),
        "bootstrap_95_ci": [round(lower, 6), round(upper, 6)],
        "paired_effect_dz": round(observed / sd, 6) if sd else None,
        "one_sided_sign_flip_p": round(p_one_sided, 8), "per_problem_deltas": diffs,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    vectors = {arm: [] for arm in ARMS}
    for rec in records:
        for arm in ARMS:
            vectors[arm].append(statistics.mean(j["scores"][arm]["usefulness"] for j in rec["judgments"]))
    arm_summary = {arm: {"mean_usefulness": round(statistics.mean(vals), 6),
                         "median_usefulness": round(statistics.median(vals), 6), "per_problem": vals}
                   for arm, vals in vectors.items()}
    comps = {base: paired_stats(vectors["structural"], vectors[base], seed=7403 + idx)
             for idx, base in enumerate(("direct", "semantic"))}
    # Holm correction for the two preregistered comparisons.
    ordered = sorted((v["one_sided_sign_flip_p"], k) for k, v in comps.items())
    adjusted: dict[str, float] = {}
    running = 0.0
    m = len(ordered)
    for rank, (p, key) in enumerate(ordered):
        running = max(running, min(1.0, p * (m - rank)))
        adjusted[key] = running
    for key, value in comps.items():
        value["holm_adjusted_p"] = round(adjusted[key], 8)
        value["significant_positive_gain"] = bool(value["mean_delta"] > 0 and value["bootstrap_95_ci"][0] > 0 and adjusted[key] < 0.05)
    return {"arms": arm_summary, "structural_minus_baseline": comps}


def run_corpus(corpus_path: str | Path, *, model: str, judge_repeats: int, require_cross_domain: bool,
               checkpoint_path: str | Path | None = None) -> dict[str, Any]:
    corpus = load_corpus(corpus_path)
    client = DeepSeekClient(model)
    completed: dict[str, Any] = {}
    checkpoint = Path(checkpoint_path) if checkpoint_path else None
    if checkpoint and checkpoint.exists():
        prior = json.loads(checkpoint.read_text())
        completed = {r["problem_id"]: r for r in prior.get("records", [])}
    for problem in corpus["problems"]:
        if problem["id"] in completed:
            continue
        retrieval = retrieve_structural(corpus["analogy_kb"], problem, require_cross_domain=require_cross_domain)
        candidates = {}
        for arm in ARMS:
            system, user = _candidate_prompt(problem, arm, corpus["analogy_kb"], retrieval if arm == "structural" else None)
            candidates[arm] = _extract_candidate(client.json_call(system, user, temperature=0.35, max_tokens=1800))
        judgments = [_judge(client, problem, candidates, repeat) for repeat in range(judge_repeats)]
        completed[problem["id"]] = {
            "problem_id": problem["id"], "domain": problem["domain"], "problem": problem["problem"],
            "retrieval": {k: v for k, v in retrieval.items() if k != "source"},
            "candidates": candidates, "judgments": judgments,
        }
        if checkpoint:
            checkpoint.write_text(json.dumps({"records": list(completed.values())}, indent=2) + "\n")
    records = [completed[p["id"]] for p in corpus["problems"]]
    return {
        "corpus_path": str(Path(corpus_path).name), "corpus_sha256": sha256_json(corpus),
        "require_cross_domain": require_cross_domain, "model": model, "judge_model": model,
        "judge_repeats": judge_repeats, "records": records, "summary": summarize(records),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--judge-repeats", type=int, default=3)
    args = parser.parse_args()
    output = Path(args.output)
    main_checkpoint = output.with_suffix(".cross.checkpoint.json")
    watchdog_checkpoint = output.with_suffix(".watchdog.checkpoint.json")
    cross = run_corpus(HERE / "cross_domain_cases.json", model=args.model, judge_repeats=args.judge_repeats,
                       require_cross_domain=True, checkpoint_path=main_checkpoint)
    watchdog = run_corpus(HERE / "watchdog_cases.json", model=args.model, judge_repeats=args.judge_repeats,
                          require_cross_domain=False, checkpoint_path=watchdog_checkpoint)
    watchdog_invalid = any(c["significant_positive_gain"] for c in watchdog["summary"]["structural_minus_baseline"].values())
    cross_positive = all(c["significant_positive_gain"] for c in cross["summary"]["structural_minus_baseline"].values())
    conclusion = "measurement_invalid_watchdog_gain" if watchdog_invalid else ("positive" if cross_positive else "measured_null_or_negative")
    result = {
        "experiment": "track_c_structural_analogy_search", "schema_version": 1,
        "created_at_epoch": int(time.time()), "conclusion": conclusion,
        "estimand": "incremental usefulness of the explicit structural-analogy pipeline; direct ideation may use latent analogy internally",
        "cross_domain": cross, "watchdog_control": watchdog,
        "limitations": [
            "Curated small corpora; problem is the independent unit and N=8 per corpus.",
            "The same DeepSeek V4 Flash family generated and judged candidates; candidates were blinded and order-randomized, but judge-family bias remains.",
            "Target relation graphs are human-authored, so this measures retrieval/mapping/transfer given a structural representation, not automatic schema extraction.",
            "A non-significant watchdog result is not an equivalence proof; it only satisfies the preregistered no-significant-gain invalidation gate.",
            "Semantic retrieval is a same-model semantic case selector in one call, not a frozen embedding index; the model could perform latent structural reasoning.",
        ],
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(output), "conclusion": conclusion,
                      "cross_deltas": {k:v["mean_delta"] for k,v in cross["summary"]["structural_minus_baseline"].items()},
                      "watchdog_deltas": {k:v["mean_delta"] for k,v in watchdog["summary"]["structural_minus_baseline"].items()}}, indent=2))


if __name__ == "__main__":
    main()
