"""Mandatory, external controls for the untrusted candidate Docker boundary.

There is deliberately no skip path here.  No Docker daemon means this control
rig failed and therefore cannot certify the verifier runtime.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

from runner.close_loop.verifier import DockerVerifier
from runner.close_loop.sandbox import (
    SandboxLimits,
    docker_run_argv,
    require_docker,
    run_in_sandbox,
)

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "container" / "verifier.Dockerfile"
MALICIOUS = ROOT / "tests" / "fixtures" / "close_loop" / "malicious_candidate.py"
PINNED_BASE = "python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df"


def _run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


@pytest.fixture(scope="session")
def verifier_image():
    try:
        docker = require_docker()
    except Exception as exc:
        pytest.fail(f"MANDATORY Docker sandbox control unavailable: {exc}", pytrace=False)

    supplied = os.environ.get("AQ_CLOSE_LOOP_VERIFIER_IMAGE")
    image = supplied or f"aq-close-loop-verifier:test-{uuid.uuid4().hex}"
    owned = not supplied
    if supplied:
        inspect = _run([docker, "image", "inspect", image], timeout=30)
        if inspect.returncode:
            pytest.fail(f"mandatory verifier image {image!r} is absent: {inspect.stderr}")
    else:
        built = _run(
            [docker, "build", "--pull", "-f", str(DOCKERFILE), "-t", image, str(ROOT)],
            timeout=300,
        )
        if built.returncode:
            pytest.fail("mandatory verifier image build failed:\n" + (built.stderr or built.stdout)[-4000:])
    try:
        yield image
    finally:
        if owned:
            _run([docker, "image", "rm", "-f", image], timeout=30)


def _candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    (candidate / "control").mkdir(parents=True)
    (candidate / ".git" / "refs" / "remotes" / "origin").mkdir(parents=True)
    shutil.copy2(MALICIOUS, candidate / "malicious_candidate.py")
    (candidate / "control" / "manifest.json").write_text('{"checks":["fixed"]}\n')
    (candidate / ".git" / "refs" / "remotes" / "origin" / "main").write_text("a" * 40 + "\n")
    # Make a deliberately weakened rw bind observably writable even though the
    # production command executes as UID 65532.  The strong control must rely on
    # the mount policy, not convenient host ownership.
    for directory in [candidate, candidate / "control", candidate / ".git",
                      candidate / ".git" / "refs", candidate / ".git" / "refs" / "remotes",
                      candidate / ".git" / "refs" / "remotes" / "origin"]:
        directory.chmod(0o777)
    for file in candidate.rglob("*"):
        if file.is_file():
            file.chmod(0o666 if file.name != "malicious_candidate.py" else 0o777)
    return candidate


def _snapshot(path: Path):
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*")) if item.is_file()
    }


def _json_stdout(text: str):
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines, "sandbox emitted no result"
    return json.loads(lines[-1])


def test_dockerfile_and_launcher_hold_the_complete_policy(tmp_path):
    dockerfile = DOCKERFILE.read_text()
    assert f"FROM {PINNED_BASE}" in dockerfile
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    argv = docker_run_argv(candidate, ["python3", "probe.py"], image="verifier:test")

    def value(flag):
        return argv[argv.index(flag) + 1]

    assert "--rm" in argv
    assert value("--network") == "none"
    assert "--read-only" in argv
    assert value("--user") == "65532"
    assert value("--cap-drop") == "ALL"
    assert value("--security-opt") == "no-new-privileges"
    assert int(value("--pids-limit")) > 0
    assert value("--memory") and value("--memory-swap") == value("--memory")
    assert value("--cpus")
    mount = value("--mount")
    assert mount.endswith(",target=/candidate,readonly") and "type=bind" in mount
    tmpfs = value("--tmpfs")
    assert tmpfs.startswith("/tmp:rw,") and all(x in tmpfs for x in ("nosuid", "nodev", "noexec", "size="))
    assert "/var/run/docker.sock" not in " ".join(argv)
    env_values = [argv[index + 1] for index, arg in enumerate(argv) if arg == "--env"]
    assert env_values and all("=" in item for item in env_values)  # never inherit with `-e NAME`
    assert not any(name in " ".join(argv) for name in ("AWS_", "AZURE_", "GITHUB_TOKEN"))


def test_malicious_candidate_cannot_cross_or_persist_sandbox(tmp_path, verifier_image, monkeypatch):
    candidate = _candidate(tmp_path)
    host_target = tmp_path / "host-persisted"
    host_target.write_text("HOST-UNCHANGED\n")
    before = _snapshot(candidate)
    host_before = host_target.read_bytes()
    monkeypatch.setenv("AQ_CLOSE_LOOP_CANARY_SECRET", "must-not-enter-container")

    attack = run_in_sandbox(
        candidate,
        ["python3", "/candidate/malicious_candidate.py", "attack", str(host_target)],
        image=verifier_image,
        limits=SandboxLimits(timeout_seconds=20),
    )
    assert attack.exit_code == 0, attack.stderr
    observed = _json_stdout(attack.stdout)

    # Assertions run outside the adversary.  Its self-report alone is not evidence.
    assert _snapshot(candidate) == before
    assert host_target.read_bytes() == host_before
    assert not (candidate / "persisted-by-candidate").exists()
    assert not (tmp_path / "persisted-by-candidate").exists()
    assert observed["control_file"] != "wrote"
    assert observed["remote_ref"] != "wrote"
    assert observed["candidate_persistence"] != "wrote"
    assert observed["root_persistence"] != "wrote"
    assert observed["credential_canary"] is None
    assert observed["credential_names"] == []
    assert observed["docker_socket_exists"] is False
    assert observed["docker_socket_connect"] != "connected"
    assert observed["external_network"] != "connected"
    assert observed["tmp_write"] == "wrote"  # the one allowed, disposable writable place

    second = run_in_sandbox(
        candidate,
        ["python3", "/candidate/malicious_candidate.py", "probe"],
        image=verifier_image,
        limits=SandboxLimits(timeout_seconds=20),
    )
    assert second.exit_code == 0, second.stderr
    assert _json_stdout(second.stdout) == {"candidate_survived": False, "tmp_survived": False}
    assert _snapshot(candidate) == before
    assert host_target.read_bytes() == host_before


def test_control_is_sensitive_to_a_writable_candidate_bind(tmp_path, verifier_image):
    """A policy mutant must go red, proving the attack fixture is not decorative."""
    candidate = _candidate(tmp_path)
    host_target = tmp_path / "host-persisted"
    host_target.write_text("HOST-UNCHANGED\n")
    strong = list(docker_run_argv(
        candidate,
        ["python3", "/candidate/malicious_candidate.py", "attack", str(host_target)],
        image=verifier_image,
        docker=require_docker(),
    ))
    mount_index = strong.index("--mount") + 1
    assert strong[mount_index].endswith(",readonly")
    strong[mount_index] = strong[mount_index][:-len(",readonly")]
    # Keep every other production restriction, changing only the candidate bind.
    weak = _run(strong, timeout=20, env={})
    assert weak.returncode == 0, weak.stderr
    observed = _json_stdout(weak.stdout)
    assert observed["control_file"] == "wrote"
    assert observed["remote_ref"] == "wrote"
    assert (candidate / "persisted-by-candidate").exists()


def test_actual_trusted_verifier_rejects_failure_skip_and_candidate_plugin(verifier_image):
    """The image entrypoint, not only the generic wall, must fail closed."""
    docker = require_docker()
    inspected = _run(
        [docker, "image", "inspect", "--format", "{{.Id}}", verifier_image], timeout=30
    )
    assert inspected.returncode == 0, inspected.stderr
    image_id = inspected.stdout.strip()
    trusted = ROOT / "tests" / "fixtures" / "close_loop" / "trusted_checks"
    manifests = ROOT / "tests" / "fixtures" / "close_loop" / "manifests"
    candidates = ROOT / "tests" / "fixtures" / "close_loop"
    verifier = DockerVerifier(image_id, docker=docker, timeout_seconds=60)

    malicious = verifier.verify(
        manifests / "malicious.json", candidates / "malicious_candidate",
        trusted_checks_root=trusted,
    )
    assert malicious.verdict == "hold"
    assert any("required_check_failed" in code for code in malicious.reason_codes)

    positive = verifier.verify(
        manifests / "positive.json", candidates / "positive_candidate",
        trusted_checks_root=trusted,
    )
    assert positive.verdict == "approve_local"

    weak = verifier.verify(
        manifests / "weak.json", candidates / "weak_candidate",
        trusted_checks_root=trusted,
    )
    assert weak.verdict == "hold"
    assert any("required_check_skipped" in code for code in weak.reason_codes)
