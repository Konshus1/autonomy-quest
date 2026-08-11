from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import datetime as dt

from hermetic_verifier import run as verifier
from hermetic_verifier import verify_verdict


def _git(path: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], check=True, text=True, capture_output=True)
    return result.stdout.strip()


def test_materialize_is_exact_pinned_tree_without_git_or_untracked_secret(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "tracked.txt").write_text("pinned")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "candidate")
    sha = verifier.resolve_commit(repo, "HEAD")
    (repo / "tracked.txt").write_text("working-tree mutation")
    (repo / ".env").write_text("PUSH_TOKEN=must-not-leak")

    destination = tmp_path / "export" / "candidate"
    destination.parent.mkdir()
    verifier.materialize(repo, sha, destination)

    assert (destination / "tracked.txt").read_text() == "pinned"
    assert not (destination / ".git").exists()
    assert not (destination / ".env").exists()


def test_docker_policy_is_deny_by_default_and_mount_allowlisted(tmp_path):
    command = verifier.docker_command(
        image="verifier@sha256:abc", source=tmp_path, commit="a" * 40,
        name="proof", command=["python", "-m", "pytest"], memory="1g", cpus="2",
    )
    rendered = " ".join(map(str, command))
    assert "--network none" in rendered
    assert "--read-only" in command
    assert "--cap-drop ALL" in rendered
    assert "--security-opt no-new-privileges:true" in rendered
    assert "--user 65532:65534" in rendered
    assert "--ipc none" in rendered
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert mounts == [f"type=bind,src={tmp_path},dst=/candidate,readonly,bind-propagation=rprivate"]
    assert "/var/run/docker.sock" not in rendered
    assert "AQ_DB_URL" not in rendered
    assert "SSH_AUTH_SOCK" not in rendered
    assert "PUSH_TOKEN" not in rendered


def test_ed25519_signature_binds_every_payload_field(tmp_path):
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    private.chmod(0o600)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    payload = {"schema": verifier.SCHEMA, "verdict": "PASS", "candidate": {"commit": "a" * 40}}
    signature = verifier.sign(payload, private)

    message = tmp_path / "message.json"
    sig = tmp_path / "signature.bin"
    import base64
    message.write_bytes(verifier.canonical(payload))
    sig.write_bytes(base64.b64decode(signature))
    good = subprocess.run([
        "openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", public,
        "-sigfile", sig, "-in", message,
    ])
    assert good.returncode == 0
    payload["verdict"] = "FAIL"
    message.write_bytes(verifier.canonical(payload))
    tampered = subprocess.run([
        "openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", public,
        "-sigfile", sig, "-in", message,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert tampered.returncode != 0


def test_signing_key_must_not_be_group_or_world_readable(tmp_path):
    private = tmp_path / "private.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    private.chmod(0o644)
    try:
        verifier.sign({"verdict": "PASS"}, private)
    except RuntimeError as exc:
        assert "owner-only" in str(exc)
    else:
        raise AssertionError("insecure signing key permissions were accepted")


def _keys(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    private.chmod(0o600)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    return private, public


def _authorization_payload(*, nonce: str = "nonce-001") -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    command = ["python", "-m", "pytest", "-q"]
    entrypoint = ["python", "/opt/hermetic-verifier/container_entrypoint.py"]
    policy = {
        "network": "none",
        "root_filesystem": "read-only",
        "capabilities": [],
        "no_new_privileges": True,
        "uid": 65532,
        "source_mount": "read-only-git-archive",
        "writable_filesystems": ["tmpfs:/work", "tmpfs:/tmp"],
        "credentials_forwarded": [],
        "effective_policy_inspected": True,
    }
    return {
        "schema": verifier.SCHEMA,
        "verdict": "PASS",
        "candidate": {"repository": "git@example.invalid:aq/repo.git", "commit": "a" * 40},
        "test_plan": {
            "command": command,
            "digest": verifier.digest_json({"command": command}),
        },
        "runtime": {
            "image": "aq-hermetic-verifier:local",
            "image_id": "sha256:" + "b" * 64,
            "entrypoint": entrypoint,
            "entrypoint_digest": verifier.digest_json(entrypoint),
        },
        "result": {"exit_code": 0, "timed_out": False, "infrastructure_error": None,
                   "census_valid": True, "clean": True, "collected_count": 1,
                   "passed_count": 1, "failed_count": 0, "skipped_count": 0,
                   "error_count": 0},
        "policy": policy,
        "policy_digest": verifier.digest_json(policy),
        "authorization": {
            "key_id": "close-loop-test-key-v1",
            "issued_at": now.isoformat(),
            "expires_at": (now + dt.timedelta(minutes=5)).isoformat(),
            "nonce": nonce,
        },
    }


def _authorization_request(tmp_path: pathlib.Path, public: pathlib.Path, payload: dict):
    return verify_verdict.AuthorizationRequest(
        public_key=public,
        repository=payload["candidate"]["repository"],
        candidate_sha=payload["candidate"]["commit"],
        test_plan_digest=payload["test_plan"]["digest"],
        image_id=payload["runtime"]["image_id"],
        entrypoint_digest=payload["runtime"]["entrypoint_digest"],
        policy_digest=payload["policy_digest"],
        key_id=payload["authorization"]["key_id"],
        nonce_store=tmp_path / "used-nonces",
    )


def _signed(payload: dict, private: pathlib.Path) -> dict:
    return {"payload": payload, "signature": {"algorithm": "ed25519", "value": verifier.sign(payload, private)}}


def test_authorization_accepts_legitimate_bound_nonvacuous_fresh_verdict(tmp_path):
    private, public = _keys(tmp_path)
    payload = _authorization_payload()
    accepted = verify_verdict.verify_envelope(_signed(payload, private), _authorization_request(tmp_path, public, payload))
    assert accepted.accepted
    assert accepted.nonce == "nonce-001"


def test_authorization_rejects_swapped_entrypoint_even_when_resigned(tmp_path):
    private, public = _keys(tmp_path)
    payload = _authorization_payload()
    request = _authorization_request(tmp_path, public, payload)
    payload["runtime"]["entrypoint"] = ["/usr/bin/true"]
    payload["runtime"]["entrypoint_digest"] = verifier.digest_json(payload["runtime"]["entrypoint"])
    rejected = verify_verdict.verify_envelope(_signed(payload, private), request)
    assert not rejected.accepted
    assert rejected.reason == "entrypoint_identity_mismatch"


def test_authorization_rejects_vacuous_and_all_skipped_pass(tmp_path):
    private, public = _keys(tmp_path)
    for passed, skipped, reason in ((0, 0, "vacuous_test_run"), (0, 3, "all_tests_skipped")):
        payload = _authorization_payload(nonce=f"nonce-{passed}-{skipped}")
        payload["result"]["passed_count"] = passed
        payload["result"]["skipped_count"] = skipped
        rejected = verify_verdict.verify_envelope(
            _signed(payload, private), _authorization_request(tmp_path, public, payload)
        )
        assert not rejected.accepted
        assert rejected.reason == reason


def test_authorization_binds_command_image_policy_key_expiry_and_replay_nonce(tmp_path):
    private, public = _keys(tmp_path)
    mutations = {
        "test_plan_digest_mismatch": ("test_plan_digest", "c" * 64),
        "image_identity_mismatch": ("image_id", "sha256:" + "c" * 64),
        "effective_policy_mismatch": ("policy_digest", "c" * 64),
        "signing_key_id_mismatch": ("key_id", "other-key"),
    }
    for reason, (field, value) in mutations.items():
        payload = _authorization_payload(nonce="nonce-" + reason)
        request = _authorization_request(tmp_path, public, payload)
        object.__setattr__(request, field, value)
        rejected = verify_verdict.verify_envelope(_signed(payload, private), request)
        assert not rejected.accepted
        assert rejected.reason == reason

    expired = _authorization_payload(nonce="expired-001")
    expired["authorization"]["issued_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=2)).isoformat()
    expired["authorization"]["expires_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
    rejected = verify_verdict.verify_envelope(_signed(expired, private), _authorization_request(tmp_path, public, expired))
    assert not rejected.accepted and rejected.reason == "verdict_expired"

    payload = _authorization_payload(nonce="replayed")
    request = _authorization_request(tmp_path, public, payload)
    envelope = _signed(payload, private)
    assert verify_verdict.verify_envelope(envelope, request).accepted
    rejected = verify_verdict.verify_envelope(envelope, request)
    assert not rejected.accepted and rejected.reason == "nonce_replayed"


def test_effective_config_environment_is_exact_allowlist(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    source.mkdir()
    image_id = "sha256:" + "d" * 64
    commit = "a" * 40
    inspected = [{
        "Image": image_id,
        "Config": {
            "User": "65532:65534",
            "Entrypoint": ["python", "/opt/hermetic-verifier/container_entrypoint.py"],
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", f"AQ_CANDIDATE_SHA={commit}", "SIGNER_SECRET=leak"],
        },
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
            "CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges:true"],
            "IpcMode": "none", "PidsLimit": 256, "Devices": [], "DeviceRequests": [],
            "Binds": None, "Tmpfs": {"/work": "x", "/tmp": "x"},
        },
        "Mounts": [{"Type": "bind", "Source": str(source), "Destination": "/candidate", "RW": False,
                    "Propagation": "rprivate"}],
    }]
    monkeypatch.setattr(verifier, "_run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(inspected), ""))
    try:
        verifier.inspect_policy(name="proof", source=source, image_id=image_id, commit=commit)
    except RuntimeError as exc:
        assert "environment allowlist" in str(exc)
    else:
        raise AssertionError("unexpected Config.Env entry was accepted")


def test_rc_zero_is_not_pass_without_nonvacuous_semantics():
    clean = {"census_valid": True, "clean": True, "collected_count": 1,
             "failed_count": 0, "error_count": 0}
    assert verifier.classify_verdict(
        infrastructure_error=None, timed_out=False, container_rc=0,
        semantic_result_observed=False, passed_count=0, **clean,
    ) == "FAIL"
    assert verifier.classify_verdict(
        infrastructure_error=None, timed_out=False, container_rc=0,
        semantic_result_observed=True, passed_count=0, **clean,
    ) == "FAIL"
    assert verifier.classify_verdict(
        infrastructure_error=None, timed_out=False, container_rc=0,
        semantic_result_observed=True, passed_count=1, **clean,
    ) == "PASS"


def test_legacy_stdout_summary_is_not_a_trusted_census():
    output = b"1 passed in 0.01s\nAQ_HERMETIC_RESULT:{\"passed_count\":1,\"skipped_count\":0}\n"
    assert verifier.parse_test_semantics(output) is None
