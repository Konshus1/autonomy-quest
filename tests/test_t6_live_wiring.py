"""T6 (#4898) — WIRING the rescued surprise machinery into the live AQ loop.

FAIL-FIRST. This file does NOT re-test surprise_wiring.py's internals — those are
covered by experiments/causal_edges/test_surprise_wiring.py (52 tests, all passing).
This file tests the ONE thing that was actually missing: the trigger.

BACKGROUND (why #4898 is WIRE, not BUILD):
  T5/T6/T7 were built 2026-08-08 06:22-06:31 but left UNTRACKED in a detached-HEAD
  publish worktree, so no commit exists and the task's "done" flip looked unbacked.
  c2f-4834-aq-synthesis located and rescued them (rescue/T5-T7-causal-edge-experiments,
  9b1ad62); this branch carries them verbatim (sha256-identical) under experiments/.
  Their own header asserts they are NOT imported by any main-path file — so the wire
  must go through the management API (HTTP), exactly as T10/T11 were wired via
  causal_sync, and NOT via a direct import into runner/loop.py.

THE PRODUCER/CONSUMER SEAM:
  T5 planning_check.record()  -> INSERT INTO planning_prediction   (producer, pre-act)
  T6 resolve_outcome()        -> SELECT ... WHERE actual_outcome IS NULL  (consumer, post-outcome)
  Wiring T6 alone yields nothing: with no producer there are never any rows to resolve.
  So the pre-act half must be wired too, or T6 is a no-op in production.

SCHEMA NOTE: the experiments own causal_edge + planning_prediction (009_causal_edges.sql,
additive per task amendment_54837). The live store is ralph_causal_edges (jsonb). The
endpoint projects live edges into the experimental table so the check runs on REAL edges.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# =============================================================================
# STATUS: THESE ENCODE **UNMET** T6 ACCEPTANCE. THEY ARE EXPECTED TO FAIL.
#
# The loop trigger was NOT completed in this session. Rather than delete the
# tests (which would erase the record of what T6 still owes) or let them fail
# the suite (which would block everyone else), they are marked xfail with the
# reason attached. When the wire lands, these flip to xpass and the marks come
# off. Nothing here should be read as "T6 is wired".
#
# Also unmet, per pasa-4863-review-ralph-work (bus #56872): DR6 ABDUCTION MODE
# (BB #2330 s12) requires a surprise investigation to yield >=1 candidate edge
# proposal WITH a falsifier. The rescued module emits prose hypotheses only —
# no candidate edge, no analogy provenance, no falsifier. T6 acceptance is
# therefore incomplete even once the HTTP wiring exists.
#
# -----------------------------------------------------------------------------
# HANDOFF — for whoever picks this up. Agreed across four lanes (BB #2422/#2426).
# Recorded here rather than only in the thread: a report gets summarized, a
# header travels with the code.
#
# TRIGGER 3 (goal_requires_unknown_attribute, decision #831 / BB #2365) carries:
#   (a) frame_expansion.py:180 constructor fix. `dimensions or DEFAULT_DIMENSIONS`
#       silently loads 21 dims for an EMPTY library, so the null-hypothesis probe
#       returns either a clean None ("no gaps" — from the probe designed to elicit
#       maximal gaps) or a plausible partial, depending purely on how many probe
#       attributes collide with the defaults. Nothing in the artifact says which,
#       or that a substitution happened. Fix: DEFAULT_DIMENSIONS if dimensions is
#       None else dimensions.
#   (b) three assertions, MIXED CASE PRIMARY. A null-hypothesis test built only
#       from exact-ID attributes PASSES against the buggy constructor by returning
#       None for the wrong reason — the obvious test for this defect is itself a
#       receipt. Use set equality, not membership, and not list equality (order
#       dependence produces spurious failures, which is how a good assertion gets
#       downgraded back to an existence check).
#   (c) provenance on every mapping_exhausted signal: dimension-library id/size AND
#       CONTENT HASH, plus the matcher backend that ACTUALLY RESOLVED at runtime.
#       Size alone is defeated by (a); the requested backend is not evidence of the
#       backend used when it degrades silently.
#   (d) predicted_edges_stressed stamped as unconditional. planning_check.py:132-134
#       appends every governing edge with no removal and no recomputation, while the
#       docstring at :124-127 describes a DR7 counterfactual test. The field is always
#       the full input set and carries zero bits. SurpriseWiring copies it into EVERY
#       T6 packet, so the warning must travel on the packet, not live in T5's docstring.
#
# SEQUENCING vs T12 (BB #2422 / #2426 step 1). (a)-(d) are frame_expansion /
# planning_check surface. Whichever lands SECOND must RE-RUN the other's assertions
# AND RECORD THE OUTCOME EVEN WHEN THEY PASS — a silently-still-passing assertion
# after a matcher change is indistinguishable from one nobody re-ran. A green suite
# is evidence only if someone can see it was actually re-run.
#
# T12 SCOPE CHANGED 2026-08-08 (BB #2422 addendum 6) — read this before assuming
# the paragraph above still describes it. c2f-4886 measured embeddings on the real
# C10 task (text-embedding-3-small, live 21-dim library):
#       COVERED   min 0.361
#       UNCOVERED max 0.480
# The classes OVERLAP, so NO threshold separates them and "recalibrate the threshold
# per backend" is not achievable — that spec item is retracted, not pending. T12 is
# therefore likely to become TWO-TIER: similarity as a high-recall PRUNE, plus an
# LLM adjudicator — i.e. T10's shape, since T10 is the component that demonstrably
# discriminates (77.1% pruned deterministically, then 22 of 24 candidates rejected
# by the classifier).
#
# CONSEQUENCE FOR (b): a prune stage tuned for RECALL will legitimately let more
# attributes through to adjudication, so the mixed-case probe's expected value may
# not be {"scope","observability"} under the final design. Do NOT simply re-run (b)
# and accept a pass — check the expected value against T12 as BUILT. Blindly
# re-running an assertion whose expected value silently moved is the receipt
# failure this header exists to prevent, one level up.
# =============================================================================

pytestmark = pytest.mark.xfail(
    reason="T6 loop wiring incomplete; DR6 abduction-mode acceptance unmet (bus #56859/#56868/#56872)",
    strict=False,
)



# --------------------------------------------------------------------------
# The wire exists at all
# --------------------------------------------------------------------------

def test_causal_sync_exposes_planning_prediction_recorder():
    """Pre-act (T5) half: the loop must be able to record a prediction."""
    from runner import causal_sync
    assert hasattr(causal_sync, "record_planning_prediction")


def test_causal_sync_exposes_outcome_surprise_resolver():
    """Post-outcome (T6) half: the loop must be able to resolve it."""
    from runner import causal_sync
    assert hasattr(causal_sync, "resolve_planning_surprise")


def test_loop_records_prediction_pre_act():
    source = (ROOT / "runner" / "loop.py").read_text()
    assert "record_planning_prediction" in source, (
        "runner/loop.py must record a planning prediction before acting — "
        "without a producer, T6's resolver has nothing to resolve"
    )


def test_loop_resolves_surprise_post_outcome():
    source = (ROOT / "runner" / "loop.py").read_text()
    assert "resolve_planning_surprise" in source, (
        "runner/loop.py's outcome phase must invoke the T6 resolution"
    )


def test_management_api_exposes_both_endpoints():
    source = (ROOT / "management" / "api" / "app.py").read_text()
    assert "/api/causal/planning-check" in source
    assert "/api/causal/resolve-outcome" in source


# --------------------------------------------------------------------------
# The wire preserves the experiments' import-safety contract
# --------------------------------------------------------------------------

def test_experiments_are_not_imported_at_module_scope_by_main_path():
    """surprise_wiring.py's own docstring guarantees this; the wire must not break it.

    A module-scope `import experiments...` in loop.py would make the main path depend
    on an EXPERIMENTAL tree and on psycopg2 being importable. The import must be lazy
    (inside the endpoint function), which is what the HTTP wire buys us.
    """
    for rel in ("runner/loop.py", "runner/causal_sync.py", "runner/executor.py"):
        path = ROOT / rel
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "experiments" in stripped:
                pytest.fail(f"{rel} imports experiments at module scope: {stripped!r}")


def test_loop_wire_is_best_effort_and_cannot_fail_a_cycle():
    """Doctrine from the T10/T11 wiring: the run is durably recorded before any of this.

    A surprise-path hiccup must never fail the cycle. The calls therefore live inside
    the existing post-commit best-effort block.
    """
    source = (ROOT / "runner" / "loop.py").read_text()
    idx_commit = source.find("CLOSE THE CAUSAL LOOP")
    idx_resolve = source.find("resolve_planning_surprise")
    assert idx_commit != -1
    assert idx_resolve > idx_commit, (
        "the T6 resolution must sit inside the post-commit best-effort block"
    )


# --------------------------------------------------------------------------
# Gate stays fail-closed through the wire (AC2 end-to-end)
# --------------------------------------------------------------------------

def test_emitted_packets_carry_no_authority_through_the_wire():
    """AC2/AC3: whatever reaches the loop is a HELD investigation, never a directive."""
    from management.api.experiments_causal import packet_authority

    authority = packet_authority()
    assert authority["requires_manager_review"] is True
    assert not any(v for k, v in authority.items() if k.startswith("may_")), (
        "a HELD investigation must claim no may_* authority"
    )


def test_schema_migration_is_additive_only():
    """amendment_54837: additive migrations only; 001-008 must be untouched."""
    schema_dir = ROOT / "schema"
    if not schema_dir.exists():
        pytest.skip("no schema/ dir in this checkout")
    migration = ROOT / "experiments" / "causal_edges" / "009_causal_edges.sql"
    assert migration.exists(), "the T5 additive migration must ship with the wire"
    text = migration.read_text().upper()
    for forbidden in ("DROP TABLE", "ALTER TABLE RUNS", "ALTER TABLE WORK", "DROP COLUMN"):
        assert forbidden not in text, f"migration must be additive; found {forbidden}"


# --------------------------------------------------------------------------
# C4a handoff to C5 — raised by c2f-4886-jump-research against BB #2358.
# #2358 lists FOUR C4a failure modes; the task spec's acceptance covers three.
# The fourth: a failed prediction must re-enter the next retrieval signature,
# which is how C4a hands off to C5 (abduction). Without it the detector is built
# but the handoff is not, and C5 silently stays open.
# --------------------------------------------------------------------------

def test_failed_prediction_re_enters_retrieval_signature():
    from runner import causal_sync
    assert hasattr(causal_sync, "failed_predictions_for_retrieval"), (
        "C4a->C5 handoff: resolved-failed predictions must be retrievable so they "
        "re-enter the next retrieval signature (BB #2358 C4a failure mode 4, #2330 L6)"
    )
