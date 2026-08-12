"""Per-kind payload schema tests (#4834 comms Phase 2, design §4.1/§8.5).

The load-bearing rule proved here: an ``experiment.result`` artifact reference is an IMMUTABLE
sha256 DIGEST + metadata — never a host path or an auto-fetchable URL (the SSRF boundary). Plus size
caps, digest-format enforcement, the digest-MISMATCH check the parent uses against ground truth, and
that a claim is labelled a claim (``outcome_claimed`` never a verdict).
"""

from __future__ import annotations

import pytest

from management.api.comms_payloads import (
    MAX_ARTIFACT_REFS,
    PayloadError,
    artifact_digest_matches,
    build_experiment_progress,
    build_experiment_result,
    build_status_report,
    compute_digest,
    validate_replica_payload,
)
from management.api.comms_envelope import MAX_TEXT_BYTES, MAX_PAYLOAD_BYTES

GOOD_DIGEST = "sha256:" + "a" * 64


# --- status.report / experiment.progress -------------------------------------
def test_status_report_versioned_and_bounded():
    p = build_status_report(text="cycle done", state="cycling")
    assert p["schema_version"] == 1 and p["text"] == "cycle done" and p["state"] == "cycling"


def test_status_report_oversize_text_rejected():
    with pytest.raises(PayloadError):
        build_status_report(text="x" * (MAX_TEXT_BYTES + 1))


def test_progress_must_be_unit_interval():
    build_experiment_progress(text="halfway", progress=0.5)
    for bad in (-0.1, 1.1, True):
        with pytest.raises(PayloadError):
            build_experiment_progress(text="x", progress=bad)


# --- experiment.result: artifact refs are DIGESTS ONLY -----------------------
def test_result_accepts_digest_plus_metadata():
    p = build_experiment_result(
        summary="run finished", outcome_claimed="success",
        artifact_refs=[{"digest": GOOD_DIGEST, "media_type": "application/json",
                        "size_bytes": 12, "name": "metrics"}],
        metrics={"score": 0.9})
    assert p["artifact_refs"][0]["digest"] == GOOD_DIGEST
    assert p["outcome_claimed"] == "success"
    # A result ALWAYS declares it needs verification — it is a claim, not a verdict.
    assert p["verification_required"] is True


@pytest.mark.parametrize("forbidden_key", ["path", "url", "uri", "location", "fetch", "src"])
def test_result_rejects_host_path_or_url_reference(forbidden_key):
    # The SSRF / path-traversal boundary: a "reference" may NEVER carry a path or fetchable URL.
    with pytest.raises(PayloadError):
        build_experiment_result(
            summary="x",
            artifact_refs=[{"digest": GOOD_DIGEST, forbidden_key: "http://169.254.169.254/latest"}])


@pytest.mark.parametrize("bad_digest", [
    "md5:abc", "sha256:short", "sha256:" + "A" * 64, "/var/lib/artifact", "http://x/y",
    "sha256:" + "g" * 64,
])
def test_result_rejects_non_sha256_digest(bad_digest):
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", artifact_refs=[{"digest": bad_digest}])


def test_result_media_type_and_name_cannot_be_locators():
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", artifact_refs=[
            {"digest": GOOD_DIGEST, "media_type": "http://evil/x"}])
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", artifact_refs=[
            {"digest": GOOD_DIGEST, "name": "../../etc/passwd"}])


def test_result_too_many_refs_rejected():
    refs = [{"digest": GOOD_DIGEST}] * (MAX_ARTIFACT_REFS + 1)
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", artifact_refs=refs)


def test_result_bad_outcome_claim_rejected():
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", outcome_claimed="definitely-true")


def test_result_oversize_payload_rejected():
    # A summary within text cap but many metrics could still blow the envelope cap; assert the guard.
    big = {"m%d" % i: i for i in range(10_000)}
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", metrics=big)


def test_extra_must_be_scalar_and_cannot_override_reserved():
    build_experiment_result(summary="x", extra={"cycle": 4})
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", extra={"artifact_refs": [{"digest": GOOD_DIGEST}]})
    with pytest.raises(PayloadError):
        build_experiment_result(summary="x", extra={"nested": {"a": 1}})


# --- the digest-MISMATCH check the parent runs against ground truth ----------
def test_digest_matches_true_only_for_real_content():
    content = b"the real artifact bytes"
    ref = {"digest": compute_digest(content)}
    assert artifact_digest_matches(ref, content) is True
    # A forged claim: same ref, different (real) content -> mismatch, flagged by the parent.
    assert artifact_digest_matches(ref, b"tampered bytes") is False
    # A malformed digest never matches.
    assert artifact_digest_matches({"digest": "sha256:bad"}, content) is False


# --- validate_replica_payload (API hardening entry) --------------------------
def test_validate_replica_payload_roundtrips_each_kind():
    assert validate_replica_payload("status.report", {"text": "hi"})["schema_version"] == 1
    assert validate_replica_payload("experiment.progress", {"text": "p", "progress": 0.2})["progress"] == 0.2
    out = validate_replica_payload("experiment.result",
                                   {"summary": "s", "artifact_refs": [{"digest": GOOD_DIGEST}]})
    assert out["artifact_refs"][0]["digest"] == GOOD_DIGEST


def test_validate_replica_payload_rejects_forged_result_reference():
    with pytest.raises(PayloadError):
        validate_replica_payload("experiment.result",
                                 {"summary": "s", "artifact_refs": [{"digest": GOOD_DIGEST,
                                                                     "url": "http://x"}]})
