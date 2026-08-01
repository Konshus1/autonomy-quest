"""Task #4407 — host-mediated replication + OVERRIDE."""

from ralph_portable.replication_request import (
    OVERRIDE_ENV,
    advance_host_execution,
    initial_status_for_host,
    override_enabled,
    validate_host_execution_record,
    validate_replication_request,
)


def _base(**extra):
    packet = {
        "mode": "straight_copy",
        "mission_id": "mission-scale-1",
        "requester_instance_id": "aq-parent-1",
    }
    packet.update(extra)
    return packet


def test_valid_straight_copy() -> None:
    assert validate_replication_request(_base()) == []


def test_modifications_required() -> None:
    errors = validate_replication_request(
        _base(mode="copy_with_modifications", modifications={})
    )
    assert any("modifications" in e for e in errors)


def test_guest_must_not_execute() -> None:
    errors = validate_replication_request(_base(guest_executes=True))
    assert any("host" in e.lower() or "guest_executes" in e for e in errors)


def test_override_defaults_off() -> None:
    assert override_enabled({}) is False
    assert initial_status_for_host({}) == "awaiting_operator"


def test_override_auto_approves() -> None:
    env = {OVERRIDE_ENV: "1"}
    assert override_enabled(env) is True
    assert initial_status_for_host(env) == "auto_approved"
    assert advance_host_execution("auto_approved") == "host_executing"


def test_host_execution_record() -> None:
    assert (
        validate_host_execution_record(
            {
                "executed_by": "host",
                "status": "host_executing",
                "host_action": "docker_run_copy",
            }
        )
        == []
    )
    errors = validate_host_execution_record(
        {"executed_by": "guest", "status": "host_executing", "host_action": "x"}
    )
    assert any("host" in e for e in errors)
