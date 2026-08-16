"""W1a gated deploy pathway (task #6087) — the guest-emitted deploy REQUEST contract.

Red-first coverage of the request layer: exact-SHA (never a floating tag), git-SHA-ONLY artifact (a
guest may NOT name an image — the host resolves the SHA to its own image), guest-never-executes,
no-credential-rides-the-request, and flag-default-OFF.
"""
import pytest

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
        _base(requested_sha="latest", artifact={"git_sha": "latest"}))
    assert any("EXACT stamped git SHA" in e for e in errors)
    assert dr.is_exact_sha("latest") is False
    assert dr.is_exact_sha("main") is False


def test_exact_sha_accepts_git_hex_only() -> None:
    assert dr.is_exact_sha("a" * 40) is True
    assert dr.is_exact_sha("deadbee") is True          # 7 hex is the floor
    assert dr.is_exact_sha("xyz") is False
    assert dr.is_exact_sha("sha256:" + "b" * 64) is False  # requested_sha is a GIT sha, not a digest


# --- git-SHA-ONLY artifact: the guest never names the image ------------------
def test_image_key_is_rejected() -> None:
    errors = dr.validate_deploy_request(_base(
        requested_sha="c" * 40, artifact={"image": "reg/app:" + "c" * 40}))
    assert any("must not carry an 'image'" in e for e in errors)


def test_unknown_artifact_key_rejected() -> None:
    errors = dr.validate_deploy_request(_base(
        artifact={"git_sha": "a" * 40, "registry": "attacker.example.com"}))
    assert any("unknown key" in e for e in errors)


def test_git_sha_must_equal_requested_sha() -> None:
    errors = dr.validate_deploy_request(_base(
        requested_sha="a" * 40, artifact={"git_sha": "e" * 40, "build_context": "."}))
    assert any("must EQUAL requested_sha" in e for e in errors)


def test_build_context_must_be_relative_no_traversal() -> None:
    assert any("RELATIVE" in e for e in dr.validate_deploy_request(
        _base(artifact={"git_sha": "a" * 40, "build_context": "/etc"})))
    assert any("RELATIVE" in e for e in dr.validate_deploy_request(
        _base(artifact={"git_sha": "a" * 40, "build_context": "../../secrets"})))


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
        target="aq-site", adapter="aca", git_sha="f" * 40, build_context=".",
        requester_instance_id="aq-1", mission_id="m1")
    assert req.adapter == "aca"
    assert req.artifact == {"git_sha": "f" * 40, "build_context": "."}
    assert "image" not in req.artifact
    assert dr.validate_deploy_request(req.to_packet()) == []


def test_build_deploy_request_raises_on_floating_tag() -> None:
    with pytest.raises(ValueError):
        dr.build_deploy_request(
            target="aq-site", adapter="mock", git_sha="latest",
            requester_instance_id="aq-1", mission_id="m1")
