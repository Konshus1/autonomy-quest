from copy import deepcopy

from runner.close_loop.consultant import (
    CloseLoopSnapshot,
    recommend_close_the_loop,
)


def test_consultant_is_pure_and_recommends_verified_shadow_merge_first():
    raw = {
        "admitted_tasks": ({"source_task_id": "42"},),
        "verified_candidates": ({"source_task_id": "41", "candidate_sha": "a" * 40,
                                 "actuation_mode": "shadow"},),
        "active_work_ids": (),
    }
    before = deepcopy(raw)
    recommendation = recommend_close_the_loop(CloseLoopSnapshot(**raw))
    assert raw == before
    assert recommendation.source == "close_the_loop"
    assert recommendation.action.kind == "merge_verified_branch"
    assert recommendation.action.candidate_sha == "a" * 40
    assert recommendation.action.actuation_mode == "shadow"
    assert recommendation.requires_human is False
    assert recommendation.cost_estimate > 0


def test_consultant_pulls_only_without_active_work_and_otherwise_passes():
    pull = recommend_close_the_loop(CloseLoopSnapshot(
        admitted_tasks=({"source_task_id": "42"},), verified_candidates=(), active_work_ids=(),
    ))
    assert pull.action.kind == "pull_task" and pull.action.source_task_id == "42"
    busy = recommend_close_the_loop(CloseLoopSnapshot(
        admitted_tasks=({"source_task_id": "42"},), verified_candidates=(), active_work_ids=(7,),
    ))
    assert busy.action.kind == "none"


def test_public_candidate_is_human_gated_and_never_armed_by_consultant():
    recommendation = recommend_close_the_loop(CloseLoopSnapshot(
        admitted_tasks=(), verified_candidates=({"source_task_id": "42", "candidate_sha": "b" * 40,
                                                   "actuation_mode": "public"},), active_work_ids=(),
    ))
    assert recommendation.action.kind == "merge_verified_branch"
    assert recommendation.requires_human is True
    assert recommendation.action.actuation_mode == "public"
