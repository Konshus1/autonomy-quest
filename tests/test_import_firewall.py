"""The autonomous mission loop must never reach an ACTUATION surface — formal promotion, replication
execution, or fleet brokering. This uses the reusable import_firewall and extends the original
formal-only check to cover replication + the new fleet-registry too (one guard, all actuation).

The replication rows deserve special scrutiny: the module that stands up a REAL Docker replica stack
(``ralph_portable.host_replica_stack``) executes ``docker``/``docker compose`` against the host daemon.
It must live ONLY on the host (the broker). A guest — the in-container management API that serves
``POST /api/replication/propose``, and the mission loop — may only WRITE a proposal row; it must never
be able to import the docker stand-up/teardown. GUEST_REACHABLE below is the structural proof of exactly
that: ``management/api/app.py`` runs INSIDE the guest container, so if it (or the loop) ever imported the
docker step, this test fails in CI. This is the "host executes; guest never gets docker.sock" invariant."""
import pathlib

from ralph_portable.import_firewall import (
    forbidden_references,
    transitive_import_closure,
)

REPO = pathlib.Path(__file__).resolve().parent.parent

HOT_PATH = ["runner/loop.py", "runner/causal_sync.py", "ralph_portable/principle_mining.py"]

# Modules that RUN INSIDE the guest container. app.py serves the propose endpoint the guest hits; it
# must reach the request-validation surface only, never the host-only docker executor/stand-up.
GUEST_REACHABLE = HOT_PATH + ["management/api/app.py"]

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
    """The in-container API + loop must not import/reference the host-only docker replica step.

    This is the concrete "guest never gets docker.sock" proof: the guest writes a proposal row and
    stops; the HOST broker is the only path to ``docker compose up``."""
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
    """The FULL transitive import closure of the guest entrypoints must not contain — nor import,
    nor name — the host-only docker step (stand-up/teardown) or the auto-executor daemon.

    The original firewall checked only the 4 direct guest files. With a daemon that stands up REAL
    replicas hands-off, that is not enough: a docker import buried three hops deep inside something
    ``management/api/app.py`` imports would also give a guest docker.sock reach. This walks the
    entire in-repo import graph from the guest entrypoints and proves the docker step is outside it."""
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
