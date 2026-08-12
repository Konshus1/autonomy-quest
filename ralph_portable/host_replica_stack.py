"""Host-ONLY replica stack lifecycle (Task #4834, self-replication Step 1).

DANGER — READ THIS. This module executes ``docker``/``docker compose`` against the host
daemon. It stands up and tears down REAL, independent container stacks. It must therefore
NEVER be importable from inside a guest container, nor from any guest-reachable module
(the autonomous mission loop, or the in-container management API). A guest only ever
*proposes* a replication row; the HOST — via ``scripts/host_replication_broker.py`` — is
the only legitimate caller of this module. The import firewall
(``tests/test_import_firewall.py`` + ``ralph_portable/import_firewall.py``) enforces that
structurally: guest-reachable modules that import this one FAIL CI.

What this adds on top of the safe filesystem copy in ``host_replication_executor``:
after the instance-dir copy, the host brings up a NEW, ISOLATED Docker Compose stack for
the replica — a distinct compose project (``aq-replica-<shortid>``), its own
project-namespaced volumes, a generated secret file, and a probed free host-port range —
then ASSERTS the replica is a live independent instance by polling its ``/health`` for
reachability + a present ``git_sha`` (the same deploy-hygiene liveness contract).

SAFETY GATES enforced here before any spin-up (the review will attack these):
  1. Host-executes-only: all docker/compose calls live here + the host broker, never in a
     guest-reachable module (import-firewall proof).
  2. BOUNDED: refuse at/over ``AQ_REPLICATION_MAX_REPLICAS`` (default 2). The count is over
     ALL ``aq-replica-*`` projects regardless of who asked, so replicas cannot fork-bomb
     across generations.
  3. RESOURCE-GATED: refuse below ``AQ_REPLICATION_MIN_FREE_MEM_PCT`` (default 35) free host
     memory. A replica is a full ~4-service stack; the host just rebooted from pressure.
  4. REVERSIBLE: every replica is enumerable by project prefix and torn down (``down -v`` +
     generated-file removal) by ``teardown_replica`` / ``scripts/host_replication_teardown.py``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ralph_portable.host_replication_executor import _safe_component
from ralph_portable.replication_request import (
    advance_host_execution,
    validate_replication_request,
)

# --- naming + config ---------------------------------------------------------
REPLICA_PROJECT_PREFIX = "aq-replica-"
MAX_REPLICAS_ENV = "AQ_REPLICATION_MAX_REPLICAS"
MIN_FREE_MEM_PCT_ENV = "AQ_REPLICATION_MIN_FREE_MEM_PCT"
LOCK_FILE_ENV = "AQ_REPLICATION_LOCK_FILE"
DEFAULT_MAX_REPLICAS = 2
DEFAULT_MIN_FREE_MEM_PCT = 35.0

# The parent/seed and its overlays publish these host ports; a replica must avoid them
# even before the OS "is this bound?" probe, so a momentary gap can't hand us a parent port.
_KNOWN_PARENT_HOST_PORTS = frozenset({8080, 8090, 8091, 55483})

# Compose label that carries the project name — the authoritative, orphan-proof way to
# enumerate and reap replicas (independent of any file we wrote).
_COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

_COMPOSE_LABEL_FILTER = f"label={_COMPOSE_PROJECT_LABEL}"

# A short id is a single filesystem/DNS-safe component; keep it tight so the project name
# stays a legal compose project (lowercase alnum + dashes).
_SHORTID_RE = re.compile(r"[^a-z0-9]+")


# A docker runner: takes argv (WITHOUT the leading "docker"), returns stdout. Injectable so
# unit tests never touch the daemon. Raises subprocess.CalledProcessError on nonzero.
DockerRunner = Callable[..., str]
# A memory reader: returns free-memory percentage (0..100). Injectable for the gate test.
MemReader = Callable[[], float]


def _default_docker(
    args: list[str], *, check: bool = True, timeout: float | None = 120.0,
    env: dict[str, str] | None = None, cwd: str | Path | None = None,
) -> str:
    proc = subprocess.run(
        ["docker", *args], check=check, timeout=timeout, env=env, cwd=cwd,
        capture_output=True, text=True,
    )
    return proc.stdout


def replica_cap(env: dict[str, str] | None = None) -> int:
    source = env if env is not None else os.environ
    try:
        val = int(str(source.get(MAX_REPLICAS_ENV, DEFAULT_MAX_REPLICAS)).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_REPLICAS
    return max(0, val)


def min_free_mem_pct(env: dict[str, str] | None = None) -> float:
    source = env if env is not None else os.environ
    try:
        return float(str(source.get(MIN_FREE_MEM_PCT_ENV, DEFAULT_MIN_FREE_MEM_PCT)).strip())
    except (TypeError, ValueError):
        return DEFAULT_MIN_FREE_MEM_PCT


# --- TOCTOU cap lock (host-side flock) ---------------------------------------
# Review note #2 (now load-bearing WITH a daemon): the cap gate is a check-then-act.
# Between "count aq-replica-* stacks" and "compose up", a CONCURRENT daemon poll or a
# manual broker could each observe count < cap and both spin up, breaching the cap.
# A host-side advisory flock serializes the count->stand-up critical section across
# every process on the host, so the bound holds under concurrency. The lockfile lives
# at a FIXED host path (beside the replicas state dir, or AQ_REPLICATION_LOCK_FILE) so
# the daemon and the broker contend on the SAME lock regardless of who started first.
def default_cap_lock_path(env: dict[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    override = source.get(LOCK_FILE_ENV)
    if override:
        return Path(override)
    return default_state_root(source).parent / ".aq-replica-cap.lock"


@contextmanager
def replica_cap_lock(
    lock_path: Path | str, *, timeout_s: float = 1800.0, poll_s: float = 0.1,
) -> Iterator[Path]:
    """Hold an exclusive host-side flock for the count->stand-up critical section.

    Advisory ``flock`` released automatically on fd close / process death, so a crashed
    holder never wedges the host (no stale lock). Bounded acquire (non-blocking poll to a
    deadline) so an automated caller can never hang forever waiting on a wedged peer.
    """
    import fcntl

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"could not acquire replica cap lock {path} within {timeout_s:.0f}s"
                    )
                time.sleep(poll_s)
        yield path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --- enumeration (gate 2 input + reap discovery) -----------------------------
def list_replica_projects(docker: DockerRunner = _default_docker) -> list[str]:
    """Distinct compose projects with the replica prefix, INCLUDING stopped stacks.

    Counting stopped-but-present stacks (``ps -a``) is deliberate: a stopped replica still
    holds volumes and can be restarted, so it must count against the cap. This is the
    across-generations bound — any ``aq-replica-*`` counts no matter who created it.
    """
    out = docker([
        "ps", "-a", "--filter", _COMPOSE_LABEL_FILTER,
        "--format", "{{.Label \"%s\"}}" % _COMPOSE_PROJECT_LABEL,
    ])
    projects = {
        line.strip()
        for line in out.splitlines()
        if line.strip().startswith(REPLICA_PROJECT_PREFIX)
    }
    return sorted(projects)


def count_live_replicas(docker: DockerRunner = _default_docker) -> int:
    return len(list_replica_projects(docker=docker))


# --- memory (gate 3 input) ---------------------------------------------------
def _default_mem_reader() -> float:
    """Free host memory as a percentage. psutil when available, else OS-native parsing.

    Uses ``available`` memory (reclaimable included) over total — the honest "can I fit a
    new 4-service stack" number, not merely wired/free pages.
    """
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return float(vm.available) / float(vm.total) * 100.0
    except Exception:
        pass
    # Linux
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        vals: dict[str, int] = {}
        for line in meminfo.read_text().splitlines():
            k, _, rest = line.partition(":")
            vals[k.strip()] = int(rest.strip().split()[0])  # kB
        total = vals.get("MemTotal")
        avail = vals.get("MemAvailable") or vals.get("MemFree")
        if total and avail is not None:
            return avail / total * 100.0
    # macOS: vm_stat pages
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True).stdout
        page = 4096
        m = re.search(r"page size of (\d+) bytes", out)
        if m:
            page = int(m.group(1))
        counts: dict[str, int] = {}
        for line in out.splitlines():
            m2 = re.match(r"\"?([\w\s]+?)\"?:\s+(\d+)\.", line)
            if m2:
                counts[m2.group(1).strip().lower()] = int(m2.group(2))
        free_pages = counts.get("pages free", 0) + counts.get("pages speculative", 0) \
            + counts.get("pages inactive", 0) + counts.get("pages purgeable", 0)
        used_pages = (
            counts.get("pages active", 0) + counts.get("pages wired down", 0)
            + counts.get("pages occupied by compressor", 0) + counts.get("pages free", 0)
            + counts.get("pages speculative", 0) + counts.get("pages inactive", 0)
        )
        if used_pages:
            return free_pages / used_pages * 100.0
    except Exception:
        pass
    # Cannot measure -> fail CLOSED (report 0% free so the gate refuses). Better to refuse a
    # spin-up than to spin one up on an unknown-memory host that just rebooted from pressure.
    return 0.0


def host_free_mem_pct(reader: MemReader = _default_mem_reader) -> float:
    return reader()


# --- gate predicates (pure; unit-tested without docker) ----------------------
def check_cap_gate(replica_count: int, cap: int) -> tuple[bool, str]:
    if replica_count >= cap:
        return False, (
            f"replica cap reached: {replica_count} live aq-replica-* stack(s) >= cap {cap} "
            f"(raise {MAX_REPLICAS_ENV} to allow more; refusing to fork-bomb the host)"
        )
    return True, f"cap ok: {replica_count} < {cap}"


def check_mem_gate(free_pct: float, min_pct: float) -> tuple[bool, str]:
    if free_pct < min_pct:
        return False, (
            f"insufficient free memory: {free_pct:.1f}% < required {min_pct:.1f}% "
            f"({MIN_FREE_MEM_PCT_ENV}); a replica is a full ~4-service stack, refusing spin-up"
        )
    return True, f"memory ok: {free_pct:.1f}% >= {min_pct:.1f}%"


# --- identity + ports --------------------------------------------------------
def allocate_replica_identity(
    packet: dict[str, Any] | None = None, *, existing: Iterable[str] = (),
) -> tuple[str, str, str]:
    """Return ``(project_name, instance_id, shortid)`` — collision-free + traversal-safe.

    The shortid is fresh random entropy (not derived from guest-controlled fields), then run
    through the same ``_safe_component`` discipline as the filesystem copy so a crafted
    requester/mission can never inject a path/label separator. The project name is checked
    against ``existing`` so two concurrent requests cannot land on the same project.
    """
    taken = set(existing)
    for _ in range(64):
        shortid = _SHORTID_RE.sub("", secrets.token_hex(4).lower())
        safe = _safe_component(shortid) or ""
        # _safe_component can only shrink an already-[a-z0-9] string; guard anyway.
        if not safe or safe != shortid:
            continue
        project = f"{REPLICA_PROJECT_PREFIX}{shortid}"
        if project in taken:
            continue
        instance_id = f"urn:uuid:{uuid.uuid4()}"
        return project, instance_id, shortid
    raise RuntimeError("could not allocate a collision-free replica identity")  # pragma: no cover


def probe_free_ports(
    count: int, *, exclude: Iterable[int] = (), host: str = "127.0.0.1",
) -> list[int]:
    """Probe ``count`` distinct, currently-free TCP host ports, none in ``exclude``.

    Holds each socket open until all are chosen so the kernel does not hand the same
    ephemeral port to two calls in the same allocation.
    """
    excluded = set(exclude) | set(_KNOWN_PARENT_HOST_PORTS)
    held: list[socket.socket] = []
    ports: list[int] = []
    try:
        while len(ports) < count:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, 0))
            p = s.getsockname()[1]
            if p in excluded:
                s.close()
                continue
            held.append(s)
            excluded.add(p)
            ports.append(p)
    finally:
        for s in held:
            s.close()
    return ports


@dataclass
class ReplicaPorts:
    postgres: int
    governance: int
    app_ui: int
    app_mgmt: int

    def as_dict(self) -> dict[str, int]:
        return {
            "postgres": self.postgres,
            "governance": self.governance,
            "app_ui": self.app_ui,
            "app_mgmt": self.app_mgmt,
        }


def render_ports_overlay(ports: ReplicaPorts, *, host: str = "127.0.0.1") -> str:
    """A compose overlay that REPLACES every published port with a replica-unique host port.

    Uses the compose ``!override`` tag (compose >= 2.24) so the base file's fixed
    ``127.0.0.1:8080:8080`` etc. are REPLACED, not appended — appended mappings would keep the
    parent's ports and collide. Every mapping stays bound to loopback (never 0.0.0.0).
    """
    return (
        "# GENERATED for a replica stack — safe to delete when the replica is torn down.\n"
        "services:\n"
        "  postgres:\n"
        "    ports: !override\n"
        f'      - "{host}:{ports.postgres}:5432"\n'
        "  governance:\n"
        "    ports: !override\n"
        f'      - "{host}:{ports.governance}:8090"\n'
        "  app:\n"
        "    ports: !override\n"
        f'      - "{host}:{ports.app_ui}:8080"\n'
        f'      - "{host}:{ports.app_mgmt}:8090"\n'
    )


_SECRET_KEYS = (
    "AQ_DB_OWNER_PASSWORD",
    "AQ_LOOP_DB_PASSWORD",
    "AQ_ACTOR_DB_PASSWORD",
    "AQ_GOVERNANCE_DB_PASSWORD",
    "AQ_EVALUATOR_DB_PASSWORD",
    "AQ_GOVERNANCE_EVIDENCE_TOKEN",
    "AQ_GOVERNANCE_DECISION_TOKEN",
    "AQ_EVALUATOR_TOKEN",
    "AQ_EVALUATOR_TRIGGER_TOKEN",
    "AQ_GOVERNANCE_TOKEN",
)


def render_secret_file(instance_id: str) -> str:
    """A complete compose-secrets file for the replica (own creds, own AQ_INSTANCE_ID).

    Distinct secrets per replica so replicas share no credential material with the parent or
    each other. Written 0600 by the caller.
    """
    lines = [f"{k}={secrets.token_hex(32)}" for k in _SECRET_KEYS]
    lines.append(f"AQ_INSTANCE_ID={instance_id}")
    lines.append("AQ_GOVERNANCE_ADJUDICATOR=local-human-adjudicator")
    return "\n".join(lines) + "\n"


# --- state dir ---------------------------------------------------------------
def default_state_root(env: dict[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    base = source.get("AQ_STATE_DIR") or str(Path.home() / ".local/state/autonomy-quest")
    return Path(base) / "replicas"


def replica_dir(project: str, *, state_root: Path | str | None = None,
                env: dict[str, str] | None = None) -> Path:
    safe = _safe_component(project) or "replica"
    root = Path(state_root) if state_root is not None else default_state_root(env)
    return root / safe


# --- health assertion --------------------------------------------------------
@dataclass
class HealthResult:
    ok: bool
    reachable: bool = False
    git_sha: str | None = None
    reason: str = ""
    attempts: int = 0
    body: dict[str, Any] = field(default_factory=dict)


def poll_replica_health(
    mgmt_port: int, *, host: str = "127.0.0.1", timeout_s: float = 240.0,
    interval_s: float = 5.0, opener: Callable[[str], bytes] | None = None,
) -> HealthResult:
    """Assert the replica is a live independent instance: /health reachable + git_sha present.

    Deliberately does NOT require ``cycling`` — a straight_copy replica gets its own EMPTY
    codex-auth volume, so its loop will hibernate awaiting device auth. Step 1 proves "a new
    independent healthy stack stood up", not "the replica does work". Reachable + a present
    SHA is the liveness contract here.
    """
    url = f"http://{host}:{mgmt_port}/health"

    def _fetch(u: str) -> bytes:
        with urllib.request.urlopen(u, timeout=10) as resp:  # noqa: S310 (loopback only)
            return resp.read()

    fetch = opener or _fetch
    deadline = time.monotonic() + timeout_s
    attempts = 0
    last_reason = "never got a /health response"
    while time.monotonic() < deadline:
        attempts += 1
        try:
            raw = fetch(url)
            body = json.loads(raw)
            sha = body.get("git_sha")
            if body.get("ok") and sha:
                return HealthResult(True, reachable=True, git_sha=sha,
                                    reason=f"reachable + git_sha={str(sha)[:12]}",
                                    attempts=attempts, body=body)
            last_reason = (
                f"reachable but git_sha missing (ok={body.get('ok')} git_sha={sha!r})"
            )
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_reason = f"/health unreachable at {url} ({exc})"
        except (ValueError, json.JSONDecodeError) as exc:
            last_reason = f"/health non-JSON ({exc})"
        time.sleep(interval_s)
    return HealthResult(False, reason=f"timed out after {timeout_s:.0f}s: {last_reason}",
                        attempts=attempts)


# --- stand-up orchestration --------------------------------------------------
def stand_up_replica_stack(
    packet: dict[str, Any],
    *,
    status: str,
    repo_root: Path | str,
    git_sha: str | None = None,
    state_root: Path | str | None = None,
    env: dict[str, str] | None = None,
    docker: DockerRunner = _default_docker,
    mem_reader: MemReader = _default_mem_reader,
    health_timeout_s: float = 240.0,
    build: bool = True,
    do_health_check: bool = True,
    compose_runner: Callable[..., subprocess.CompletedProcess] | None = None,
    lock_path: Path | str | None = None,
) -> dict[str, Any]:
    """Bring up a NEW isolated compose stack for an approved replication request.

    Runs the three safety gates FIRST (cap, then memory), refusing before any side effect if
    either fails. Only from an ``approved``/``auto_approved`` status. Returns a record dict
    (never raises for operational failure): ``status`` is one of ``refused`` (a gate blocked),
    ``failed`` (spin-up/health error), or ``executed`` (a live independent replica).

    The cap-count -> compose-up critical section runs under a host-side ``flock`` (review note
    #2, now load-bearing with the daemon) so concurrent daemon polls / a manual broker cannot
    both pass the cap check and breach the bound. The lock is released BEFORE the (slow) health
    poll — the new stack already counts in ``docker ps -a`` once ``up -d`` returns.
    """
    env = env if env is not None else dict(os.environ)
    repo_root = Path(repo_root)

    # Guard: well-formed + host-safe request, and an approved gate. Done BEFORE taking the
    # lock — a malformed/unapproved request must never contend for the host cap lock.
    errors = validate_replication_request(packet)
    if errors:
        return _refused(packet, reason="; ".join(errors), gate="validate")
    advance_host_execution(status)  # raises on a non-approved status (caller misuse)

    lock_path = lock_path if lock_path is not None else default_cap_lock_path(env)

    # ---- BEGIN cap critical section (host flock) ----------------------------
    with replica_cap_lock(lock_path):
        # GATE 2 — bounded across generations.
        cap = replica_cap(env)
        existing = list_replica_projects(docker=docker)
        ok, reason = check_cap_gate(len(existing), cap)
        if not ok:
            return _refused(packet, reason=reason, gate="cap",
                            details={"cap": cap, "live_replicas": existing})

        # GATE 3 — resource-gated (mandatory).
        min_pct = min_free_mem_pct(env)
        free_pct = host_free_mem_pct(mem_reader)
        ok, reason = check_mem_gate(free_pct, min_pct)
        if not ok:
            return _refused(packet, reason=reason, gate="memory",
                            details={"free_mem_pct": round(free_pct, 1), "min_pct": min_pct})

        # Allocate a collision-free identity + free ports.
        project, instance_id, shortid = allocate_replica_identity(packet, existing=existing)
        p = probe_free_ports(4)
        ports = ReplicaPorts(postgres=p[0], governance=p[1], app_ui=p[2], app_mgmt=p[3])

        # Resolve the SHA to stamp (straight_copy => same code; default to the source HEAD).
        if git_sha is None:
            try:
                git_sha = subprocess.run(
                    ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                    capture_output=True, text=True, check=True,
                ).stdout.strip()
            except Exception:
                git_sha = ""

        rdir = replica_dir(project, state_root=state_root, env=env)
        secret_file = rdir / "compose-secrets"
        ports_overlay = rdir / "ports.yml"
        manifest_file = rdir / "replica.json"

        record: dict[str, Any] = {
            "ok": False,
            "executed_by": "host",
            "status": "host_executing",
            "mode": packet.get("mode"),
            "project": project,
            "instance_id": instance_id,
            "ports": ports.as_dict(),
            "health_url": f"http://127.0.0.1:{ports.app_mgmt}/health",
            "git_sha": git_sha,
            "requester_instance_id": packet.get("requester_instance_id"),
            "mission_id": packet.get("mission_id") or packet.get("mission"),
            "state_dir": str(rdir),
            "used_docker_sock": False,
            "distinct_project": True,
        }

        # Materialize the replica's generated files (0700 dir, 0600 secret).
        rdir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(rdir, 0o700)
        except OSError:  # pragma: no cover - platform
            pass
        secret_file.write_text(render_secret_file(instance_id))
        os.chmod(secret_file, 0o600)
        ports_overlay.write_text(render_ports_overlay(ports))
        manifest_file.write_text(json.dumps(record, indent=2, sort_keys=True))

        # Bring up the isolated stack via the SAME pattern as the live deploy:
        #   compose-with-secrets.sh -f docker-compose.yml -f <ports overlay> up -d [--build]
        # under a distinct COMPOSE_PROJECT_NAME + the replica's own secret file + GIT_SHA.
        compose_env = dict(env)
        compose_env["COMPOSE_PROJECT_NAME"] = project
        compose_env["AQ_COMPOSE_SECRET_FILE"] = str(secret_file)
        compose_env["AQ_STATE_DIR"] = str(rdir)
        compose_env["GIT_SHA"] = git_sha or ""
        up_args = ["-f", "docker-compose.yml", "-f", str(ports_overlay), "up", "-d"]
        if build:
            up_args.append("--build")

        runner = compose_runner or _run_compose_with_secrets
        try:
            proc = runner(up_args, cwd=repo_root, env=compose_env)
        except Exception as exc:  # noqa: BLE001 - operational failure -> failed record + reap
            _best_effort_teardown(project, rdir, docker=docker)
            record.update(status="failed", error=f"compose up raised: {exc}")
            _write_manifest(manifest_file, record)
            return record

        if proc.returncode != 0:
            _best_effort_teardown(project, rdir, docker=docker)
            record.update(
                status="failed",
                error=f"compose up exited {proc.returncode}",
                stderr=(proc.stderr or "")[-2000:],
            )
            _write_manifest(manifest_file, record)
            return record
    # ---- END cap critical section (lock released; the stack now counts) ------

    # ASSERT it is a live independent instance: /health reachable + SHA present.
    if do_health_check:
        health = poll_replica_health(ports.app_mgmt, timeout_s=health_timeout_s)
        record["health"] = {
            "ok": health.ok, "reachable": health.reachable,
            "git_sha": health.git_sha, "reason": health.reason, "attempts": health.attempts,
        }
        if not health.ok:
            _best_effort_teardown(project, rdir, docker=docker)
            record.update(status="failed", error=f"replica health assertion failed: {health.reason}")
            _write_manifest(manifest_file, record)
            return record

    record.update(ok=True, status="executed", host_executed_at=_now_iso())
    _write_manifest(manifest_file, record)
    return record


def _run_compose_with_secrets(
    compose_args: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 1800.0,
) -> subprocess.CompletedProcess:
    script = Path(cwd) / "scripts" / "compose-with-secrets.sh"
    return subprocess.run(
        ["sh", str(script), *compose_args], cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )


# --- teardown (gate 4) -------------------------------------------------------
def teardown_replica(
    project: str, *, state_root: Path | str | None = None,
    env: dict[str, str] | None = None, docker: DockerRunner = _default_docker,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Fully remove a replica: its containers, volumes, networks, and generated files.

    Primary path is a label-based sweep (``com.docker.compose.project=<project>``) — the
    ``down -v`` equivalent that needs neither the compose file nor the secret env, so it
    reaps even a replica whose generated files were already deleted. Nothing orphaned.
    """
    if not project.startswith(REPLICA_PROJECT_PREFIX):
        return {"ok": False, "project": project,
                "error": f"refusing to tear down a non-replica project (must start with {REPLICA_PROJECT_PREFIX!r})"}

    result: dict[str, Any] = {"ok": True, "project": project,
                              "containers_removed": [], "volumes_removed": [],
                              "networks_removed": [], "files_removed": []}
    filt = f"label={_COMPOSE_PROJECT_LABEL}={project}"

    # Optional: best-effort `docker compose down -v` when we still have the files (clean stop).
    rdir = replica_dir(project, state_root=state_root, env=env)
    overlay = rdir / "ports.yml"
    secret = rdir / "compose-secrets"
    if repo_root and overlay.is_file() and secret.is_file():
        try:
            cenv = dict(env if env is not None else os.environ)
            cenv.update(COMPOSE_PROJECT_NAME=project, AQ_COMPOSE_SECRET_FILE=str(secret),
                        AQ_STATE_DIR=str(rdir), GIT_SHA=cenv.get("GIT_SHA", ""))
            _run_compose_with_secrets(
                ["-f", "docker-compose.yml", "-f", str(overlay), "down", "-v", "--remove-orphans"],
                cwd=Path(repo_root), env=cenv, timeout=300.0,
            )
        except Exception:  # noqa: BLE001 - the label sweep below is the guarantee
            pass

    # Authoritative sweep by label.
    cids = [c for c in docker(["ps", "-aq", "--filter", filt]).splitlines() if c.strip()]
    if cids:
        docker(["rm", "-f", *cids], check=False)
        result["containers_removed"] = cids
    vols = [v for v in docker(["volume", "ls", "-q", "--filter", filt]).splitlines() if v.strip()]
    if vols:
        docker(["volume", "rm", "-f", *vols], check=False)
        result["volumes_removed"] = vols
    nets = [n for n in docker(["network", "ls", "-q", "--filter", filt]).splitlines() if n.strip()]
    if nets:
        docker(["network", "rm", *nets], check=False)
        result["networks_removed"] = nets

    # Remove generated files last (only after the daemon-side reap succeeded).
    if rdir.exists():
        for child in sorted(rdir.rglob("*"), reverse=True):
            try:
                child.unlink() if child.is_file() or child.is_symlink() else child.rmdir()
                result["files_removed"].append(str(child))
            except OSError:  # pragma: no cover
                pass
        try:
            rdir.rmdir()
        except OSError:  # pragma: no cover
            pass
    return result


def teardown_all_replicas(
    *, state_root: Path | str | None = None, env: dict[str, str] | None = None,
    docker: DockerRunner = _default_docker, repo_root: Path | str | None = None,
) -> list[dict[str, Any]]:
    return [
        teardown_replica(p, state_root=state_root, env=env, docker=docker, repo_root=repo_root)
        for p in list_replica_projects(docker=docker)
    ]


def _best_effort_teardown(project: str, rdir: Path, *, docker: DockerRunner) -> None:
    try:
        teardown_replica(project, state_root=rdir.parent, docker=docker)
    except Exception:  # noqa: BLE001 - teardown of a failed spin-up is best-effort
        pass


# --- helpers -----------------------------------------------------------------
def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _write_manifest(path: Path, record: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(record, indent=2, sort_keys=True))
    except OSError:  # pragma: no cover
        pass


def _refused(packet: Any, *, reason: str, gate: str,
             details: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = {
        "ok": False,
        "executed_by": "host",
        "status": "refused",
        "gate": gate,
        "mode": packet.get("mode") if isinstance(packet, dict) else None,
        "reason": reason,
        "used_docker_sock": False,
    }
    if details:
        rec["details"] = details
    return rec
