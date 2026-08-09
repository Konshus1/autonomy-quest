"""Ordered response to a localized frame gap (BB #2430 / #873 / #874).

Insufficiency is not an authorization failure.  It creates a knowledge-acquisition
step.  Analogy is deliberately a proposer: its output is never the target action.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class AcquisitionRung(StrEnum):
    RECALL = "recall"
    SEARCH = "search"
    BOUNDED_EXPERIMENT = "bounded_experiment"
    ANALOGY_PROPOSAL = "analogy_proposal"
    HUMAN = "human"


ACQUISITION_LADDER = (
    AcquisitionRung.RECALL,
    AcquisitionRung.SEARCH,
    AcquisitionRung.BOUNDED_EXPERIMENT,
    AcquisitionRung.ANALOGY_PROPOSAL,
    AcquisitionRung.HUMAN,
)


@dataclass(frozen=True)
class AcquisitionStep:
    target_step_id: str
    rung: AcquisitionRung | str
    rung_index: int
    action_step_id: str
    instruction: str
    proposer_only: bool = False


def next_acquisition_step(
    target_step_id: str,
    completed_rungs: Iterable[str | AcquisitionRung] = (),
    *,
    include_analogy: bool = True,
) -> AcquisitionStep | None:
    """Return the first uncompleted rung; None means the ladder is exhausted."""
    completed = {AcquisitionRung(value) for value in completed_rungs}
    ladder = ACQUISITION_LADDER if include_analogy else tuple(
        rung for rung in ACQUISITION_LADDER if rung != AcquisitionRung.ANALOGY_PROPOSAL
    )
    rung = next((candidate for candidate in ladder if candidate not in completed), None)
    if rung is None:
        return None
    index = ACQUISITION_LADDER.index(rung)
    instructions = {
        AcquisitionRung.RECALL: f"Recall traceable prior evidence governing plan step {target_step_id}; do not execute that target step.",
        AcquisitionRung.SEARCH: f"Search authoritative sources for the missing direction at plan step {target_step_id}; do not execute that target step.",
        AcquisitionRung.BOUNDED_EXPERIMENT: f"Design and run a reversible bounded experiment for the missing direction at plan step {target_step_id}; do not execute the target action broadly.",
        AcquisitionRung.ANALOGY_PROPOSAL: f"Propose one analogy candidate for plan step {target_step_id} for adjudication, bounded experiment, or human review. Do not act on the analogy candidate.",
        AcquisitionRung.HUMAN: f"Ask a human for the missing directional relation at plan step {target_step_id}; do not execute that target step.",
    }
    return AcquisitionStep(
        target_step_id=target_step_id,
        rung=rung,
        rung_index=index,
        action_step_id=f"acquire-{target_step_id}-{rung.value}",
        instruction=instructions[rung],
        proposer_only=rung == AcquisitionRung.ANALOGY_PROPOSAL,
    )


def acquisition_step_for_mode(target_step_id: str, mode: str, observation_index: int) -> AcquisitionStep:
    """Materialize a selected non-terminal meta-mode as a bounded acquisition action."""
    from .meta_mode import MetaMode
    selected = MetaMode(mode)
    instructions = {
        MetaMode.INTERNAL_COMPUTATION: (
            f"Perform one bounded internal computation pass for missing plan step {target_step_id}: "
            "retrieve, decompose, or simulate only enough to change the plan decision. Do not execute the target step."),
        MetaMode.ENVIRONMENT_EXPERIMENT: (
            f"Run one reversible tool/environment experiment for missing plan step {target_step_id} "
            "with an observable decision-relevant result. Do not execute the target action broadly."),
        MetaMode.HUMAN_QUESTION: (
            f"Ask one precise human question whose answer can change the decision at plan step {target_step_id}. "
            "Do not ask for approval when the gap is factual, and do not execute the target step."),
        MetaMode.HUMAN_DEMONSTRATION: (
            f"Request one bounded human demonstration for competence gap {target_step_id}, preserving an "
            "executable trace and transfer criterion. Do not execute the target step."),
        MetaMode.AUTONOMOUS_PRACTICE: (
            f"Practice the missing capability for plan step {target_step_id} in a sandbox with a holdout "
            "transfer test. Do not touch the live target."),
    }
    if selected not in instructions:
        raise ValueError(f"{selected.value} is a terminal meta-mode, not an acquisition action")
    return AcquisitionStep(target_step_id, selected, observation_index,
                           f"acquire-{target_step_id}-{observation_index}-{selected.value}",
                           instructions[selected], False)
