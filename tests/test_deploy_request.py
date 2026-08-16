"""W1a gated deploy pathway (task #6087) — the guest-emitted deploy REQUEST contract.

Red-first coverage of the request layer: exact-SHA (never a floating tag), artifact-carries-the-SHA
(can't build one SHA and deploy another), guest-never-executes, no-credential-rides-the-request, and
flag-default-OFF.
"""
from ralph_portable import deploy_request as dr


def _base(**extra):
    packet = {
        "target": "aq-marketing-site",
        "adapter": "mock",
        "requested_sha": "a" * 40,
        "artifact": {"git_sha": "a" * 40, "build_context": "."},
        "requester_instance_id": "aq-parent-1",
        "mission_id": "mission-ship-site",
    }
    packet.update(extra)
    return packet


def test_valid_request() -> None:
    assert dr.validate_deploy_request(_base()) == []


def test_flag_default_off() -> None:
    assert dr.enabled({}) is False
    assert dr.enabled({dr.FLAG: "1"}) is True
    assert dr.enabled({dr.FLAG: "0"}) is False


def test_unknown_adapter_rejected() -> None:
    errors = dr.validate_deploy_request(_base(adapter="rogue-cloud"))
    assert any("adapter must be one of" in e for e in errors)


# --- EXACT-SHA: never a floating tag -----------------------------------------
def test_latest_tag_is_rejected() -> None:
    errors = dr.validate_deploy_request(
        _base(requested_sha="latest", artifact={"image": "reg/app:latest"}))
    assert any("EXACT stamped SHA" in e for e in errors)
    assert dr.is_exact_sha("latest") is False
    assert dr.is_exact_sha("main") is False


def test_exact_sha_accepts_git_hex_and_digest() -> None:
    assert dr.is_exact_sha("a" * 40) is True
    assert dr.is_exact_sha("sha256:" + "b" * 64) is True
    assert dr.is_exact_sha("deadbee") is True          # 7 hex is the floor
    assert dr.is_exact_sha("xyz") is False


def test_image_artifact_must_carry_requested_sha() -> None:
    # image pinned by the SHA (as a tag/suffix) -> ok
    assert dr.validate_deploy_request(_base(
        requested_sha="c" * 40, artifact={"image": "reg/app:" + "c" * 40})) == []
    # image pinned by the SHA as an OCI digest -> ok
    assert dr.validate_deploy_request(_base(
        requested_sha="sha256:" + "c" * 64, artifact={"image": "reg/app@sha256:" + "c" * 64})) == []
    # image pinned by a DIFFERENT sha -> rejected (built one SHA, asked to deploy another)
    errors = dr.validate_deploy_request(_base(
        requested_sha="c" * 40, artifact={"image": "reg/app:" + "d" * 40}))
    assert any("does not carry requested_sha" in e for e in errors)


def test_git_sha_must_equal_requested_sha() -> None:
    errors = dr.validate_deploy_request(_base(
        requested_sha="a" * 40, artifact={"git_sha": "e" * 40, "build_context": "."}))
    assert any("does not carry requested_sha" in e for e in errors)


# --- guest never executes ----------------------------------------------------
def test_guest_must_not_execute() -> None:
    errors = dr.validate_deploy_request(_base(guest_executes=True))
    assert any("HOST executes" in e for e in errors)


# --- no credential ever rides the request ------------------------------------
def test_credential_key_is_rejected() -> None:
    for key in ("AZURE_CLIENT_SECRET", "AQ_DEPLOY_AZURE_CREDENTIAL", "deploy_token", "api_key"):
        errors = dr.validate_deploy_request(_base(**{key: "s3cr3t"}))
        assert any("must not carry a credential" in e for e in errors), key


def test_credential_marker_detects_secrets() -> None:
    assert dr.credential_marker("AZURE_CLIENT_SECRET") is not None
    assert dr.credential_marker("AQ_DEPLOY_AZURE_CREDENTIAL") is not None
    assert dr.credential_marker("target") is None


# --- builder fails loud ------------------------------------------------------
def test_build_deploy_request_roundtrips() -> None:
    req = dr.build_deploy_request(
        target="aq-site", adapter="aca", requested_sha="f" * 40,
        artifact={"git_sha": "f" * 40, "build_context": "."},
        requester_instance_id="aq-1", mission_id="m1")
    assert req.adapter == "aca"
    assert dr.validate_deploy_request(req.to_packet()) == []


def test_build_deploy_request_raises_on_floating_tag() -> None:
    import pytest
    with pytest.raises(ValueError):
        dr.build_deploy_request(
            target="aq-site", adapter="mock", requested_sha="latest",
            artifact={"image": "reg/app:latest"}, requester_instance_id="aq-1", mission_id="m1")
