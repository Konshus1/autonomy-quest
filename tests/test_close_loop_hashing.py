from copy import deepcopy

import pytest

from runner.close_loop.hashing import (
    IntentContractError,
    canonical_digest,
    canonical_json,
    source_hashes,
    task_intent_hash,
    mission_boundary_hash,
)


def task():
    return {
        "id": 42,
        "title": "Bounded fix",
        "description": "Fix one thing",
        "parent_task_id": None,
        "status": "pending",
        "updated_at": "now",
        "details": {"aq_phase1": {"intent": {
            "repo_id": "aq",
            "target_ref": "refs/heads/main",
            "scope": ["runner/close_loop"],
            "definition_of_done": "tests pass",
            "stop_condition": "one bounded patch",
            "verifier_manifest": "trusted-v1",
            "authority": "local-candidate-only",
        }}},
    }


def test_canonical_hash_is_key_order_stable_and_domain_separated():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert canonical_digest("one", {"a": 1}) != canonical_digest("two", {"a": 1})


def test_mutable_orchestration_changes_observation_not_intent_hash():
    before = task()
    after = deepcopy(before)
    after["status"] = "in_progress"
    after["updated_at"] = "later"
    after["details"]["aq_phase1"]["readiness"] = {"ready": True}
    h1 = source_hashes(before, {"ready_for_worker_launch": False})
    h2 = source_hashes(after, {"ready_for_worker_launch": True})
    assert h1.intent_hash == h2.intent_hash
    assert h1.observation_hash != h2.observation_hash


def test_semantic_intent_change_changes_hash():
    first = task()
    second = deepcopy(first)
    second["details"]["aq_phase1"]["intent"]["scope"] = ["schema"]
    assert task_intent_hash(first) != task_intent_hash(second)


def test_missing_or_unknown_immutable_contract_fields_fail_closed():
    missing = task()
    del missing["details"]["aq_phase1"]["intent"]["authority"]
    with pytest.raises(IntentContractError):
        task_intent_hash(missing)
    unknown = task()
    unknown["details"]["aq_phase1"]["intent"]["mutable_status"] = "ready"
    with pytest.raises(IntentContractError):
        task_intent_hash(unknown)


def test_non_finite_numbers_are_not_canonicalized():
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_mission_boundary_hash_changes_only_with_mission_or_repo_authority():
    mission={"objective":"ship safely","measure":{"what":"x","where":"select 1"},"horizon":"month","boundaries":{"may_act_alone":["local"]}}
    first=mission_boundary_hash(mission,{"repos":["aq"]})
    assert first == mission_boundary_hash(deepcopy(mission),{"repos":["aq"]})
    changed=deepcopy(mission); changed["boundaries"]={"may_act_alone":["public"]}
    assert first != mission_boundary_hash(changed,{"repos":["aq"]})
    assert first != mission_boundary_hash(mission,{"repos":["other"]})
