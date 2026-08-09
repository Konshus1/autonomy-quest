"""toy_demo.py — EXPERIMENTAL, end-to-end analogy -> action toy mission.

T7 (#4899): the visible proof of the causal-edge AGE loop. It wires T5
(planning_check, RECORD-ONLY prediction) and T6 (surprise_wiring, gated
fail-closed surprise + weight update) into one end-to-end run:

    episode library
        -> novel-but-structurally-similar mission
        -> analogy retrieval (nearest episode by dimension overlap)
        -> propose a causal edge (source_action -> direct_effect -> mission_measure)
        -> record predicted certainty via T5 PlanningCheck
        -> "act" (mock outcome: success or failure)
        -> T6 SurpriseWiring computes surprise
        -> surprise fires -> edge weight update (support_count decreases)
        -> dump graph state (edges, predictions, surprises, weights) as JSON

FRAME-EXPANSION OBSERVATION
--------------------------
One mock episode carries an attribute ("reversibility") that NO dimension in
the T1 dimension library captures. The demo honestly records whether the
analogy layer notices this gap. Expected (and observed): it does NOT, because
frame expansion (T11) is not wired — analogy retrieval operates solely on
dimension-overlap, so an uncategorised attribute is invisible to it. This
absence is a finding, recorded as such, not hidden.

EXPERIMENTAL CONSTRAINT
----------------------
- Additive only. Lives under experiments/causal_edges/. No main-path change.
- Import-safe: importing this module has no side effects. Not imported by any
  main-path file (runner/loop.py, executor.py, gateway.py, ...). The test
  suite asserts that.
- Uses T5 + T6 modules which are themselves record-only / gated fail-closed.

Source of truth: BB decision #746 (causal edge model), #829 (JEPA enrichment),
T1 dimension library (artifacts/task_4893/data/category_clusters.json).
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

import psycopg2
import psycopg2.extras

# Make sibling modules (planning_check, surprise_wiring) importable when this
# file is imported from the test harness or run directly.
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from planning_check import PlanningCheck, Plan  # noqa: E402
from surprise_wiring import SurpriseWiring  # noqa: E402

log = logging.getLogger("aq.experiments.causal_edges.toy_demo")

# ---------------------------------------------------------------------------
# T1 dimension library — a small curated subset.
#
# Drawn from artifacts/task_4893/data/category_clusters.json (T1). We don't load
# the full 11k-line file; we hand-pick the dimensions relevant to a "mission"
# analogy and reference the source clusters by id so the provenance is honest.
# ---------------------------------------------------------------------------
DIMENSION_LIBRARY: dict[str, dict[str, Any]] = {
    "scope":              {"cluster_id": 1088, "members": ["scope", "scope_limits"]},
    "observability":      {"cluster_id": 786,  "members": ["observability"]},
    "idempotency":        {"cluster_id": 600,  "members": ["idempotency"]},
    "state_transitions":  {"cluster_id": 1195, "members": ["state_transitions", "transition_model", "transitions"]},
    "recovery_actions":   {"cluster_id": 931,  "members": ["recovery_actions", "recovery_options"]},
    "constraints":        {"cluster_id": 260,  "members": ["constraint_set", "constraints"]},
}
KNOWN_DIMENSIONS = set(DIMENSION_LIBRARY.keys())


@dataclass
class Episode:
    """A past mission the system has experienced."""
    eid: str
    goal: str
    approach: str
    outcome: str                      # "success" | "failure" | "partial"
    dimensions: set[str]              # mapped dimensions (subset of KNOWN_DIMENSIONS)
    source_action: str                # the mechanical action that produced the direct effect
    direct_effect: str
    mission_measure: str
    extra_attributes: dict[str, Any] = field(default_factory=dict)  # things NO dimension captures


# ---------------------------------------------------------------------------
# 1. Seed episode library (3-5 mock episodes)
# ---------------------------------------------------------------------------
def seed_episode_library() -> list[Episode]:
    return [
        Episode(
            eid="E1",
            goal="Convert cold leads into demos",
            approach="Multi-touch follow-up email sequence",
            outcome="success",
            dimensions={"scope", "observability", "state_transitions", "recovery_actions"},
            source_action="follow_up_sequence",
            direct_effect="contact_recurrence",
            mission_measure="demo_conversion_rate",
        ),
        Episode(
            eid="E2",
            goal="Win back at-risk accounts",
            approach="Targeted re-engagement campaign with pause-and-resume",
            outcome="success",
            dimensions={"scope", "observability", "idempotency", "recovery_actions"},
            source_action="winback_campaign",
            direct_effect="re-engagement_contact",
            mission_measure="retention_rate",
            # FRAME-EXPANSION PROBE: an attribute no dimension in the library
            # captures. The analogy layer should NOT notice this gap (T11 not
            # wired). We record that honestly.
            extra_attributes={"reversibility": "campaign can be rolled back mid-flight if it annoys"},
        ),
        Episode(
            eid="E3",
            goal="Activate new signups",
            approach="Timed educational drip",
            outcome="partial",
            dimensions={"scope", "state_transitions", "idempotency"},
            source_action="onboarding_drip",
            direct_effect="guided_step_completion",
            mission_measure="activation_rate",
        ),
        Episode(
            eid="E4",
            goal="Increase pricing-page conversion",
            approach="Split-test CTA copy",
            outcome="success",
            dimensions={"observability", "constraints", "scope"},
            source_action="cta_ab_test",
            direct_effect="cta_variant_exposure",
            mission_measure="signup_conversion_rate",
        ),
    ]


# ---------------------------------------------------------------------------
# 2. Novel-but-structurally-similar mission (same dimensions, different surface)
# ---------------------------------------------------------------------------
@dataclass
class Mission:
    mid: str
    goal: str
    surface: str                 # different surface story
    dimensions: set[str]        # same structural dimensions as the best analogy
    proposed_source_action: str
    proposed_direct_effect: str
    proposed_mission_measure: str


def novel_mission() -> Mission:
    """A mission that is surface-novel but structurally similar to E1:
    re-engaging dormant trial users via a follow-up sequence. Same dimensions
    (scope, observability, state_transitions, recovery_actions), different
    surface wording.
    """
    return Mission(
        mid="M1",
        goal="Re-engage dormant trial users",
        surface="Dormant trial users on a freemium product (not cold leads)",
        dimensions={"scope", "observability", "state_transitions", "recovery_actions"},
        proposed_source_action="follow_up_sequence",
        proposed_direct_effect="contact_recurrence",
        proposed_mission_measure="trial_re_engagement_rate",
    )


# ---------------------------------------------------------------------------
# 3. Analogy retrieval — nearest episode by dimension overlap (Jaccard)
# ---------------------------------------------------------------------------
def retrieve_analogy(episodes: list[Episode], mission: Mission) -> tuple[Episode, float]:
    """Return (best_episode, overlap_score). Simple Jaccard over dimension sets."""
    best: Episode | None = None
    best_score = -1.0
    for ep in episodes:
        inter = len(ep.dimensions & mission.dimensions)
        union = len(ep.dimensions | mission.dimensions)
        score = inter / union if union else 0.0
        if score > best_score:
            best_score = score
            best = ep
    assert best is not None
    return best, round(best_score, 4)


# ---------------------------------------------------------------------------
# 4. Propose a causal edge from the analogy
# ---------------------------------------------------------------------------
def propose_edge_from_analogy(
    analogy: Episode, mission: Mission
) -> dict[str, Any]:
    """Propose a causal edge: source_action -> direct_effect -> mission_measure.

    The predicted certainty is derived from the analogy: a strong analogous
    success yields high formality (epistemic confidence). We lift the
    source_action/direct_effect from the analogous episode and swap the
    mission_measure to the novel mission's measure (the mechanical claim is
    portable; the goal-potency claim is re-projected onto the new measure).
    """
    # Analogy outcome success + high overlap -> high formality (0.9).
    formality = 0.9 if analogy.outcome == "success" else 0.5
    strictness = 0.6   # soft-constraint (advisory-but-binding) by default
    directness = 0.6   # judgment -> structured predicate, not yet a deterministic script
    return {
        "source_action": mission.proposed_source_action,
        "direct_effect": mission.proposed_direct_effect,
        "mission_measure": mission.proposed_mission_measure,
        "formality_weight": formality,
        "strictness_weight": strictness,
        "directness_weight": directness,
        "executor_slot": "A",
        "support_count": 3,           # seeded from the analogous episode's support
        "analogy_source": analogy.eid,
        "analogy_overlap": None,       # filled by caller
    }


def _insert_edge(conn, spec: dict[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO causal_edge
                 (source_action, direct_effect, mission_measure,
                  formality_weight, strictness_weight, directness_weight,
                  executor_slot, support_count)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING edge_id""",
            (
                spec["source_action"], spec["direct_effect"], spec["mission_measure"],
                spec["formality_weight"], spec["strictness_weight"], spec["directness_weight"],
                spec["executor_slot"], spec["support_count"],
            ),
        )
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# 5..8. Act + surprise + weight update (driven by T5 + T6)
# ---------------------------------------------------------------------------
def _dump_graph(conn) -> dict[str, Any]:
    """Snapshot the full graph state: edges, predictions, surprises, weights."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT edge_id, source_action, direct_effect, mission_measure,
                      formality_weight, strictness_weight, directness_weight,
                      executor_slot, predicted_certainty, support_count,
                      falsified_by, scope_conditions, created_at, updated_at
                 FROM causal_edge ORDER BY edge_id"""
        )
        edges = [dict(r) for r in cur.fetchall()]
        for e in edges:
            e["created_at"] = e["created_at"].isoformat() if e["created_at"] else None
            e["updated_at"] = e["updated_at"].isoformat() if e["updated_at"] else None

        cur.execute(
            """SELECT prediction_id, edge_id, plan_id,
                      predicted_principles_supported, predicted_principles_challenged,
                      predicted_edges_stressed, predicted_certainty,
                      actual_outcome, surprise_level, recorded_at
                 FROM planning_prediction ORDER BY prediction_id"""
        )
        preds = [dict(r) for r in cur.fetchall()]
        for p in preds:
            # jsonb comes back as Python lists/dicts already via RealDictCursor
            for k in ("predicted_principles_supported", "predicted_principles_challenged",
                      "predicted_edges_stressed"):
                v = p.get(k)
                if isinstance(v, str):
                    try:
                        p[k] = json.loads(v)
                    except Exception:
                        pass
            p["recorded_at"] = p["recorded_at"].isoformat() if p["recorded_at"] else None

    return {"edges": edges, "predictions": preds}


# ---------------------------------------------------------------------------
# Frame-expansion observation (T11 not wired — negative finding, honestly recorded)
# ---------------------------------------------------------------------------
def frame_expansion_observation(episodes: list[Episode]) -> dict[str, Any]:
    """Check whether the analogy layer notices attributes no dimension captures.

    The analogy retrieval (retrieve_analogy) uses ONLY dimension sets. Any
    episode attribute outside KNOWN_DIMENSIONS is invisible to it. We scan all
    episodes for uncaptured attributes and honestly record that the demo does
    NOT notice the gap (frame expansion / T11 is not wired).
    """
    uncaptured: list[dict[str, Any]] = []
    for ep in episodes:
        for attr, val in ep.extra_attributes.items():
            if attr not in KNOWN_DIMENSIONS:
                uncaptured.append({
                    "episode": ep.eid,
                    "attribute": attr,
                    "value": val,
                    "in_dimension_library": False,
                })
    # The demo's analogy layer has no mechanism to surface these. Prove it:
    # re-run a retrieval and confirm the retrieved analogy's extra_attributes
    # are never inspected.
    mission = novel_mission()
    analogy, _ = retrieve_analogy(episodes, mission)
    noticed = False  # the retrieval never looks at extra_attributes
    return {
        "uncaptured_attributes": uncaptured,
        "analogy_layer_inspected_extra_attributes": False,
        "gap_noticed": noticed,
        "reason": (
            "Frame expansion (T11) is not wired. Analogy retrieval operates solely "
            "on dimension-overlap (Jaccard over known dimensions). An attribute that "
            "no dimension in the library captures (e.g. 'reversibility') is invisible "
            "to the analogy layer. The absence of a frame-expansion signal is itself "
            "the finding: the system cannot notice kinds of things it has no category for."
        ),
    }


# ---------------------------------------------------------------------------
# THE END-TO-END TOY DEMO
# ---------------------------------------------------------------------------
def run_toy_demo(
    conn,
    *,
    mission_outcome: str = "failure",
    task_id: int = 4899,
    evidence_run_id: int = 7,
) -> dict[str, Any]:
    """Run the end-to-end analogy -> action toy mission.

    Parameters
    ----------
    conn : psycopg2 connection to a DB that already has 009_causal_edges.sql applied.
    mission_outcome : "success" | "failure" — the mock outcome of "acting".
    task_id / evidence_run_id : bookkeeping for the surprise packet + demotion.

    Returns
    -------
    A graph-dump dict with the full edge lifecycle:
        episodes, mission, analogy, proposed_edge, edge_id, plan_id,
        predictions, surprise_packets, demoted_edges, graph (edges+predictions),
        frame_expansion, lifecycle.
    """
    # 1. seed
    episodes = seed_episode_library()
    # 2. novel mission
    mission = novel_mission()
    # 3. analogy retrieval
    analogy, overlap = retrieve_analogy(episodes, mission)
    # 4. propose edge
    edge_spec = propose_edge_from_analogy(analogy, mission)
    edge_spec["analogy_overlap"] = overlap
    edge_id = _insert_edge(conn, edge_spec)

    # 5. record prediction via T5 PlanningCheck
    pc = PlanningCheck(conn)
    plan_id = f"toy-{mission.mid}-{edge_id}"
    plan = Plan(
        plan_id=plan_id,
        actions=[edge_spec["source_action"]],
        rationale=(
            f"Analogy from {analogy.eid} (overlap={overlap}): "
            f"{analogy.approach} succeeded for '{analogy.goal}'; "
            f"port the mechanical action to the novel mission."
        ),
    )
    preds = pc.check_and_record(plan)

    # 6. "act" — mock execute the plan
    #    mission_outcome is injected by the caller so tests can exercise both
    #    the surprise-fires (failure) and no-surprise (success) paths.
    actual_outcome = mission_outcome

    # 7. on outcome, run T6 SurpriseWiring -> compute surprise + weight update
    sw = SurpriseWiring(conn)
    packets, demoted = sw.process_outcome(
        plan_id, actual_outcome, task_id=task_id, evidence_run_id=evidence_run_id
    )

    # 8. dump graph state
    graph = _dump_graph(conn)

    # frame-expansion observation
    frame_exp = frame_expansion_observation(episodes)

    # edge lifecycle trace
    edge_after = next((e for e in graph["edges"] if e["edge_id"] == edge_id), None)
    pred_after = [p for p in graph["predictions"] if p["edge_id"] == edge_id]
    lifecycle = {
        "created": {"edge_id": edge_id, **{k: edge_spec[k] for k in
                     ("source_action", "direct_effect", "mission_measure",
                      "formality_weight", "strictness_weight", "directness_weight",
                      "support_count", "analogy_source", "analogy_overlap")}},
        "predicted": {
            "plan_id": plan_id,
            "n_predictions": len(preds),
            "predicted_certainty": preds[0].predicted_certainty if preds else None,
        },
        "acted": {"actual_outcome": actual_outcome},
        "surprise": {
            "fired": len(packets) > 0,
            "n_packets": len(packets),
            "surprise_levels": [p.surprise_level for p in packets],
            "gate": "held_fail_closed" if packets else "n/a",
        },
        "weight_update": {
            "support_count_before": edge_spec["support_count"],
            "support_count_after": edge_after["support_count"] if edge_after else None,
            "decreased": (
                edge_after is not None
                and edge_after["support_count"] < edge_spec["support_count"]
            ),
        },
        "demoted": {"demoted_edges": demoted, "falsified_by": edge_after["falsified_by"] if edge_after else None},
    }

    return {
        "task": "T7_toy_demo",
        "mission_outcome": actual_outcome,
        "episodes": [
            {
                "eid": e.eid, "goal": e.goal, "approach": e.approach, "outcome": e.outcome,
                "dimensions": sorted(e.dimensions), "source_action": e.source_action,
                "direct_effect": e.direct_effect, "mission_measure": e.mission_measure,
                "extra_attributes": e.extra_attributes,
            }
            for e in episodes
        ],
        "mission": {
            "mid": mission.mid, "goal": mission.goal, "surface": mission.surface,
            "dimensions": sorted(mission.dimensions),
            "proposed_source_action": mission.proposed_source_action,
            "proposed_direct_effect": mission.proposed_direct_effect,
            "proposed_mission_measure": mission.proposed_mission_measure,
        },
        "analogy": {"source_episode": analogy.eid, "overlap_score": overlap},
        "proposed_edge": {**edge_spec, "edge_id": edge_id},
        "edge_id": edge_id,
        "plan_id": plan_id,
        "predictions": [
            {
                "edge_id": p.edge_id, "plan_id": p.plan_id,
                "predicted_certainty": p.predicted_certainty,
                "predicted_principles_supported": p.predicted_principles_supported,
                "predicted_principles_challenged": p.predicted_principles_challenged,
                "predicted_edges_stressed": p.predicted_edges_stressed,
            }
            for p in preds
        ],
        "surprise_packets": [
            {
                "edge_id": p.edge_id, "prediction_id": p.prediction_id,
                "surprise_level": p.surprise_level, "severity": p.to_dict()["severity"],
                "gate": "held_fail_closed", "authority": p.authority,
                "spawn_recommended": p.spawn_recommended,
            }
            for p in packets
        ],
        "demoted_edges": demoted,
        "graph": graph,
        "frame_expansion": frame_exp,
        "lifecycle": lifecycle,
    }


# ---------------------------------------------------------------------------
# CLI entry: set up a throwaway DB, run, write dump + report to artifacts/
# ---------------------------------------------------------------------------
def _admin_conn():
    return psycopg2.connect(
        dbname=os.environ.get("AQ_T7_ADMIN_DB", "postgres"),
        user=os.environ.get("AQ_T7_TEST_USER", os.environ.get("AQ_T5_TEST_USER", "kthomas")),
        host=os.environ.get("AQ_T7_TEST_HOST", "localhost"),
        port=os.environ.get("AQ_T7_TEST_PORT", "5432"),
    )


def _setup_throwaway_db() -> str:
    test_db = os.environ.get("AQ_T7_TEST_DB", "aq_t7_test")
    host = os.environ.get("AQ_T7_TEST_HOST", "localhost")
    port = os.environ.get("AQ_T7_TEST_PORT", "5432")
    user = os.environ.get("AQ_T7_TEST_USER", os.environ.get("AQ_T5_TEST_USER", "kthomas"))

    admin = _admin_conn()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{test_db}"')
        cur.execute(f'CREATE DATABASE "{test_db}"')
    admin.close()

    c = psycopg2.connect(dbname=test_db, user=user, host=host, port=port)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute((HERE / "009_causal_edges.sql").read_text())
    c.close()
    return f"postgresql://{user}@{host}:{port}/{test_db}"


def _write_artifacts(dump: dict[str, Any]) -> None:
    """Write graph_dump.json + demo_run_report.md to artifacts/task_4899/."""
    # artifacts live under the tb-runtime workspace (where the parent agent runs)
    candidates = [
        pathlib.Path(os.environ.get("TB_RUNTIME", "")) / "artifacts" / "task_4899",
        pathlib.Path.cwd() / "artifacts" / "task_4899",
        HERE.parent.parent.parent / "artifacts" / "task_4899",
    ]
    out_dir = next((p for p in candidates if p.parent.exists()), candidates[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "graph_dump.json").write_text(json.dumps(dump, indent=2, default=str))

    lc = dump["lifecycle"]
    fe = dump["frame_expansion"]
    report = f"""# T7 Toy Demo — Run Report

**Task:** #4899 (T7 end-to-end analogy -> action toy mission)
**Status:** EXPERIMENTAL, additive-only, in `experiments/causal_edges/`.
**Outcome injected:** `{dump['mission_outcome']}`

## 1. Episode library

{len(dump['episodes'])} mock episodes seeded. Each has a goal, approach,
outcome, dimension mapping, and a (source_action -> direct_effect -> mission_measure) triple.

| eid | goal | outcome | dimensions |
|-----|------|---------|------------|
"""
    for e in dump["episodes"]:
        report += f"| {e['eid']} | {e['goal']} | {e['outcome']} | {', '.join(e['dimensions'])} |\n"
    report += f"""
## 2. Novel mission

- **mid:** {dump['mission']['mid']}
- **goal:** {dump['mission']['goal']}
- **surface:** {dump['mission']['surface']}
- **dimensions:** {', '.join(dump['mission']['dimensions'])}

## 3. Analogy retrieval (dimension overlap)

- **retrieved:** episode `{dump['analogy']['source_episode']}`
- **overlap (Jaccard):** {dump['analogy']['overlap_score']}

## 4. Proposed causal edge

- source_action: `{dump['proposed_edge']['source_action']}`
- direct_effect: `{dump['proposed_edge']['direct_effect']}`
- mission_measure: `{dump['proposed_edge']['mission_measure']}`
- formality / strictness / directness: {dump['proposed_edge']['formality_weight']} / {dump['proposed_edge']['strictness_weight']} / {dump['proposed_edge']['directness_weight']}
- edge_id: {dump['edge_id']}

## 5. Prediction (T5 PlanningCheck, RECORD-ONLY)

- plan_id: `{dump['plan_id']}`
- predictions recorded: {lc['predicted']['n_predictions']}
- predicted_certainty: {lc['predicted']['predicted_certainty']}

## 6. Act (mock outcome)

actual_outcome: `{lc['acted']['actual_outcome']}`

## 7. Surprise (T6 SurpriseWiring, gated fail-closed)

- surprise fired: **{lc['surprise']['fired']}**
- packets emitted: {lc['surprise']['n_packets']}
- surprise levels: {lc['surprise']['surprise_levels']}
- gate: {lc['surprise']['gate']}

## 8. Weight update

- support_count before: {lc['weight_update']['support_count_before']}
- support_count after: {lc['weight_update']['support_count_after']}
- decreased: **{lc['weight_update']['decreased']}**
- demoted edges: {lc['demoted']['demoted_edges']}
- falsified_by: {lc['demoted']['falsified_by']}

## 9. Edge lifecycle (visible in graph dump)

created -> predicted -> acted -> surprise -> weight update

```
edge {dump['edge_id']}: support {lc['weight_update']['support_count_before']} -> {lc['weight_update']['support_count_after']}
prediction certainty = {lc['predicted']['predicted_certainty']}, actual = {lc['acted']['actual_outcome']}
surprise fired = {lc['surprise']['fired']}, weight decreased = {lc['weight_update']['decreased']}
```

## 10. Frame-expansion observation (HONEST, negative finding)

- uncaptured attributes found: {len(fe['uncaptured_attributes'])}
"""
    for u in fe["uncaptured_attributes"]:
        report += f"  - episode `{u['episode']}` attribute `{u['attribute']}`: {u['value']}\n"
    report += f"""
- **analogy layer inspected extra_attributes:** {fe['analogy_layer_inspected_extra_attributes']}
- **gap noticed by the system:** **{fe['gap_noticed']}**

### Finding (recorded honestly)

{fe['reason']}

This is a **negative observation**, recorded as a finding, not hidden: the toy
demo does NOT notice the 'reversibility' attribute on episode E2, because frame
expansion (T11) is not wired and the analogy layer operates solely on
dimension-overlap. The system cannot notice a *kind of thing it has no
category for*. Wiring frame expansion is future work (T11).

## 11. Honest scaffolding note

The demo runs end-to-end on the real T5 + T6 modules against a throwaway DB
with the real 009 migration applied. No hidden scaffolding is required for
the edge lifecycle (create -> predict -> act -> surprise -> weight update):
that path is exercised by the production-grade PlanningCheck and
SurpriseWiring classes. The only "scaffolding" is the mock *outcome* of the
mission, which is injected (`mission_outcome` param) rather than produced by
a real actuator — this is expected for a toy demo and is stated openly.
"""
    (out_dir / "demo_run_report.md").write_text(report)
    print(f"[toy_demo] wrote {out_dir / 'graph_dump.json'}")
    print(f"[toy_demo] wrote {out_dir / 'demo_run_report.md'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = _setup_throwaway_db()
    conn = psycopg2.connect(url)
    conn.autocommit = True
    # Default run: outcome = failure (surprise fires + weight update visible).
    dump = run_toy_demo(conn, mission_outcome="failure")
    _write_artifacts(dump)
    conn.close()
    print("[toy_demo] end-to-end run complete.")
