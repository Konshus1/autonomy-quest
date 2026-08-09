#!/usr/bin/env python3
"""Completion verifier for the preregistered N=15 distributional ablation.

Recomputes frozen-input hashes, all artifact hashes, executable correctness,
blinded-review validity/consensus, and decoded analysis. It performs no model calls.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
EXPECTED_PLAN_SHA256 = "ae67fcab4bffbffd953fb0a0661adf5fcf5423dfe70999dd6c6f97a38bb8f340"
EXPECTED_CONTEXTS_SHA256 = "fa380201ff807c11b1228b4e23680270e7d526aa124afc79ff5f013748042f71"
REVIEWERS = ("A", "B")
SUFFIX = "\n\nReturn a concise implementation plan naming concrete organizing mechanisms, complete solution.py code, and source-relation to target-mechanism trace entries only if the supplied source directly caused the mechanism; otherwise use an empty trace. Correctness against the public contract is mandatory. Do not discuss the experiment or condition."


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def expected_prompt(contract: str, case: str, condition: str, contexts: dict[str, Any]) -> str:
    prompt = contract
    if condition != "default":
        key = "irrelevant" if condition == "irrelevant_worked" else condition
        prompt += "\n\nSOURCE ANALOGUE (a correct worked source-domain solution; transfer only relations that genuinely fit):\n" + json.dumps(contexts[case][key], indent=2)
    return prompt + SUFFIX


def opaque_label(salt: bytes, case: str, condition: str, sample: int) -> str:
    cell = f"{case}\0{condition}\0{sample:02d}".encode()
    return "sha256-" + hashlib.sha256(salt + b"\0" + cell).hexdigest()


def structural_symbols(code: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names

    class Visitor(ast.NodeVisitor):
        stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            q = ".".join(self.stack + [node.name])
            names.update((node.name, q))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            q = ".".join(self.stack + [node.name])
            names.update((node.name, q))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    Visitor().visit(tree)
    return names


def validate_review_response(obj: Any, labels: list[str], codes: dict[str, str], rubric_count: int) -> tuple[dict[str, Any], list[str]]:
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
        code = codes[label]
        lines = code.splitlines()
        symbols = structural_symbols(code)
        evidence = review.get("evidence", [])
        okay = review.get("verdict") == "YES"

        def cited_ok(item: Any) -> bool:
            item_ok = isinstance(item, dict) and item.get("satisfied") is True and bool(item.get("citations"))
            for citation in item.get("citations", []) if isinstance(item, dict) else []:
                try:
                    a, b = citation["line_start"], citation["line_end"]
                    if not (isinstance(a, int) and isinstance(b, int) and 1 <= a <= b <= len(lines)):
                        item_ok = False
                        continue
                    segment = "\n".join(lines[a - 1:b])
                    quote = citation["quote"].strip()
                    symbol = citation["symbol"].strip()
                    if quote not in segment or symbol not in symbols:
                        item_ok = False
                except Exception:
                    item_ok = False
            return item_ok

        if not cited_ok(review.get("source_relation")):
            okay = False
            errors.append(f"{label}: frozen source relation lacks a valid structural citation")
        if len(evidence) != rubric_count or {x.get("item") for x in evidence if isinstance(x, dict)} != set(range(1, rubric_count + 1)):
            okay = False
            errors.append(f"{label}: evidence items not exact")
        for item in evidence:
            if not cited_ok(item):
                okay = False
                errors.append(f"{label}: item {item.get('item')} lacks a valid structural citation")
        effective[label] = {"reported_verdict": review.get("verdict"), "effective_yes": bool(okay), "review": review}
    return effective, errors


def import_analyzer(study: Path):
    path = study / "analyze_distribution.py"
    spec = importlib.util.spec_from_file_location("distribution_analysis_for_verification", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import analyzer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(study: Path, run: Path, analysis_path: Path) -> list[str]:
    failures: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            failures.append(message)

    required_top = [study / "plan.json", study / "results" / "manifest.json", run / "private" / "decode_map.json", run / "manifest.json", run / "response_schema.json", run / "consensus_blinded.json", analysis_path]
    for path in required_top:
        check(path.is_file(), f"missing required file: {path}")
    if failures:
        return failures

    plan_path = study / "plan.json"
    plan = load(plan_path)
    plan_sha = sha_file(plan_path)
    check(plan_sha == EXPECTED_PLAN_SHA256, f"plan SHA mismatch: {plan_sha}")
    check(plan.get("frozen_before_generation") is True, "plan is not marked frozen_before_generation")
    check(plan.get("n_per_cell") == 15 and plan.get("total_generations") == 135, "plan N/total changed")
    check(plan.get("cases") == ["B13", "C03", "C20"], "plan cases changed")
    check(plan.get("conditions") == ["default", "matched", "irrelevant_worked"], "plan conditions changed")
    check(plan.get("annotation", {}).get("reviewers") == 2, "reviewer count changed")

    contexts_path = (study / plan["contexts_file"]).resolve()
    contexts_sha = sha_file(contexts_path) if contexts_path.is_file() else "MISSING"
    check(contexts_sha == EXPECTED_CONTEXTS_SHA256, f"contexts SHA mismatch: {contexts_sha}")
    check(plan.get("contexts_sha256") == EXPECTED_CONTEXTS_SHA256, "plan's contexts SHA field changed")
    contexts = load(contexts_path)

    contracts_path = study.parent / "full_run" / "frozen_inputs" / "task_contracts.json"
    contracts = {x["id"]: x["contract"] for x in load(contracts_path)}
    expected_cells = [
        (case, condition, sample)
        for case in plan["cases"]
        for condition in plan["conditions"]
        for sample in range(1, 16)
    ]
    expected_set = set(expected_cells)

    generation = load(study / "results" / "manifest.json")
    gen_records = generation.get("records", [])
    gen_keys = [(r.get("task"), r.get("condition"), r.get("sample")) for r in gen_records]
    check(len(gen_records) == 135 and len(set(gen_keys)) == 135 and set(gen_keys) == expected_set, "generation manifest coverage is not exactly 135 preregistered cells")
    gen_by = {key: record for key, record in zip(gen_keys, gen_records)}
    check(generation.get("engine") == plan.get("engine"), "generation engine differs from plan")

    decode = load(run / "private" / "decode_map.json")
    check(decode.get("preregistration_sha256") == EXPECTED_PLAN_SHA256, "decode map preregistration hash mismatch")
    try:
        salt = bytes.fromhex(decode["label_salt_hex"])
        check(len(salt) == 32, "opaque-label salt is not 32 bytes")
    except Exception:
        salt = b""
        check(False, "opaque-label salt is malformed")
    decode_records = decode.get("records", [])
    decode_keys = [(r.get("case"), r.get("condition"), r.get("sample")) for r in decode_records]
    labels = [r.get("label") for r in decode_records]
    check(len(decode_records) == 135 and len(set(decode_keys)) == 135 and set(decode_keys) == expected_set, "decode map coverage is not exactly 135 cells")
    check(len(set(labels)) == 135, "decode labels are not unique")
    decode_by_label = {r["label"]: r for r in decode_records if isinstance(r.get("label"), str)}

    codes: dict[str, str] = {}
    recomputed_test_rc: dict[tuple[str, str, int], int] = {}
    for case, condition, sample in expected_cells:
        cell = study / "results" / case / condition / f"sample{sample:02d}"
        key = (case, condition, sample)
        needed = ["prompt.txt", "prompt_sha256.txt", "response.json", "plan.json", "solution.py", "normalization.json", "cell.json", "test_stdout.txt", "test_stderr.txt"]
        for name in needed:
            check((cell / name).is_file(), f"{case}/{condition}/sample{sample:02d}: missing {name}")
        if any(not (cell / name).is_file() for name in needed):
            continue
        prompt = (cell / "prompt.txt").read_text(encoding="utf-8")
        expected = expected_prompt(contracts[case], case, condition, contexts)
        check(prompt == expected, f"{case}/{condition}/sample{sample:02d}: prompt differs from frozen condition construction")
        check((cell / "prompt_sha256.txt").read_text().strip() == sha_text(prompt), f"{case}/{condition}/sample{sample:02d}: prompt hash receipt mismatch")
        try:
            response = load(cell / "response.json")
            generated_plan = load(cell / "plan.json")
            normalization = load(cell / "normalization.json")
            code = response["code"]
            expected_norm: list[str] = []
            if code.endswith("\\n"):
                code = code[:-2]
                expected_norm.append("stripped_terminal_literal_backslash_n")
            normalized_code = code.rstrip() + "\n"
            check((cell / "solution.py").read_text(encoding="utf-8") == normalized_code, f"{case}/{condition}/sample{sample:02d}: solution does not reproduce from raw response")
            check(normalization == expected_norm, f"{case}/{condition}/sample{sample:02d}: normalization receipt mismatch")
            check(generated_plan == {k: response[k] for k in ("plan", "mechanisms", "trace")}, f"{case}/{condition}/sample{sample:02d}: plan/trace artifact differs from raw response")
        except Exception as exc:
            check(False, f"{case}/{condition}/sample{sample:02d}: malformed raw/normalized generation artifact: {exc}")
        dmatch = [r for r in decode_records if (r.get("case"), r.get("condition"), r.get("sample")) == key]
        if len(dmatch) == 1:
            dr = dmatch[0]
            expected_label = opaque_label(salt, case, condition, sample) if salt else ""
            check(dr.get("label") == expected_label, f"{case}/{condition}/sample{sample:02d}: opaque label does not derive from salt")
            check(dr.get("code_sha256") == sha_file(cell / "solution.py"), f"{case}/{condition}/sample{sample:02d}: frozen code hash mismatch")
            check(dr.get("response_sha256") == sha_file(cell / "response.json"), f"{case}/{condition}/sample{sample:02d}: frozen response hash mismatch")
            if (cell / "plan.json").is_file():
                implementation_plan = load(cell / "plan.json").get("plan")
                check(dr.get("implementation_plan_sha256") == sha_text(jdump(implementation_plan)), f"{case}/{condition}/sample{sample:02d}: frozen implementation-plan hash mismatch")
            codes[dr["label"]] = (cell / "solution.py").read_text(encoding="utf-8")
        manifest_row = gen_by.get(key)
        cell_row = load(cell / "cell.json")
        check(manifest_row == cell_row, f"{case}/{condition}/sample{sample:02d}: cell row differs from manifest")
        if manifest_row:
            check(manifest_row.get("generation_rc") == 0, f"{case}/{condition}/sample{sample:02d}: generation was not successful")
        test = study.parent / "full_run" / "tests" / f"test_{case.lower()}.py"
        cp = subprocess.run([sys.executable, str(test), str(cell / "solution.py")], text=True, capture_output=True)
        recomputed_test_rc[key] = cp.returncode
        if manifest_row:
            check(manifest_row.get("test_rc") == cp.returncode, f"{case}/{condition}/sample{sample:02d}: manifest test rc {manifest_row.get('test_rc')} != recomputed {cp.returncode}")

    annotation_manifest = load(run / "manifest.json")
    check(annotation_manifest.get("preregistration_sha256") == EXPECTED_PLAN_SHA256, "annotation manifest preregistration hash mismatch")
    response_schema_text = (run / "response_schema.json").read_text(encoding="utf-8")
    check(annotation_manifest.get("response_schema_sha256") == sha_text(response_schema_text), "review response-schema hash mismatch")
    batches_by_reviewer: dict[str, list[list[str]]] = {}
    recomputed_votes: dict[str, dict[str, bool]] = {label: {} for label in labels}
    raw_response_hashes: dict[str, set[str]] = {reviewer: set() for reviewer in REVIEWERS}
    for reviewer in REVIEWERS:
        entries = annotation_manifest.get("reviewers", {}).get(reviewer, [])
        check(len(entries) == 15, f"reviewer {reviewer}: expected 15 batches, got {len(entries)}")
        all_batch_labels = [label for entry in entries for label in entry.get("labels", [])]
        check(len(all_batch_labels) == 135 and set(all_batch_labels) == set(labels) and len(set(all_batch_labels)) == 135, f"reviewer {reviewer}: label coverage is not exactly once")
        batches_by_reviewer[reviewer] = [entry.get("labels", []) for entry in entries]
        for entry in entries:
            batch = entry.get("batch")
            batch_labels = entry.get("labels", [])
            cases = {decode_by_label[x]["case"] for x in batch_labels if x in decode_by_label}
            conditions = [decode_by_label[x]["condition"] for x in batch_labels if x in decode_by_label]
            check(len(batch_labels) == 9 and len(cases) == 1, f"reviewer {reviewer} {batch}: batch is not 9 same-contract candidates")
            check(all(conditions.count(c) == 3 for c in plan["conditions"]), f"reviewer {reviewer} {batch}: batch does not contain 3 per condition")
            prompt_path = run / entry.get("prompt_file", "")
            check(prompt_path.is_file(), f"reviewer {reviewer} {batch}: missing prompt")
            if prompt_path.is_file():
                prompt_text = prompt_path.read_text(encoding="utf-8")
                check(sha_text(prompt_text) == entry.get("prompt_sha256"), f"reviewer {reviewer} {batch}: prompt hash mismatch")
                for label in batch_labels:
                    check(prompt_text.count(f"OPAQUE LABEL: {label}\n") == 1, f"reviewer {reviewer} {batch}: label not exactly once in prompt")
                    code = codes.get(label)
                    if code is not None:
                        numbered = "\n".join(f"{i:05d} | {line}" for i, line in enumerate(code.splitlines(), 1))
                        check(numbered in prompt_text, f"reviewer {reviewer} {batch}: prompt code snapshot mismatch for {label}")
                for dr in decode_records:
                    source_dir = dr.get("source_dir", "")
                    if source_dir:
                        check(source_dir not in prompt_text, f"reviewer {reviewer} {batch}: leaked source path")
            validated_path = run / "audit" / f"reviewer_{reviewer}" / str(batch) / "validated.json"
            check(validated_path.is_file(), f"reviewer {reviewer} {batch}: missing validated review")
            if not validated_path.is_file() or len(cases) != 1:
                continue
            validated = load(validated_path)
            check(validated.get("reviewer") == reviewer and validated.get("batch") == batch, f"reviewer {reviewer} {batch}: validated metadata mismatch")
            check(validated.get("prompt_sha256") == entry.get("prompt_sha256"), f"reviewer {reviewer} {batch}: validated prompt hash mismatch")
            rubric_count = len(plan["mechanisms"][next(iter(cases))]["required_code_evidence"])
            matched_raw = False
            attempt_root = validated_path.parent
            for attempt in sorted(attempt_root.glob("attempt-*")):
                transport = attempt / "transport.json"
                raw = attempt / "response.raw.json"
                if not transport.is_file() or not raw.is_file():
                    continue
                if load(transport).get("returncode") != 0:
                    continue
                stderr = (attempt / "stderr.txt").read_text(encoding="utf-8", errors="replace") if (attempt / "stderr.txt").is_file() else ""
                check("model:" in stderr and "gpt-5.6-sol" in stderr and "provider:" in stderr and "openai" in stderr, f"reviewer {reviewer} {batch}: successful transport lacks gpt-5.6-sol/openai receipt")
                try:
                    raw_obj = load(raw)
                    effective, notes = validate_review_response(raw_obj, batch_labels, codes, rubric_count)
                except Exception:
                    continue
                if effective == validated.get("effective") and notes == validated.get("validation_notes"):
                    matched_raw = True
                    raw_response_hashes[reviewer].add(sha_file(raw))
                    for label, value in effective.items():
                        recomputed_votes[label][reviewer] = bool(value["effective_yes"])
                    break
            check(matched_raw, f"reviewer {reviewer} {batch}: validated review cannot be reproduced from a successful raw response")
    check(batches_by_reviewer.get("A") != batches_by_reviewer.get("B"), "reviewer batch assignments are identical, not independently mixed")
    check(raw_response_hashes.get("A", set()).isdisjoint(raw_response_hashes.get("B", set())), "reviewers share an identical raw response artifact")

    consensus = load(run / "consensus_blinded.json")
    consensus_records = consensus.get("records", [])
    consensus_labels = [r.get("label") for r in consensus_records]
    check(len(consensus_records) == 135 and set(consensus_labels) == set(labels) and len(set(consensus_labels)) == 135, "consensus label coverage is not exactly 135")
    consensus_by = {r["label"]: r for r in consensus_records if isinstance(r.get("label"), str)}
    for label in labels:
        votes = recomputed_votes.get(label, {})
        check(set(votes) == set(REVIEWERS), f"{label}: does not have two reproducible independent votes")
        row = consensus_by.get(label, {})
        check(row.get("reviewer_effective_yes") == votes, f"{label}: consensus vote record mismatch")
        check(row.get("consensus_presence") is all(votes.values()), f"{label}: consensus AND rule mismatch")

    try:
        analyzer = import_analyzer(study)
        expected_analysis = analyzer.analyze(study, run)
        recorded_analysis = load(analysis_path)
        check(recorded_analysis == expected_analysis, "analysis.json differs from recomputed decoded analysis")
    except Exception as exc:
        check(False, f"analysis recomputation failed: {exc}")

    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", type=Path, default=HERE)
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--analysis", type=Path)
    args = ap.parse_args()
    study = args.study.resolve()
    run = args.run_dir.resolve() if args.run_dir else study / "annotation_results"
    analysis_path = args.analysis.resolve() if args.analysis else study / "analysis.json"
    failures = verify(study, run, analysis_path)
    if failures:
        print("FAIL: distributional ablation completion gate")
        for failure in failures:
            print(f"- {failure}")
        return 1
    analysis = load(analysis_path)
    correct = analysis["generation_correctness"]["successes"]
    print("PASS: distributional ablation completion gate")
    print(f"cells=135 generations=135 correct={correct} reviewers=2 consensus=135")
    rates = {
        case: {condition: value["conditions"][condition]["mechanism_presence"]["exact"] for condition in plan_conditions()}
        for case, value in analysis["cases"].items()
    }
    print("mechanism_presence=" + json.dumps(rates, sort_keys=True))
    print(f"primary_outcome={analysis['primary_outcome']} qualifying_cases={json.dumps(analysis['qualifying_cases'])}")
    return 0


def plan_conditions() -> tuple[str, str, str]:
    return ("default", "matched", "irrelevant_worked")


if __name__ == "__main__":
    raise SystemExit(main())
