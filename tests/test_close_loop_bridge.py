from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from runner.close_loop.bridge import (
    Bridge,
    BridgeMode,
    BridgeModeError,
    BridgeRefusal,
    InMemoryBridgeLedger,
    QueueBridge,
    SourceSnapshot,
    DispatchCapability,
)
from runner.close_loop.hashing import source_hashes
from runner.close_loop.lease import (
    AQSelector,
    LeaseAuthorityError,
    RalphSelector,
    SharedLeaseStore,
)


def source_task():
    return {
        "id": 42,
        "title": "Bounded task",
        "description": "Do one thing",
        "status": "pending",
        "parent_task_id": None,
        "completed_on": None,
        "details": {
            "aq_phase1": {
                "admit": True,
                "intent": {
                    "repo_id": "aq",
                    "target_ref": "refs/heads/main",
                    "scope": ["runner/close_loop"],
                    "definition_of_done": ["tests pass"],
                    "stop_condition": "one slice",
                    "verifier_manifest": {"id": "v1", "sha256": "a" * 64},
                    "authority": {"class": "local_dev"},
                    "dependencies": [],
                },
            }
        },
    }


def readiness():
    return {
        "task_id": 42,
        "status": "launch_ready_bounded_dev_safe_slice",
        "ready_for_worker_launch": True,
        "missing_contract_fields": [],
        "contract_state": {},
    }


def snapshot(*, status="launch_ready_bounded_dev_safe_slice", ready=True,
             cancel=False, admitted=None, task=None, readiness_row=None):
    task = task or source_task()
    readiness_row = readiness_row or readiness()
    hashes = source_hashes(task, readiness_row)
    return SourceSnapshot(
        source_system="talkingback",
        source_task_id="42",
        status=status,
        readiness=ready,
        cancel=cancel,
        hashes=hashes,
        admitted_intent_hash=admitted,
        payload=task,
    )


def test_repeated_observe_ticks_write_only_explicit_observation_rows():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    bridge = QueueBridge(BridgeMode.OBSERVE, ledger=ledger, lease_store=store)
    item = snapshot()
    for _ in range(3):
        bridge.observe(item)
    assert len(ledger.observations) == 3
    assert store.count() == 0
    assert ledger.links == {}
    assert ledger.works == {}
    assert ledger.sessions == ledger.worktrees == ledger.branches == []
    assert ledger.runs == ledger.candidates == ledger.dispatches == []


def test_exact_mode_interlocks_block_wrong_mutators_before_side_effects():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    observe = QueueBridge("observe", ledger=ledger, lease_store=store)
    with pytest.raises(BridgeModeError):
        observe.materialize(snapshot())
    with pytest.raises(BridgeModeError):
        observe.dispatch(object())
    materialize = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    )
    with pytest.raises(BridgeModeError):
        materialize.observe(snapshot())
    with pytest.raises(BridgeModeError):
        materialize.dispatch(object())
    assert store.count() == 0
    assert not ledger.links and not ledger.dispatches


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        (snapshot(ready=False), "ready_for_worker_launch_not_true"),
        (snapshot(status="worker_dispatched"), "status_not_exactly_launch_ready"),
        (snapshot(cancel=True), "source_cancelled"),
        (snapshot(cancel=None), "cancel_state_not_boolean"),
        (snapshot(admitted="0" * 64), "stale_intent_hash_disagreement"),
    ],
)
def test_stale_disagreement_cancellation_and_intent_drift_yield_zero_dispatch(item, reason):
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    bridge = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    )
    with pytest.raises(BridgeRefusal, match=reason):
        bridge.materialize(item)
    assert store.count() == 0
    assert not ledger.links and not ledger.works and not ledger.dispatches


def test_racing_real_aq_and_ralph_selector_classes_grants_exactly_one_owner():
    store = SharedLeaseStore()
    item = snapshot()
    aq = AQSelector(store, owner_instance="aq-1")
    ralph = RalphSelector(store, owner_instance="ralph-1")
    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = list(pool.map(lambda selector: selector.select(item), (aq, ralph)))
    assert sum(grant.acquired for grant in grants) == 1
    assert {grant.decision.value for grant in grants} == {"acquired", "held"}
    assert store.count() == 1


def test_materialize_creates_one_link_and_worker_reviewer_work_without_dispatch():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    bridge = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    )
    first = bridge.materialize(snapshot())
    second = bridge.materialize(snapshot())
    assert first.link == second.link
    assert first.work == second.work
    assert len(ledger.links) == len(ledger.works) == 1
    assert first.work.execution_path == "worker_reviewer"
    assert not ledger.sessions and not ledger.worktrees and not ledger.branches
    assert not ledger.runs and not ledger.candidates and not ledger.dispatches


def test_expired_owner_generation_is_fenced_after_other_selector_reclaims():
    store = SharedLeaseStore()
    item = snapshot()
    old = AQSelector(store, owner_instance="aq-1").select(item)
    store.expire_for_test(old)
    new = RalphSelector(store, owner_instance="ralph-1").select(item)
    assert new.acquired and new.generation == old.generation + 1
    with pytest.raises(LeaseAuthorityError):
        store.assert_authority(old)
    store.assert_authority(new)


def test_dispatch_requires_fresh_materialized_capability_and_positive_control():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    materializer = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    )
    materialized = materializer.materialize(snapshot())
    called = []
    dispatcher = QueueBridge(
        "dispatch", ledger=ledger, lease_store=store,
        dispatcher=lambda work_id: called.append(work_id) or "started",
    )
    with pytest.raises(LeaseAuthorityError):
        dispatcher.dispatch(object())
    receipt = dispatcher.dispatch(materialized.capability)
    assert called == [materialized.work.id]
    assert receipt.result == "started"
    assert len(ledger.dispatches) == 1


def test_dispatch_refuses_capability_after_release_or_fence():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    materialized = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    ).materialize(snapshot())
    assert store.release(materialized.lease)
    called = []
    dispatch = QueueBridge(
        "dispatch", ledger=ledger, lease_store=store,
        dispatcher=lambda work_id: called.append(work_id),
    )
    with pytest.raises(LeaseAuthorityError):
        dispatch.dispatch(materialized.capability)
    assert called == []
    assert ledger.dispatches == []


def test_observation_and_intent_hashes_are_bound_into_materialized_link():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    item = snapshot()
    result = QueueBridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    ).materialize(item)
    assert result.link.source_intent_hash == item.intent_hash
    assert result.link.source_observation_hash == item.observation_hash
    assert result.link.lease_token == result.lease.token
    assert result.link.lease_generation == result.lease.generation


def test_mission_boundary_change_yields_zero_dispatch_authority():
    item = snapshot()
    changed = SourceSnapshot(
        item.source_system, item.source_task_id, item.status, item.readiness,
        item.cancel, item.hashes, item.intent_hash, "a"*64, "b"*64, item.payload,
    )
    store=SharedLeaseStore(); selector=AQSelector(store,owner_instance="aq")
    grant=selector.try_claim(changed)
    assert not grant.acquired and grant.reason == "mission_boundary_changed"
    assert store.count() == 0


def test_bridge_mode_parse_and_tick_are_strict_exact_routers():
    assert BridgeMode.parse("observe") is BridgeMode.OBSERVE
    assert BridgeMode.parse(BridgeMode.DISPATCH) is BridgeMode.DISPATCH
    for bad in ("Observe", " observe", "dispatch ", True, None):
        with pytest.raises(ValueError):
            BridgeMode.parse(bad)

    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    observed = Bridge("observe", ledger=ledger, lease_store=store).tick(snapshot=snapshot())
    assert observed.source_task_id == "42"
    with pytest.raises(TypeError):
        Bridge("observe", ledger=ledger, lease_store=store).tick()


def test_dispatch_rejects_fabricated_work_binding_even_with_live_grant():
    store = SharedLeaseStore()
    ledger = InMemoryBridgeLedger()
    result = Bridge(
        "materialize", ledger=ledger, lease_store=store,
        selector=AQSelector(store, owner_instance="aq-1"),
    ).tick(snapshot=snapshot())
    dispatch = Bridge(
        "dispatch", ledger=ledger, lease_store=store,
        dispatcher=lambda work_id: pytest.fail(f"dispatched fabricated work {work_id}"),
    )
    forged = DispatchCapability(result.lease, result.work.id + 100, object())
    with pytest.raises(LeaseAuthorityError, match="not bound"):
        dispatch.tick(capability=forged)
