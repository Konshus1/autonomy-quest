"""Export what generalises. Import what others learned — but never as truth.

The whole divergent-evolution bet lives here: instances diverge, some of what they learn is about
how autonomous operation WORKS rather than about their own mission, and those bits transfer.

The danger lives here too. `scope = generalisable` is SELF-AUTHORED — the instance that wrote the
learning graded its own portability. Treat that flag as truth on the receiving side and one
instance's self-certification becomes another's executable truth, where it will be read as prior
knowledge and acted on forever.

So the rule, which the code enforces rather than merely states:

    A GENERALISABLE LABEL IS A PROPOSAL, NOT PROOF.
    Imports are STAGED and INERT. They reach no decision until this instance adjudicates them.

Sharing is opt-in, off by default, and nothing leaves the box unless a human runs `export`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def export(db, inst, path: str) -> dict:
    """Emit the learnings this instance believes would hold for a DIFFERENT mission — WITH the
    evidence, so the receiver can judge instead of trust."""
    rows = db.exportable_learnings()
    bundle = {
        "format": "autonomy-quest/learning-bundle/1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "origin_instance": os.environ.get("AQ_INSTANCE_NAME", "unnamed-instance"),
        "origin_mission": inst.mission.objective,
        "note": ("Each item is a PROPOSAL, not proof. The generalisable flag is self-authored by "
                 "the origin instance. Stage these, adjudicate them against YOUR mission, and "
                 "promote only what you can justify locally."),
        "learnings": [
            {
                "insight": r["insight"],
                "confidence": r["confidence"],
                "origin_run_id": r["run_id"],
                "origin_evidence": r["evidence"],
                "origin_outcome": r["origin_outcome"] or "",
                # These two are what make the claim ARGUABLE rather than merely assertable.
                "applies_when": "",     # filled by the exporting agent — when is this even relevant?
                "falsified_by": "",     # what observation would DISPROVE it? No falsifier, no claim.
            }
            for r in rows
        ],
    }
    with open(path, "w") as fh:
        json.dump(bundle, fh, indent=2)
    return bundle


def import_bundle(db, path: str) -> dict:
    """Receive a bundle. STAGE it. Nothing is adopted, nothing reaches the loop.

    A learning arriving from another instance is INERT here until this instance decides otherwise.
    It does not appear in live_learnings(), so it cannot influence a single decide() call.
    """
    with open(path) as fh:
        b = json.load(fh)
    if b.get("format") != "autonomy-quest/learning-bundle/1":
        raise ValueError(f"unknown bundle format: {b.get('format')!r}")

    staged, skipped = 0, 0
    for l in b["learnings"]:
        # A claim with no falsifier is not a claim, it is an opinion. We still stage it — the
        # human may see value — but we mark the gap honestly rather than papering over it.
        sid = db.stage_import(
            origin_instance=b["origin_instance"],
            origin_mission=b["origin_mission"],
            origin_run_id=l.get("origin_run_id"),
            origin_evidence=l.get("origin_evidence", ""),
            origin_outcome=l.get("origin_outcome", ""),
            insight=l["insight"],
            confidence=float(l.get("confidence", 0.5)),
            applies_when=l.get("applies_when") or "(the origin did not say — judge for yourself)",
            falsified_by=l.get("falsified_by") or "(NO FALSIFIER GIVEN — treat with suspicion)",
        )
        staged += 1 if sid else 0
        skipped += 0 if sid else 1

    return {
        "from": b["origin_instance"],
        "their_mission": b["origin_mission"],
        "staged": staged,
        "already_had": skipped,
        "status": ("STAGED and INERT. These influence NOTHING until you adjudicate them. "
                   "Review: ./aq.py share review"),
    }
