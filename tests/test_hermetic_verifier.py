from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess

from hermetic_verifier import run as verifier


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
