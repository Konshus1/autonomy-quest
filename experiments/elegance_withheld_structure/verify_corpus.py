#!/usr/bin/env python3
"""Discriminating M1 audit for the Elegance Under Withheld Structure corpus."""
from __future__ import annotations
import json, re, sys
from pathlib import Path

MIN_INCLUDED = 15
MIN_DOMAINS = 8
REQUIRED_INCLUDED = {
    "id", "status", "domain", "title", "requirement", "hidden_structure",
    "forbidden_cues", "admission_rationale", "human_analogy",
}
REQUIRED_EXCLUDED = {
    "id", "status", "domain", "title", "requirement", "hidden_structure",
    "forbidden_cues", "exclusion_reason", "leakage_quote",
}

def norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

def phrase_present(phrase: str, text: str) -> bool:
    # Token/phrase boundaries matter: cue "lease" must not match API word "release".
    return f" {norm(phrase)} " in f" {norm(text)} "

def audit(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return [f"corpus is not valid JSON: {exc}"]
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return ["candidates must be a list"]
    ids: set[str] = set()
    included = []
    excluded = []
    for i, item in enumerate(candidates):
        where = f"candidate[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = item.get("id", where)
        if cid in ids:
            errors.append(f"{cid}: duplicate id")
        ids.add(cid)
        status = item.get("status")
        required = REQUIRED_INCLUDED if status == "included" else REQUIRED_EXCLUDED if status == "excluded" else set()
        if not required:
            errors.append(f"{cid}: status must be included or excluded")
            continue
        missing = sorted(k for k in required if not item.get(k))
        if missing:
            errors.append(f"{cid}: missing non-empty fields: {', '.join(missing)}")
        requirement = norm(str(item.get("requirement", "")))
        cues = item.get("forbidden_cues", [])
        if not isinstance(cues, list) or not cues:
            errors.append(f"{cid}: forbidden_cues must be a non-empty list")
            cues = []
        hits = [cue for cue in cues if norm(str(cue)) and phrase_present(str(cue), requirement)]
        if status == "included":
            included.append(item)
            if hits:
                errors.append(f"{cid}: included prompt leaks forbidden mechanism cue(s): {', '.join(hits)}")
            rationale = str(item.get("admission_rationale", ""))
            if len(rationale.split()) < 10:
                errors.append(f"{cid}: admission rationale is too short to audit")
            analogy = str(item.get("human_analogy", ""))
            if len(analogy.split()) < 12:
                errors.append(f"{cid}: human analogy is too short for a role/relation map later")
            if not requirement.startswith("implement a python"):
                errors.append(f"{cid}: requirement must define a Python implementation task")
            if "standard library" not in requirement:
                errors.append(f"{cid}: dependency boundary is not stated")
        else:
            excluded.append(item)
            quote = norm(str(item.get("leakage_quote", "")))
            if not quote or not phrase_present(quote, requirement):
                errors.append(f"{cid}: leakage_quote is not verbatim present in excluded prompt")
            if not hits:
                errors.append(f"{cid}: excluded prompt has no detectable forbidden mechanism cue")
    if len(included) < max(MIN_INCLUDED, int(data.get("minimum_included", 0))):
        errors.append(f"only {len(included)} included problems; need at least {max(MIN_INCLUDED, int(data.get('minimum_included', 0)))}")
    domains = {str(item.get("domain")) for item in included}
    if len(domains) < MIN_DOMAINS:
        errors.append(f"only {len(domains)} included domains; need at least {MIN_DOMAINS}")
    if not excluded:
        errors.append("no excluded candidates preserve the rejection boundary")
    return errors

def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("corpus_candidates.json")
    errors = audit(path)
    if errors:
        print("FAIL: M1 corpus admission audit")
        for error in errors:
            print(f"- {error}")
        return 1
    data = json.loads(path.read_text())
    inc = [x for x in data["candidates"] if x["status"] == "included"]
    exc = [x for x in data["candidates"] if x["status"] == "excluded"]
    print("PASS: M1 corpus admission audit")
    print(f"included={len(inc)} excluded={len(exc)} domains={len({x['domain'] for x in inc})}")
    print("included_ids=" + ",".join(x["id"] for x in inc))
    print("excluded_ids=" + ",".join(x["id"] for x in exc))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
