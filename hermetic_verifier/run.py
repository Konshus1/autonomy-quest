#!/usr/bin/env python3
"""Materialize one git commit, run it powerless, and sign the host-observed verdict."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Sequence

SCHEMA = "aq.hermetic-verdict.v1"
DEFAULT_IMAGE = "aq-hermetic-verifier:local"
MAX_DOCKER_OUTPUT = 512 * 1024


def _run(argv: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), check=False, **kwargs)


def _git(repo: pathlib.Path, *args: str) -> str:
    result = _run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def resolve_commit(repo: pathlib.Path, requested: str) -> str:
    resolved = _git(repo, "rev-parse", "--verify", f"{requested}^{{commit}}")
    if len(resolved) != 40 or any(c not in "0123456789abcdef" for c in resolved):
        raise RuntimeError("git returned a non-canonical candidate commit")
    return resolved


def repository_id(repo: pathlib.Path) -> str:
    result = _run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else str(repo.resolve())


def materialize(repo: pathlib.Path, commit: str, destination: pathlib.Path) -> None:
    archive = destination.parent / "candidate.tar"
    result = _run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(archive), commit], capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "git archive failed")
    destination.mkdir()
    # git archive emits relative member names, but retain an explicit containment check.
    with tarfile.open(archive, "r:") as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"candidate archive path escapes root: {member.name!r}")
        bundle.extractall(destination, filter="data")
    archive.unlink()


def image_identity(image: str) -> str:
    result = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Docker image is unavailable: {image}")
    identity = result.stdout.strip()
    if not identity.startswith("sha256:"):
        raise RuntimeError("Docker returned an invalid image identity")
    return identity


def _bounded_docker(argv: list[str], timeout: int) -> tuple[int, bytes, bool]:
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    captured = bytearray()

    def drain() -> None:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            room = MAX_DOCKER_OUTPUT - len(captured)
            if room > 0:
                captured.extend(chunk[:room])

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        return_code = 124
        process.kill()
        process.wait()
    reader.join(timeout=5)
    return return_code, bytes(captured), timed_out


def docker_command(*, image: str, source: pathlib.Path, commit: str, name: str, command: list[str], memory: str, cpus: str) -> list[str]:
    # Create first, inspect the engine's effective policy, and only then start candidate code.
    return [
        "docker", "create", "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--user", "65532:65534",
        "--pids-limit", "256",
        "--memory", memory,
        "--cpus", cpus,
        "--ipc", "none",
        "--tmpfs", "/work:rw,nosuid,nodev,size=256m,mode=0700,uid=65532,gid=65534",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=0700,uid=65532,gid=65534",
        "--mount", f"type=bind,src={source},dst=/candidate,readonly,bind-propagation=rprivate",
        "--env", f"AQ_CANDIDATE_SHA={commit}",
        image,
        *command,
    ]


def inspect_policy(*, name: str, source: pathlib.Path, image_id: str, commit: str) -> dict:
    result = _run(["docker", "inspect", name], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot inspect created verifier container")
    try:
        inspected = json.loads(result.stdout)
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise ValueError("unexpected inspect document")
        info = inspected[0]
        host = info["HostConfig"]
        config = info["Config"]
        mounts = info["Mounts"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"malformed Docker inspect output: {exc}") from exc

    failures: list[str] = []
    checks = {
        "image ID": info.get("Image") == image_id,
        "numeric non-root user": config.get("User") == "65532:65534",
        "network disabled": host.get("NetworkMode") == "none",
        "root filesystem read-only": host.get("ReadonlyRootfs") is True,
        "not privileged": host.get("Privileged") is False,
        "all capabilities dropped": host.get("CapDrop") == ["ALL"] and not host.get("CapAdd"),
        "no-new-privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
        "private IPC": host.get("IpcMode") == "none",
        "PID bound": host.get("PidsLimit") == 256,
        "no devices": not host.get("Devices") and not host.get("DeviceRequests"),
        "no bind shorthand": not host.get("Binds"),
        "sized tmpfs only": set((host.get("Tmpfs") or {}).keys()) == {"/work", "/tmp"},
        "one source mount": len(mounts) == 1,
    }
    if len(mounts) == 1:
        mount = mounts[0]
        checks["source bind exact and read-only"] = (
            mount.get("Type") == "bind"
            and pathlib.Path(mount.get("Source", "")).resolve() == source.resolve()
            and mount.get("Destination") == "/candidate"
            and mount.get("RW") is False
            and mount.get("Propagation") == "rprivate"
        )
    environment = config.get("Env") or []
    forwarded = [entry for entry in environment if entry.startswith("AQ_CANDIDATE_SHA=")]
    checks["only expected AQ environment"] = forwarded == [f"AQ_CANDIDATE_SHA={commit}"] and not any(
        entry.startswith(("AQ_DB_", "AQ_GOVERNANCE_", "AQ_ACTOR_", "AQ_LOOP_", "AQ_APPROVAL_"))
        for entry in environment
    )
    for label, passed in checks.items():
        if not passed:
            failures.append(label)
    if failures:
        raise RuntimeError("effective Docker policy rejected: " + ", ".join(failures))
    return {
        "effective_policy_inspected": True,
        "mount_count": len(mounts),
        "network_mode": host["NetworkMode"],
        "readonly_rootfs": host["ReadonlyRootfs"],
        "user": config["User"],
    }


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sign(payload: dict, private_key: pathlib.Path) -> str:
    mode = stat.S_IMODE(private_key.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(f"signing key permissions must be owner-only (0600 or stricter), got {mode:04o}")
    # OpenSSL's Ed25519 implementation is one-shot and some builds reject a pipe because its
    # length is unknown. A private host temp file contains only the public verdict payload.
    with tempfile.TemporaryDirectory(prefix="aq-verdict-sign-") as temp:
        message = pathlib.Path(temp) / "payload.json"
        message.write_bytes(canonical(payload))
        result = _run(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key), "-in", str(message)],
            capture_output=True,
        )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "OpenSSL signing failed")
    return base64.b64encode(result.stdout).decode("ascii")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=pathlib.Path)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--signing-key", required=True, type=pathlib.Path)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--memory", default="1g")
    parser.add_argument("--cpus", default="2")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="candidate command after -- (default: pytest -q)")
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        args.command = ["python", "-m", "pytest", "-q"]
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    started = dt.datetime.now(dt.timezone.utc)
    started_monotonic = time.monotonic()
    name = f"aq-hermetic-{secrets.token_hex(6)}"
    container_rc = 125
    timed_out = False
    infrastructure_error = None
    resolved = ""
    image_id = ""
    repo_id = ""
    effective_policy: dict = {"effective_policy_inspected": False}
    command = list(args.command)

    try:
        repo = args.repo.resolve(strict=True)
        resolved = resolve_commit(repo, args.sha)
        repo_id = repository_id(repo)
        image_id = image_identity(args.image)
        with tempfile.TemporaryDirectory(prefix="aq-hermetic-source-") as temp:
            source = pathlib.Path(temp) / "candidate"
            materialize(repo, resolved, source)
            docker_argv = docker_command(
                image=args.image, source=source, commit=resolved, name=name,
                command=command, memory=args.memory, cpus=args.cpus,
            )
            created = _run(docker_argv, text=True, capture_output=True)
            if created.returncode:
                raise RuntimeError(created.stderr.strip() or "Docker could not create verifier container")
            try:
                effective_policy = inspect_policy(name=name, source=source, image_id=image_id, commit=resolved)
                container_rc, output, timed_out = _bounded_docker(
                    ["docker", "start", "--attach", name], args.timeout
                )
                state_result = _run(
                    ["docker", "inspect", name, "--format", "{{json .State}}"],
                    text=True, capture_output=True,
                )
                if state_result.returncode == 0:
                    state = json.loads(state_result.stdout)
                    if state.get("OOMKilled") or state.get("Error"):
                        infrastructure_error = "container OOM-killed" if state.get("OOMKilled") else state.get("Error")
                if output:
                    sys.stderr.write("[untrusted candidate log]\n")
                    sys.stderr.buffer.write(output)
                    if len(output) >= MAX_DOCKER_OUTPUT:
                        sys.stderr.write("\n[host log cap reached]\n")
            finally:
                _run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:  # Emit a signed ERROR when possible; never turn infrastructure failure GREEN.
        infrastructure_error = str(exc)

    if infrastructure_error or timed_out or container_rc == 125:
        verdict = "ERROR"
    elif container_rc == 0:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    ended = dt.datetime.now(dt.timezone.utc)
    payload = {
        "schema": SCHEMA,
        "verdict": verdict,
        "candidate": {"repository": repo_id, "commit": resolved},
        "test_command": command,
        "runtime": {"image": args.image, "image_id": image_id},
        "result": {
            "exit_code": container_rc,
            "timed_out": timed_out,
            "infrastructure_error": infrastructure_error,
        },
        "policy": {
            "network": "none",
            "root_filesystem": "read-only",
            "capabilities": [],
            "no_new_privileges": True,
            "uid": 65532,
            "source_mount": "read-only-git-archive",
            "writable_filesystems": ["tmpfs:/work", "tmpfs:/tmp"],
            "credentials_forwarded": [],
            **effective_policy,
        },
        "started_at": started.isoformat(),
        "finished_at": ended.isoformat(),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "nonce": secrets.token_hex(16),
    }
    try:
        signature = sign(payload, args.signing_key)
    except Exception as exc:
        print(f"verifier could not sign verdict: {exc}", file=sys.stderr)
        return 2
    envelope = {"payload": payload, "signature": {"algorithm": "ed25519", "value": signature}}
    print(json.dumps(envelope, sort_keys=True))
    return 0 if verdict == "PASS" else (1 if verdict == "FAIL" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
