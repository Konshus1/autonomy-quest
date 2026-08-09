#!/usr/bin/env python3
"""Proposed blinded annotation runner for the preregistered distributional study.

No Codex call is made unless --mode review-a, review-b, or all is explicit.
Default output is outside the repository.  Typical later use:
  python elegance_annotation_runner.py --mode prepare
  python elegance_annotation_runner.py --mode review-a
  python elegance_annotation_runner.py --mode review-b
  python elegance_annotation_runner.py --mode consensus

Reviewer A and B are separate `codex exec --ephemeral` sessions. Failed
transport/schema attempts are replacements, not additional votes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

STUDY = Path("/Users/kevincthomas/src/aq-wt-elegance-codex-controls/experiments/elegance_withheld_structure/distributional_ablation")
DEFAULT_RUN = STUDY / "annotation_results"
REVIEWERS = ("A", "B")
MAX_PROMPT_BYTES = 120_000

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "pattern": "^sha256-[0-9a-f]{64}$"},
                    "verdict": {"type": "string", "enum": ["YES", "NO"]},
                    "source_relation": {
                        "type": "object",
                        "properties": {
                            "satisfied": {"type": "boolean"},
                            "citations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "symbol": {"type": "string", "minLength": 1},
                                        "line_start": {"type": "integer", "minimum": 1},
                                        "line_end": {"type": "integer", "minimum": 1},
                                        "quote": {"type": "string", "minLength": 1},
                                        "explanation": {"type": "string", "minLength": 1},
                                    },
                                    "required": ["symbol", "line_start", "line_end", "quote", "explanation"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["satisfied", "citations"],
                        "additionalProperties": False,
                    },
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item": {"type": "integer", "minimum": 1},
                                "satisfied": {"type": "boolean"},
                                "citations": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "symbol": {"type": "string", "minLength": 1},
                                            "line_start": {"type": "integer", "minimum": 1},
                                            "line_end": {"type": "integer", "minimum": 1},
                                            "quote": {"type": "string", "minLength": 1},
                                            "explanation": {"type": "string", "minLength": 1},
                                        },
                                        "required": ["symbol", "line_start", "line_end", "quote", "explanation"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["item", "satisfied", "citations"],
                            "additionalProperties": False,
                        },
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["label", "verdict", "source_relation", "evidence", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["reviews"],
    "additionalProperties": False,
}


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def expected_cells(plan: dict[str, Any]):
    for case in plan["cases"]:
        for condition in plan["conditions"]:
            for sample in range(1, plan["n_per_cell"] + 1):
                yield case, condition, sample


def contract_map(study: Path) -> dict[str, str]:
    p = study.parent / "full_run" / "frozen_inputs" / "task_contracts.json"
    return {x["id"]: x["contract"] for x in load_json(p)}


def complete_artifacts(study: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    """All 135 must exist before any labeling; no rolling annotation."""
    out = []
    missing = []
    for case, condition, sample in expected_cells(plan):
        d = study / "results" / case / condition / f"sample{sample:02d}"
        code_p, meta_p, response_p = d / "solution.py", d / "plan.json", d / "response.json"
        if not all(p.is_file() for p in (code_p, meta_p, response_p)):
            missing.append(f"{case}/{condition}/sample{sample:02d}")
            continue
        meta = load_json(meta_p)
        if not isinstance(meta.get("plan"), list) or not all(isinstance(x, str) for x in meta["plan"]):
            raise SystemExit(f"malformed generated plan at {meta_p}")
        # Deliberately exclude generated `mechanisms` and `trace` from reviewer material.
        out.append({
            "case": case, "condition": condition, "sample": sample,
            "source_dir": str(d), "code": code_p.read_text(encoding="utf-8"),
            "implementation_plan": list(meta["plan"]),
            "code_sha256": sha_text(code_p.read_text(encoding="utf-8")),
            "implementation_plan_sha256": sha_text(jdump(meta["plan"])),
            "response_sha256": hashlib.sha256(response_p.read_bytes()).hexdigest(),
        })
    if missing:
        preview = ", ".join(missing[:12])
        raise SystemExit(f"refusing partial annotation: {len(missing)} of {plan['total_generations']} incomplete ({preview})")
    if len(out) != plan["total_generations"]:
        raise SystemExit(f"expected {plan['total_generations']} complete artifacts, got {len(out)}")
    return out


def opaque_label(salt: bytes, case: str, condition: str, sample: int) -> str:
    cell = f"{case}\0{condition}\0{sample:02d}".encode()
    return "sha256-" + hashlib.sha256(salt + b"\0" + cell).hexdigest()


def mixed_batches(records: list[dict[str, Any]], cases: list[str], conditions: list[str]) -> dict[str, list[list[str]]]:
    """For each reviewer: one independently shuffled item/condition/batch.

    Thus every three-item batch contains default, matched, and irrelevant,
    while co-candidates and order differ across reviewers.
    """
    by = {(r["case"], r["condition"]): [] for r in records}
    for r in records:
        by[(r["case"], r["condition"])].append(r["label"])
    result: dict[str, list[list[str]]] = {}
    for reviewer in REVIEWERS:
        batches: list[list[str]] = []
        rng = secrets.SystemRandom()
        for case in cases:
            columns = []
            for condition in conditions:
                xs = list(by[(case, condition)])
                rng.shuffle(xs)
                columns.append(xs)
            if len({len(x) for x in columns}) != 1:
                raise SystemExit("unbalanced condition cells")
            # Three samples from each condition per batch: 9 candidates, 5 batches/case.
            for start in range(0, len(columns[0]), 3):
                batch = [label for column in columns for label in column[start:start + 3]]
                rng.shuffle(batch)
                batches.append(batch)
        rng.shuffle(batches)  # mixes case order too; each batch remains single-contract.
        result[reviewer] = batches
    return result


def numbered(code: str) -> str:
    return "\n".join(f"{i:05d} | {line}" for i, line in enumerate(code.splitlines(), 1))


def reviewer_prompt(case: str, labels: list[str], public_records: dict[str, Any], contract: str,
                    rubric: dict[str, Any]) -> str:
    candidates = []
    for label in labels:
        r = public_records[label]
        candidates.append(
            f"OPAQUE LABEL: {label}\n"
            f"IMPLEMENTATION PLAN (context only; never evidence):\n{jdump(r['implementation_plan']).rstrip()}\n"
            f"CODE (the only admissible evidence; left column is the citation line number):\n"
            f"```python\n{numbered(r['code'])}\n```"
        )
    evidence = "\n".join(f"  {i}. {x}" for i, x in enumerate(rubric["required_code_evidence"], 1))
    return f"""You are one of two independent, blinded code reviewers. Review EACH candidate independently.
You do not receive and must not infer an experimental condition, filesystem path, generation trace, or sample index. Do not compare candidates and do not use prevalence as evidence.

PUBLIC CONTRACT
{contract}

FROZEN MECHANISM RUBRIC
ID: {rubric['id']}
Definition: {rubric['definition']}
Frozen source relation/correspondence: {rubric['source_relation']}
Required code-evidence items (all required):
{evidence}

STRICT DECISION RULE
Return verdict YES only if the code itself, not its implementation plan, comments, naming, or self-report, concretely implements the full definition AND instantiates the frozen source relation/correspondence AND satisfies every numbered evidence item. The source_relation object and every evidence item must have satisfied=true and at least one concrete code citation. Cite a fully qualified class/method/function symbol where possible, an inclusive line range from the numbered code, an exact nonempty quote from that range, and an explanation of how executable structure satisfies that item. Similar words, aspirational plans, generated trace claims, inert IDs/keys, and isolated helpers do not count. If any part is absent, ambiguous, merely nominal, or cannot be cited, return NO; mark unsatisfied items false (citations may be empty). Do not repair or execute code. Output one review for every supplied opaque label, exactly once, and no other labels.

CANDIDATES

""" + "\n\n===== NEXT BLINDED CANDIDATE =====\n\n".join(candidates)


def prepare(study: Path, run: Path) -> None:
    if run.exists():
        raise SystemExit(f"refusing overwrite of existing run directory: {run}")
    plan_path = study / "plan.json"
    plan = load_json(plan_path)
    if not plan.get("frozen_before_generation") or plan.get("n_per_cell") != 15:
        raise SystemExit("unexpected/non-frozen preregistration")
    if plan.get("annotation", {}).get("reviewers") != 2:
        raise SystemExit("preregistered reviewer count is not two")
    contracts = contract_map(study)
    artifacts = complete_artifacts(study, plan)
    run.mkdir(parents=True, mode=0o700)
    (run / "private").mkdir(mode=0o700)
    (run / "prompts").mkdir(mode=0o700)
    (run / "audit").mkdir(mode=0o700)
    salt = secrets.token_bytes(32)
    for r in artifacts:
        r["label"] = opaque_label(salt, r["case"], r["condition"], r["sample"])
    if len({r["label"] for r in artifacts}) != len(artifacts):
        raise SystemExit("opaque-label collision")
    batches = mixed_batches(artifacts, plan["cases"], plan["conditions"])
    pub = {r["label"]: {"code": r["code"], "implementation_plan": r["implementation_plan"]} for r in artifacts}
    decode = {
        "preregistration_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "label_salt_hex": salt.hex(),
        "records": [{k: r[k] for k in ("label", "case", "condition", "sample", "source_dir", "code_sha256", "implementation_plan_sha256", "response_sha256")} for r in artifacts],
        "reviewer_batches": batches,
    }
    atomic_write(run / "private" / "decode_map.json", jdump(decode), 0o600)
    atomic_write(run / "response_schema.json", jdump(RESPONSE_SCHEMA), 0o600)
    manifest: dict[str, Any] = {
        "schema_version": 1, "preregistration_sha256": decode["preregistration_sha256"],
        "reviewers": {}, "response_schema_sha256": sha_text(jdump(RESPONSE_SCHEMA)),
    }
    for reviewer in REVIEWERS:
        items = []
        for i, labels in enumerate(batches[reviewer], 1):
            case_set = {next(r["case"] for r in artifacts if r["label"] == x) for x in labels}
            if len(case_set) != 1:
                raise SystemExit("batch crossed contracts")
            case = case_set.pop()
            prompt = reviewer_prompt(case, labels, pub, contracts[case], plan["mechanisms"][case])
            if len(prompt.encode()) > MAX_PROMPT_BYTES:
                raise SystemExit(f"batch prompt exceeds {MAX_PROMPT_BYTES} bytes")
            name = f"batch-{i:03d}"
            p = run / "prompts" / reviewer / f"{name}.txt"
            atomic_write(p, prompt, 0o600)
            items.append({"batch": name, "labels": labels, "case": case,
                          "prompt_file": str(p.relative_to(run)), "prompt_sha256": sha_text(prompt)})
        manifest["reviewers"][reviewer] = items
    atomic_write(run / "manifest.json", jdump(manifest), 0o600)
    print(f"prepared {len(artifacts)} frozen candidates and {sum(len(v) for v in batches.values())} blinded batches in {run}")


def structural_symbols(code: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    class Visitor(ast.NodeVisitor):
        stack: list[str] = []
        def visit_ClassDef(self, node):
            q = ".".join(self.stack + [node.name]); names.update((node.name, q)); self.stack.append(node.name); self.generic_visit(node); self.stack.pop()
        def visit_FunctionDef(self, node):
            q = ".".join(self.stack + [node.name]); names.update((node.name, q)); self.stack.append(node.name); self.generic_visit(node); self.stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
    Visitor().visit(tree)
    return names


def validate_response(obj: Any, labels: list[str], public_records: dict[str, Any], rubric_count: int) -> tuple[dict[str, Any], list[str]]:
    """Conservatively derive effective_yes; malformed evidence can only turn YES into NO."""
    errors: list[str] = []
    if not isinstance(obj, dict) or not isinstance(obj.get("reviews"), list):
        raise ValueError("response is not schema-shaped")
    reviews = obj["reviews"]
    got = [x.get("label") for x in reviews if isinstance(x, dict)]
    if len(got) != len(labels) or set(got) != set(labels) or len(set(got)) != len(got):
        raise ValueError("response labels are missing, duplicated, or extraneous")
    effective: dict[str, Any] = {}
    for review in reviews:
        label = review["label"]
        code = public_records[label]["code"]
        lines = code.splitlines()
        symbols = structural_symbols(code)
        ev = review.get("evidence", [])
        okay = review.get("verdict") == "YES"
        def cited_ok(item: Any) -> bool:
            item_ok = isinstance(item, dict) and item.get("satisfied") is True and bool(item.get("citations"))
            for c in item.get("citations", []) if isinstance(item, dict) else []:
                try:
                    a, b = c["line_start"], c["line_end"]
                    segment = "\n".join(lines[a-1:b])
                    quote = c["quote"].strip()
                    symbol = c["symbol"].strip()
                    if not (1 <= a <= b <= len(lines)) or quote not in segment or symbol not in symbols:
                        item_ok = False
                except Exception:
                    item_ok = False
            return item_ok
        if not cited_ok(review.get("source_relation")):
            okay = False; errors.append(f"{label}: frozen source relation lacks a valid structural citation")
        if len(ev) != rubric_count or {x.get("item") for x in ev if isinstance(x, dict)} != set(range(1, rubric_count + 1)):
            okay = False; errors.append(f"{label}: evidence items not exact")
        for item in ev:
            if not cited_ok(item):
                okay = False; errors.append(f"{label}: item {item.get('item')} lacks a valid structural citation")
        effective[label] = {"reported_verdict": review.get("verdict"), "effective_yes": bool(okay), "review": review}
    return effective, errors


def execute_reviewer(study: Path, run: Path, reviewer: str, retries: int) -> None:
    manifest = load_json(run / "manifest.json")
    decode = load_json(run / "private" / "decode_map.json")
    plan = load_json(study / "plan.json")
    if hashlib.sha256((study / "plan.json").read_bytes()).hexdigest() != manifest["preregistration_sha256"]:
        raise SystemExit("preregistration changed after prepare")
    # Load frozen code only after verifying source hashes; prompts themselves are immutable evidence snapshots.
    public_records = {}
    for r in decode["records"]:
        code = Path(r["source_dir"], "solution.py").read_text(encoding="utf-8")
        if sha_text(code) != r["code_sha256"]:
            raise SystemExit(f"source artifact changed after prepare: {r['label']}")
        public_records[r["label"]] = {"code": code}
    outdir = run / "audit" / f"reviewer_{reviewer}"
    outdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    schema_text = (run / "response_schema.json").read_text(encoding="utf-8")
    for entry in manifest["reviewers"][reviewer]:
        batch = entry["batch"]
        final = outdir / batch / "validated.json"
        if final.exists():
            continue
        prompt_path = run / entry["prompt_file"]
        prompt = prompt_path.read_text(encoding="utf-8")
        if sha_text(prompt) != entry["prompt_sha256"]:
            raise SystemExit(f"prompt changed: reviewer {reviewer} {batch}")
        bdir = outdir / batch
        bdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        success = False
        for attempt in range(1, retries + 2):
            adir = bdir / f"attempt-{attempt:02d}"
            adir.mkdir(mode=0o700)
            atomic_write(adir / "prompt.txt", prompt, 0o600)  # exact raw prompt
            with tempfile.TemporaryDirectory(prefix=f"annotation-review-{reviewer}-") as td:
                td = Path(td); schema_p = td / "schema.json"; last = td / "last.json"
                schema_p.write_text(schema_text, encoding="utf-8")
                cmd = ["codex", "exec", "--ephemeral", "--ignore-rules", "--skip-git-repo-check",
                       "-s", "read-only", "-C", str(td), "--output-schema", str(schema_p), "-o", str(last), prompt]
                try:
                    cp = subprocess.run(cmd, text=True, capture_output=True, timeout=900)
                    stdout, stderr, rc = cp.stdout, cp.stderr, cp.returncode
                except subprocess.TimeoutExpired as exc:
                    stdout, stderr, rc = exc.stdout or "", exc.stderr or str(exc), 124
                atomic_write(adir / "stdout.txt", stdout, 0o600)
                atomic_write(adir / "stderr.txt", stderr, 0o600)
                atomic_write(adir / "transport.json", jdump({"returncode": rc}), 0o600)
                if rc == 0 and last.is_file():
                    raw = last.read_text(encoding="utf-8")
                    atomic_write(adir / "response.raw.json", raw, 0o600)
                    try:
                        obj = json.loads(raw)
                        effective, errors = validate_response(obj, entry["labels"], public_records,
                                                              len(plan["mechanisms"][entry["case"]]["required_code_evidence"]))
                        atomic_write(final, jdump({"reviewer": reviewer, "batch": batch,
                                                  "prompt_sha256": entry["prompt_sha256"],
                                                  "effective": effective, "validation_notes": errors}), 0o600)
                        success = True
                        break
                    except Exception as exc:
                        atomic_write(adir / "validation_error.txt", repr(exc) + "\n", 0o600)
            time.sleep(5)
        if not success:
            raise SystemExit(f"reviewer {reviewer} batch {batch} exhausted schema/transport retries")
        print(f"reviewer {reviewer}: validated {batch}", flush=True)


def consensus(run: Path) -> None:
    manifest = load_json(run / "manifest.json")
    votes: dict[str, dict[str, bool]] = {}
    raw_refs: dict[str, dict[str, str]] = {}
    for reviewer in REVIEWERS:
        for entry in manifest["reviewers"][reviewer]:
            p = run / "audit" / f"reviewer_{reviewer}" / entry["batch"] / "validated.json"
            if not p.is_file():
                raise SystemExit(f"missing validated review: {p}")
            data = load_json(p)
            for label, value in data["effective"].items():
                votes.setdefault(label, {})[reviewer] = value["effective_yes"]
                raw_refs.setdefault(label, {})[reviewer] = str(p.relative_to(run))
    rows = []
    for label in sorted(votes):
        if set(votes[label]) != set(REVIEWERS):
            raise SystemExit(f"label lacks two independent reviews: {label}")
        rows.append({"label": label, "reviewer_effective_yes": votes[label],
                     "consensus_presence": all(votes[label].values()),
                     "rule": "YES iff both independently validated reviewers are YES; disagreement is NO",
                     "validated_review_files": raw_refs[label]})
    atomic_write(run / "consensus_blinded.json", jdump({"records": rows}), 0o600)
    print(f"wrote blinded consensus for {len(rows)} candidates; decode only in private/decode_map.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", type=Path, default=STUDY)
    ap.add_argument("--run-dir", type=Path, default=DEFAULT_RUN)
    ap.add_argument("--mode", choices=("prepare", "review-a", "review-b", "consensus", "all"), default="prepare")
    ap.add_argument("--transport-retries", type=int, default=2)
    args = ap.parse_args()
    if args.transport_retries < 0 or args.transport_retries > 5:
        raise SystemExit("transport retries must be 0..5")
    if args.mode in ("prepare", "all"):
        prepare(args.study.resolve(), args.run_dir.resolve())
    if args.mode in ("review-a", "all"):
        execute_reviewer(args.study.resolve(), args.run_dir.resolve(), "A", args.transport_retries)
    if args.mode in ("review-b", "all"):
        execute_reviewer(args.study.resolve(), args.run_dir.resolve(), "B", args.transport_retries)
    if args.mode in ("consensus", "all"):
        consensus(args.run_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
