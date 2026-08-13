"""The autonomous mission loop must never reach an ACTUATION surface — formal promotion, replication
execution, or fleet brokering. This uses the reusable import_firewall and extends the original
formal-only check to cover replication + the new fleet-registry too (one guard, all actuation).

The replication rows deserve special scrutiny: the module that stands up a REAL Docker replica stack
(``ralph_portable.host_replica_stack``) executes ``docker``/``docker compose`` against the host daemon.
It must live ONLY on the host (the broker), never actuate from a guest replica.

PRECISE, LAYERED GUARANTEE (defense-in-depth — do NOT overclaim "a guest cannot import it"):
the container image is built with a wholesale ``COPY ralph_portable/`` + ``COPY scripts/`` (see
``container/Dockerfile``), so the host-only modules are PHYSICALLY PRESENT in the guest image and
``import ralph_portable.host_replica_stack`` would in fact SUCCEED inside a guest. The real defense is
two independent layers:

  (a) IMPORT-REACH layer — the guest entrypoints' transitive import closure does NOT reach the
      host docker/replication modules, so NORMAL guest execution never calls them. That is what the
      ``forbidden_references`` + ``transitive_import_closure`` tests below prove: a docker import
      buried anywhere in what ``management/api/app.py`` (or the loop) imports fails CI. This is a
      strong guard against an accidental hot-path actuation, but it is NOT proof that arbitrary
      attacker-controlled code inside the guest could not import the physically-present module.

  (b) RUNTIME-EXECUTE layer — even if arbitrary code DID import a host module, the guest cannot
      EXECUTE docker: the guest image installs no ``docker`` CLI and no guest service mounts
      ``/var/run/docker.sock`` (see ``container/Dockerfile`` + ``docker-compose.yml``). Without the
      CLI and the daemon socket, ``docker``/``docker compose`` calls have nothing to talk to. This is
      the layer that actually makes host actuation from a guest IMPOSSIBLE, and
      ``test_guest_image_has_no_docker_cli`` + ``test_guest_services_do_not_mount_docker_sock`` assert
      it directly. Together (a) keeps the docker step off every normal guest path and (b) removes the
      means to actuate it at all — "host executes; guest has neither the reach nor the socket.\""""
import os
import pathlib
import re
import shutil

import pytest
import yaml

from ralph_portable.import_firewall import (
    forbidden_references,
    transitive_import_closure,
)

REPO = pathlib.Path(__file__).resolve().parent.parent

HOT_PATH = ["runner/loop.py", "runner/causal_sync.py", "ralph_portable/principle_mining.py"]

# Modules that RUN INSIDE the guest container. app.py serves the propose endpoint the guest hits; on
# its normal execution paths it reaches the request-validation surface only, never the host-only docker
# executor/stand-up (layer (a) — import-reach; the runtime layer (b) is asserted separately below).
GUEST_REACHABLE = HOT_PATH + ["management/api/app.py"]

# The container image + compose config, for the RUNTIME-EXECUTE layer (b) assertions.
CONTAINER_DOCKERFILE = REPO / "container" / "Dockerfile"
COMPOSE_FILE = REPO / "docker-compose.yml"
DOCKER_SOCK_PATH = "/var/run/docker.sock"

# The guest ENTRYPOINTS whose full transitive import closure defines everything a guest container
# can reach: the in-container management API (serves POST /api/replication/propose) and the
# autonomous mission loop. The transitive-closure test below proves the HOST-ONLY docker step is
# outside this closure — a guest can only ever WRITE a proposal row.
GUEST_ENTRYPOINTS = ["management/api/app.py", "runner/loop.py",
                     "runner/causal_sync.py", "ralph_portable/principle_mining.py"]

# The host-only docker step — the REAL container stand-up/teardown and the auto-executor daemon.
# NONE of these may appear in the guest import closure (that would hand a guest docker.sock reach).
HOST_ONLY_DOCKER_FILES = {
    "ralph_portable/host_replica_stack.py",       # docker/compose replica stand-up + teardown
    "ralph_portable/host_replication_executor.py",  # replication filesystem execution
    "scripts/host_replication_daemon.py",          # the host-only auto-executor daemon
    "ralph_portable/fleet_registry.py",            # HOST-OWNED topology registry writer (#4834 comms P0)
}
DOCKER_STEP_IMPORTS = (
    "ralph_portable.host_replica_stack",
    "ralph_portable.host_replication_executor",
)
DOCKER_STEP_NAMES = (
    "execute_replication_copy", "stand_up_replica_stack",
    "teardown_replica", "teardown_all_replicas", "replica_cap_lock",
)

FORBIDDEN_IMPORTS = (
    "ralph_portable.formal",              # formal promotion / oracle
    "ralph_portable.fleet_registry",      # fleet identity + comms brokering
    "ralph_portable.host_replication_executor",  # replication filesystem execution
    "ralph_portable.host_replica_stack",  # REAL docker/compose replica stand-up + teardown
)
FORBIDDEN_NAMES = (
    "apply_promotion", "verify_and_record", "attach_executor", "run_oracle", "formal_proof_evidence",
    "execute_replication_copy", "FleetRegistryStore", "mint_identity", "grant_cross_fleet",
    "stand_up_replica_stack", "teardown_replica",
)


def test_loop_cannot_reach_any_actuation_surface():
    violations = forbidden_references(
        HOT_PATH, import_prefixes=FORBIDDEN_IMPORTS, names=FORBIDDEN_NAMES, repo_root=REPO)
    assert not violations, f"the mission loop must not reach actuation: {violations}"


def test_guest_reachable_code_cannot_run_docker_replication():
    """Layer (a): the in-container API + loop's normal paths do not import/reference the host-only
    docker replica step — the guest writes a proposal row and stops; the HOST broker is the only path
    to ``docker compose up``.

    This proves the docker step is off every normal guest path. It does NOT (and cannot) prove that
    the physically-present module is un-importable — that gap is closed by the RUNTIME-EXECUTE layer
    (b): ``test_guest_image_has_no_docker_cli`` + ``test_guest_services_do_not_mount_docker_sock``."""
    violations = forbidden_references(
        GUEST_REACHABLE, import_prefixes=FORBIDDEN_IMPORTS, names=FORBIDDEN_NAMES, repo_root=REPO)
    assert not violations, (
        "guest-reachable code must not reach the host docker replica step "
        f"(would give a guest container docker.sock reach): {violations}")


def test_firewall_has_teeth(tmp_path):
    # Plant a hot-path file that DOES import + call a forbidden surface; the firewall must flag BOTH.
    bad = tmp_path / "bad_loop.py"
    bad.write_text("from ralph_portable.formal.oracle_harness import run_oracle\n"
                   "def cycle():\n    return run_oracle('k', None)\n")
    v = forbidden_references(["bad_loop.py"], import_prefixes=FORBIDDEN_IMPORTS,
                             names=FORBIDDEN_NAMES, repo_root=tmp_path)
    assert any("imports forbidden module" in x for x in v)
    assert any("references forbidden actuation name" in x for x in v)


def test_firewall_catches_dynamic_import_literal(tmp_path):
    # A dynamic import that dodges the AST channel (importlib with the module path as a STRING) is now
    # flagged by the text-path check — only deliberate string-splitting evades (documented limitation).
    bad = tmp_path / "sneaky_loop.py"
    bad.write_text("import importlib\n"
                   "def cycle():\n"
                   "    m = importlib.import_module('ralph_portable.formal.oracle_harness')\n"
                   "    return m\n")
    v = forbidden_references(["sneaky_loop.py"], import_prefixes=FORBIDDEN_IMPORTS,
                             names=FORBIDDEN_NAMES, repo_root=tmp_path)
    assert any("forbidden import path" in x for x in v), "dynamic-import literal must be flagged"


def test_firewall_is_clean_on_a_benign_file(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("import json\ndef f():\n    return json.dumps({'consult': True})\n")
    assert forbidden_references(["ok.py"], import_prefixes=FORBIDDEN_IMPORTS,
                                names=FORBIDDEN_NAMES, repo_root=tmp_path) == []


# --- TRANSITIVE closure (review note #1): the WHOLE guest closure, not 4 files ---
def test_guest_import_closure_excludes_the_host_docker_step():
    """Layer (a), the strong form: the FULL transitive import closure of the guest entrypoints must
    not contain — nor import, nor name — the host-only docker step (stand-up/teardown) or the
    auto-executor daemon.

    The original firewall checked only the 4 direct guest files. With a daemon that stands up REAL
    replicas hands-off, that is not enough: a docker import buried three hops deep inside something
    ``management/api/app.py`` imports would put the docker step on a normal guest path. This walks the
    entire in-repo import graph from the guest entrypoints and proves the docker step is outside it —
    i.e. NORMAL guest execution never reaches it. (The module still ships in the image; layer (b)
    below is what makes actuating it impossible regardless of imports.)"""
    closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)

    # Teeth: the walk is real and transitive (not a vacuous empty set) — it reaches modules the
    # entrypoints only import indirectly, including the proposal store and the request validator.
    assert len(closure) > len(GUEST_ENTRYPOINTS)
    assert "management/api/store.py" in closure
    assert "ralph_portable/replication_request.py" in closure
    assert "runner/loop.py" in closure

    # 1) No host-only docker file is anywhere in the guest-reachable closure.
    leaked = closure & HOST_ONLY_DOCKER_FILES
    assert not leaked, f"guest closure reaches the host-only docker step: {sorted(leaked)}"

    # 2) And no file in that closure imports or references the docker step by any channel.
    violations = forbidden_references(
        sorted(closure), import_prefixes=DOCKER_STEP_IMPORTS,
        names=DOCKER_STEP_NAMES, repo_root=REPO)
    assert not violations, (
        "a guest-reachable module reaches the host docker replica step: %s" % violations)


def test_guest_closure_excludes_the_host_owned_fleet_registry():
    """#4834 comms Phase 0 (test #9): guest-reachable code cannot import the HOST-OWNED topology
    registry writer. The registry holds the fleet port map + lineage + credential fingerprints and
    is host-authoritative — a guest/replica must not be able to write it or inject an endpoint.

    ``ralph_portable.fleet_registry`` / ``FleetRegistryStore`` are in FORBIDDEN_IMPORTS/NAMES, so a
    direct guest import is already caught by the checks above; this proves it stays out of the FULL
    transitive guest closure too (a registry import buried deep would also leak host authority)."""
    closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" not in closure, (
        "guest closure must not reach the host-owned fleet registry writer")
    violations = forbidden_references(
        sorted(closure), import_prefixes=("ralph_portable.fleet_registry",),
        names=("FleetRegistryStore",), repo_root=REPO)
    assert not violations, f"a guest-reachable module reaches the host fleet registry: {violations}"


def test_host_stack_is_the_registry_writer():
    """Positive control: the HOST replica stack DOES reach the fleet registry (it is the writer).

    Proves the exclusion above is meaningful — the registry writer exists and is reachable, just
    only from the host lifecycle path (stand-up/teardown/daemon), never from the guest closure."""
    host_closure = transitive_import_closure(
        ["ralph_portable/host_replica_stack.py"], repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" in host_closure, (
        "the host replica stack must reach the fleet registry (it writes it)")


def test_fleet_poller_is_host_only():
    """#4834 comms Phase 1: the host fleet /health poller is HOST-ONLY.

    Positive control: its import closure DOES reach the host-owned fleet registry (it reads live
    endpoints from it) — proving the exclusion is meaningful. And it must NOT appear in the guest
    import closure, so a guest/replica can never drive the poller (which reads the host registry)."""
    poller_closure = transitive_import_closure(
        ["scripts/host_fleet_poller.py"], repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" in poller_closure, (
        "the fleet poller must reach the host registry (it reads live endpoints from it)")

    guest_closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)
    assert "scripts/host_fleet_poller.py" not in guest_closure, (
        "the host-only fleet poller must not be reachable from the guest closure")


def test_fleet_view_projection_is_guest_safe_no_registry_import():
    """The guest-reachable fleet PROJECTION (management/api/fleet_view.py, served by /api/fleet)
    derives the fleet view from the parent journal and must NOT import the host-owned registry —
    that is what keeps the mgmt API on the guest side of the wall while still showing the fleet."""
    closure = transitive_import_closure(["management/api/fleet_view.py"], repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" not in closure
    violations = forbidden_references(
        sorted(closure), import_prefixes=("ralph_portable.fleet_registry",),
        names=("FleetRegistryStore",), repo_root=REPO)
    assert not violations, f"the fleet projection reaches the host registry: {violations}"


def test_daemon_is_on_the_host_side_of_the_wall():
    """Positive control: the daemon itself DOES import the docker step (it is the host executor).

    This proves the exclusion above is meaningful — the docker step exists and is reachable, just
    only from the HOST daemon/broker, never from the guest closure."""
    daemon_closure = transitive_import_closure(
        ["scripts/host_replication_daemon.py"], repo_root=REPO)
    assert "ralph_portable/host_replica_stack.py" in daemon_closure, (
        "the host daemon must reach the docker stand-up step (it is the host executor)")


def test_transitive_closure_walker_has_teeth(tmp_path):
    # entry -> mid -> leaf(imports the forbidden docker step). The walker must reach the leaf and
    # forbidden_references over the closure must flag it — proving a buried import is caught.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "entry.py").write_text("from pkg import mid\n")
    (tmp_path / "pkg" / "mid.py").write_text("from pkg import leaf\n")
    (tmp_path / "pkg" / "leaf.py").write_text(
        "from ralph_portable.host_replica_stack import stand_up_replica_stack\n")

    closure = transitive_import_closure(["entry.py"], repo_root=tmp_path)
    assert "pkg/leaf.py" in closure, "the walker must reach a leaf three hops deep"
    violations = forbidden_references(
        sorted(closure), import_prefixes=DOCKER_STEP_IMPORTS,
        names=DOCKER_STEP_NAMES, repo_root=tmp_path)
    assert any("host_replica_stack" in v for v in violations)


def test_workrequest_relay_is_host_only():
    """#4834 comms Phase 3: the host->replica work.request POST relay is HOST-ONLY.

    Positive control: its closure DOES reach the host-owned fleet registry (it resolves the target
    replica port ONLY from ``registry.live()``). And it must NOT appear in the guest import closure —
    a guest/replica can never import the relay (or the registry) to POST itself a work.request."""
    relay_closure = transitive_import_closure(
        ["scripts/host_workrequest_relay.py"], repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" in relay_closure, (
        "the work.request relay must reach the host registry (it reads the target port from it)")

    guest_closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)
    assert "scripts/host_workrequest_relay.py" not in guest_closure, (
        "the host-only work.request relay must not be reachable from the guest closure")


def test_tbagents_bridge_is_host_only():
    """#4834 comms Phase 5: the optional tbagents bridge is HOST-ONLY.

    Positive control: its import closure DOES reach the host-owned fleet registry (it reads live
    lineages from it), like the Phase-1/2 host relays. And it must NOT appear in the guest import
    closure — a guest/replica can never import or drive the bridge (which mirrors AQ into tbagents)."""
    bridge_closure = transitive_import_closure(
        ["scripts/host_tbagents_bridge.py"], repo_root=REPO)
    assert "ralph_portable/fleet_registry.py" in bridge_closure, (
        "the tbagents bridge must reach the host registry (it reads live lineages from it)")

    guest_closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)
    assert "scripts/host_tbagents_bridge.py" not in guest_closure, (
        "the host-only tbagents bridge must not be reachable from the guest closure")


def test_a2a_facade_modules_are_guest_safe_and_registry_free():
    """#4834 comms Phase 5: the /a2a façade is a GUEST-reachable translation layer (mounted in the
    management API). Its modules must NOT import the host-owned registry, the docker step, or any
    actuation surface — it only translates to the same typed queues (server-derived identity, ACL,
    payload validation) the native endpoints use."""
    for entry in ("management/api/a2a_translate.py", "management/api/a2a_router.py"):
        closure = transitive_import_closure([entry], repo_root=REPO)
        leaked = closure & (HOST_ONLY_DOCKER_FILES | {
            "scripts/host_workrequest_relay.py", "scripts/host_tbagents_bridge.py",
            "scripts/host_outbox_relay.py", "scripts/host_fleet_poller.py"})
        assert not leaked, f"{entry} reaches host-only code: {sorted(leaked)}"
        violations = forbidden_references(
            sorted(closure), import_prefixes=FORBIDDEN_IMPORTS, names=FORBIDDEN_NAMES, repo_root=REPO)
        assert not violations, f"{entry} reaches a forbidden actuation surface: {violations}"


def test_a2a_facade_is_in_the_guest_closure_but_not_host_code():
    """The A2A façade IS part of the guest management-API closure (it's mounted there when enabled),
    and that closure still excludes the host-only docker step + registry — proving the façade adds no
    host reach. Uses app.py as the guest entrypoint since it conditionally includes the router."""
    closure = transitive_import_closure(["management/api/app.py"], repo_root=REPO)
    assert "management/api/a2a_router.py" in closure, (
        "the management app must reach the a2a router module (it conditionally mounts it)")
    leaked = closure & (HOST_ONLY_DOCKER_FILES | {"scripts/host_tbagents_bridge.py"})
    assert not leaked, f"the guest app closure reaches host-only code via a2a: {sorted(leaked)}"


def test_phase3_guest_modules_are_registry_free():
    """#4834 comms Phase 3: the guest-side Phase-3 modules — the work.request schema/allowlist, the
    inbox projection, and the flag-gated importer — must NOT import the host-owned registry or the
    docker step. They run inside the replica container; a guest reaching host authority would break
    the wall."""
    for entry in ("management/api/comms_workrequest.py", "management/api/comms_inbox.py",
                  "management/api/comms_import.py"):
        closure = transitive_import_closure([entry], repo_root=REPO)
        leaked = closure & (HOST_ONLY_DOCKER_FILES | {"scripts/host_workrequest_relay.py"})
        assert not leaked, f"{entry} reaches host-only code: {sorted(leaked)}"
        violations = forbidden_references(
            sorted(closure), import_prefixes=FORBIDDEN_IMPORTS, names=FORBIDDEN_NAMES, repo_root=REPO)
        assert not violations, f"{entry} reaches a forbidden actuation surface: {violations}"


# --- RUNTIME-EXECUTE layer (b): the guest cannot ACTUATE docker even if a host module is imported ---
# The import-closure tests above prove the host docker step is off every NORMAL guest path, but the
# module physically ships in the image (wholesale COPY), so `import ralph_portable.host_replica_stack`
# would succeed inside a guest. What actually makes host actuation impossible is that the guest has
# neither the docker CLI nor the daemon socket. These tests assert that real, load-bearing property
# against the image build (container/Dockerfile) and the compose config (docker-compose.yml).

# Package/URL fragments that would pull a docker client into the image. A guest that installs any of
# these regains the ability to drive `docker`/`docker compose`, defeating layer (b).
DOCKER_CLI_INSTALL_TOKENS = (
    "docker.io", "docker-ce", "docker-ce-cli", "docker-cli", "docker-buildx",
    "containerd.io", "get.docker.com", "get-docker.sh",
)


def _dockerfile_instructions(text: str) -> list[str]:
    """Dockerfile instructions with comment lines dropped and backslash-continuations joined.

    Comments (``#`` lines) are excluded so a *documentation* mention of docker-compose does not read
    as an install; only real build instructions (RUN/COPY/ADD/…) are returned."""
    noncomment = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    joined = "\n".join(noncomment).replace("\\\n", " ")
    return [seg.strip() for seg in joined.split("\n") if seg.strip()]


def _instruction_installs_docker(instr: str) -> str | None:
    """Return the offending token if a single Dockerfile instruction would add a docker client, else
    None. Flags the explicit package/URL tokens anywhere, plus a bare ``docker`` word in a build step
    that fetches/copies software (RUN/COPY/ADD) — e.g. ``apt-get install ... docker``."""
    low = instr.lower()
    for tok in DOCKER_CLI_INSTALL_TOKENS:
        if tok in low:
            return tok
    if low.startswith(("run ", "copy ", "add ")) and re.search(r"\bdocker\b", low):
        return "docker"
    return None


def test_guest_image_has_no_docker_cli():
    """RUNTIME layer (b), part 1: the guest image installs no docker CLI. Without the client binary,
    imported host code has no ``docker``/``docker compose`` executable to invoke."""
    offenders = [
        (instr, tok)
        for instr in _dockerfile_instructions(CONTAINER_DOCKERFILE.read_text())
        if (tok := _instruction_installs_docker(instr))
    ]
    assert not offenders, (
        "container/Dockerfile installs a docker client — the guest could then actuate the host "
        f"docker daemon if a host module were imported: {offenders}")


def test_docker_cli_detector_has_teeth():
    # Positive control: a synthetic Dockerfile that DOES install docker must be flagged, and a comment
    # mentioning docker-compose must NOT be (so the real Dockerfile's comments don't false-positive).
    bad = (
        "FROM debian\n"
        "# we use docker-compose to build — this is only a comment\n"
        "RUN apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io\n"
    )
    instrs = _dockerfile_instructions(bad)
    assert not any(ln.lstrip().startswith("#") for ln in instrs), "comments must be stripped"
    assert any(_instruction_installs_docker(i) for i in instrs), "a docker-ce install must be flagged"

    ok = "FROM debian\n# docker-compose build.args are documented here\nRUN apt-get install -y curl git\n"
    assert not any(_instruction_installs_docker(i) for i in _dockerfile_instructions(ok))


def _service_docker_sock_mounts(service_cfg: dict) -> list:
    """Volume specs in a compose service that bind the docker daemon socket (empty == none).

    Handles both the short ``"src:dst[:mode]"`` string form and the long ``{source,target}`` dict."""
    hits = []
    for vol in (service_cfg or {}).get("volumes", []) or []:
        src = tgt = ""
        if isinstance(vol, str):
            parts = vol.split(":")
            src = parts[0]
            tgt = parts[1] if len(parts) > 1 else ""
        elif isinstance(vol, dict):
            src, tgt = str(vol.get("source", "")), str(vol.get("target", ""))
        if "docker.sock" in src or "docker.sock" in tgt:
            hits.append(vol)
    return hits


def test_guest_services_do_not_mount_docker_sock():
    """RUNTIME layer (b), part 2: no compose service binds ``/var/run/docker.sock`` into the guest.
    Without the daemon socket, even the docker CLI (which the image also lacks) would have nothing to
    talk to — host actuation from a guest is impossible regardless of what code gets imported.

    Checked for EVERY service, and the primary guest app service (``app``) is asserted present so this
    can never pass vacuously against a renamed/removed service."""
    services = yaml.safe_load(COMPOSE_FILE.read_text()).get("services", {})
    assert "app" in services, "docker-compose.yml must define the guest 'app' service"
    offenders = {
        name: mounts
        for name, cfg in services.items()
        if (mounts := _service_docker_sock_mounts(cfg))
    }
    assert not offenders, (
        f"a compose service mounts the docker daemon socket into the guest: {offenders}")


def test_docker_sock_detector_has_teeth():
    # Positive control: both the short and long volume forms of a docker.sock bind must be detected.
    assert _service_docker_sock_mounts(
        {"volumes": ["/var/run/docker.sock:/var/run/docker.sock:ro"]})
    assert _service_docker_sock_mounts(
        {"volumes": [{"type": "bind", "source": "/var/run/docker.sock",
                      "target": "/var/run/docker.sock"}]})
    # And a benign named-volume mount must NOT be flagged.
    assert not _service_docker_sock_mounts({"volumes": ["aq-workspace:/workspace"]})


@pytest.mark.skipif(
    os.environ.get("AQ_GUEST_RUNTIME_DOCKER_TEST") != "1",
    reason="SKIP LOUDLY: set AQ_GUEST_RUNTIME_DOCKER_TEST=1 and run INSIDE a guest container to "
           "assert at runtime that the docker CLI is absent from PATH and /var/run/docker.sock is "
           "not present. The default suite proves the same property statically from the "
           "Dockerfile + docker-compose.yml (tests above), so this live variant is opt-in.")
def test_guest_runtime_has_no_docker_actuation():
    """RUNTIME layer (b), live variant: run INSIDE a built guest container. Asserts the two means of
    host actuation are genuinely absent from the running guest, not just from the build config."""
    assert shutil.which("docker") is None, "the docker CLI must not be on PATH inside the guest"
    assert not os.path.exists(DOCKER_SOCK_PATH), (
        "the docker daemon socket must not be present/mounted inside the guest")
