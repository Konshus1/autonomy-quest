"""Run the zero-network graph-analogy discrimination prototype."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ralph_portable.structural_analogy import (
    Relation,
    RetrievalSignature,
    Situation,
    compare_retrieval_regimes,
    retrieve_analogy,
)

HERE = Path(__file__).resolve().parent


def load_situation(row: dict) -> Situation:
    signature = row["signature"]
    return Situation(
        id=row["id"], domain=row["domain"], title=row["title"], summary=row["summary"],
        signature=RetrievalSignature(
            tuple(signature["goal"]), tuple(signature["constraints"]),
            tuple(signature["failed_predictions"]), signature["domain"],
        ),
        roles=row["roles"],
        observed_relations=tuple(Relation(**item) for item in row["observed_relations"]),
        transferable_relations=tuple(Relation(**item) for item in row.get("transferable_relations", [])),
    )


def run(corpus_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text())
    target = load_situation(corpus["target"])
    sources = [load_situation(row) for row in corpus["sources"]]
    result = retrieve_analogy(target, sources, shortlist_size=2)
    controls = compare_retrieval_regimes(target, sources, shortlist_size=2)
    return {
        "claim_ceiling": "one curated discrimination over given normalized relation graphs",
        "cost_usd": 0.0,
        "network_calls": 0,
        "age_enabled": False,
        "stage_1_shortlist": [
            {"source_id": row.situation.id, "domain": row.situation.domain,
             "cheap_score": row.cheap_score, "field_scores": row.field_scores}
            for row in result.shortlist
        ],
        "stage_2_candidates": [
            {"source_id": row.source.id, "accepted": row.accepted,
             "matched_relation_count": row.matched_relation_count,
             "largest_connected_component": row.largest_connected_component,
             "relational_depth": row.relational_depth,
             "systematicity_score": row.systematicity_score,
             "rejection_reason": row.rejection_reason}
            for row in result.structural_candidates
        ],
        "stage_2_selected": result.selected.source.id,
        "role_mapping": result.selected.role_mapping,
        "stage_3_plan_proposal": asdict(result.plan_proposal),
        "retrieval_controls": asdict(controls),
        "discrimination_passed": (
            controls.surface_source_id == "cybersecurity_malware_scanner"
            and controls.structural_source_id == "wildfire_firebreak"
            and result.selected.source.domain != target.domain
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=HERE / "corpus.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = run(args.corpus)
    text = json.dumps(receipt, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
