"""Docker-only execution boundary for untrusted close-loop candidates.

The launcher intentionally exposes one read-only bind: the candidate tree.  The
container has no network, daemon socket, credentials, capabilities, or writable
persistent filesystem.  Its only writable mount is a fresh, bounded ``/tmp``
tmpfs which dies with the ``--rm`` container.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from typing import Mapping, Sequence

DEFAULT_IMAGE = "aq-close-loop-verifier:local"
CANDIDATE_TARGET = "/candidate"
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")


class SandboxError(RuntimeError):
    """A fail-closed sandbox setup or execution failure."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SandboxLimits:
    pids: int = 64
    memory: str = "256m"
    cpus: str = "1.0"
    tmpfs_size: str = "64m"
    timeout_seconds: int = 600

    def __post_init__(self) -> None:
        if self.pids < 1 or self.timeout_seconds < 1:
            raise ValueError("sandbox pids and timeout must be positive")
        for field in (self.memory, self.cpus, self.tmpfs_size):
            if not field or any(ch.isspace() for ch in field):
                raise ValueError("sandbox resource limits must be non-empty tokens")


@dataclass(frozen=True)
class SandboxResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


# Values are constants, never copied from os.environ.  Docker does not inherit the
# launcher's environment unless explicitly asked with -e NAME; do not add that form.
_CLEAN_ENV = (
    "HOME=/tmp/home",
    "TMPDIR=/tmp",
    "PATH=/usr/local/bin:/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONNOUSERSITE=1",
    "PYTHONHASHSEED=0",
)


def require_docker() -> str:
    """Return the Docker executable or fail.  Security controls never skip."""
    executable = shutil.which("docker")
    if not executable:
        raise SandboxError("docker_unavailable", "docker executable is required")
    try:
        probe = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxError("docker_unavailable", f"docker daemon probe failed: {exc}") from exc
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip()[-1000:]
        raise SandboxError("docker_unavailable", f"docker daemon is required: {detail}")
    return executable


def _candidate_source(candidate: str | Path) -> Path:
    source = Path(candidate).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise SandboxError("candidate_invalid", f"candidate is not a directory: {source}")
    # Docker's --mount parser uses commas as field separators and has no portable
    # escaping.  Refuse an ambiguous source rather than mounting a different path.
    if "," in str(source) or "\n" in str(source) or "\x00" in str(source):
        raise SandboxError("candidate_invalid", "candidate path is unsafe for Docker --mount")
    return source


def docker_run_argv(
    candidate: str | Path,
    command: Sequence[str],
    *,
    image: str = DEFAULT_IMAGE,
    limits: SandboxLimits = SandboxLimits(),
    container_name: str | None = None,
    docker: str = "docker",
) -> tuple[str, ...]:
    """Construct the complete, auditable Docker argv (never a shell string)."""
    source = _candidate_source(candidate)
    if not command or any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise SandboxError("command_invalid", "sandbox command must be non-empty string argv")
    if not _IMAGE.fullmatch(image):
        raise SandboxError("image_invalid", "sandbox image reference contains unsafe characters")
    if container_name is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container_name):
        raise SandboxError("container_name_invalid", "invalid Docker container name")

    argv = [
        docker,
        "run",
        "--rm",
        "--network", "none",
        "--read-only",
        "--mount", f"type=bind,source={source},target={CANDIDATE_TARGET},readonly",
        "--tmpfs", f"/tmp:rw,nosuid,nodev,noexec,size={limits.tmpfs_size},mode=1777",
        "--user", "65532",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(limits.pids),
        "--memory", limits.memory,
        "--memory-swap", limits.memory,
        "--cpus", limits.cpus,
        "--ulimit", "core=0",
        "--ulimit", "nofile=128:128",
        "--workdir", CANDIDATE_TARGET,
        "--hostname", "verifier-sandbox",
    ]
    if container_name:
        argv.extend(("--name", container_name))
    for value in _CLEAN_ENV:
        argv.extend(("--env", value))
    # Override any image default without invoking a shell.  The production
    # trusted-verifier caller passes its image-owned absolute interpreter here;
    # the adversarial control deliberately passes python3 to exercise the wall.
    argv.extend(("--entrypoint", command[0], image))
    argv.extend(command[1:])
    return tuple(argv)


def run_in_sandbox(
    candidate: str | Path,
    command: Sequence[str],
    *,
    image: str = DEFAULT_IMAGE,
    limits: SandboxLimits = SandboxLimits(),
) -> SandboxResult:
    """Execute candidate argv in the mandatory Docker boundary.

    A timeout is a refusal.  The random name exists only so the trusted launcher
    can force-remove a timed-out container; it is never exposed inside the
    candidate and normal completion is still handled by ``--rm``.
    """
    docker = require_docker()
    name = "aq-close-loop-" + uuid.uuid4().hex
    argv = docker_run_argv(
        candidate, command, image=image, limits=limits,
        container_name=name, docker=docker,
    )
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=limits.timeout_seconds,
            env={},  # the Docker CLI itself receives no host credentials either
        )
    except subprocess.TimeoutExpired as exc:
        subprocess.run(
            [docker, "rm", "-f", name], capture_output=True, timeout=15, env={}
        )
        raise SandboxError(
            "sandbox_timeout", f"sandbox exceeded {limits.timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise SandboxError("sandbox_unrunnable", str(exc)) from exc
    return SandboxResult(argv, proc.returncode, proc.stdout or "", proc.stderr or "")


def run_required(
    candidate: str | Path,
    command: Sequence[str],
    *,
    image: str = DEFAULT_IMAGE,
    limits: SandboxLimits = SandboxLimits(),
) -> SandboxResult:
    """Run and convert every non-zero candidate result into a closed refusal."""
    result = run_in_sandbox(candidate, command, image=image, limits=limits)
    if not result.passed:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise SandboxError("verifier_failed", f"verifier exited {result.exit_code}: {detail}")
    return result
