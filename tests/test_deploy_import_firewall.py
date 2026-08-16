"""W1a gated deploy pathway (task #6087) — the guest-never / host-executes import firewall.

Mirrors the self-replication firewall (``tests/test_import_firewall.py``): the HOST-ONLY deploy step
(the executor + the adapters that shell out to ``az``) must be OUTSIDE the guest import closure, so a
guest can only ever WRITE a deploy REQUEST — the deploy happens host-side or not at all, and the guest
never reaches the ``az`` shell-out or the credential it uses. The guest-reachable request module
(``deploy_request``) is PURE and must not reach the host step either.
"""
import pathlib

from ralph_portable.import_firewall import forbidden_references, transitive_import_closure

REPO = pathlib.Path(__file__).resolve().parent.parent

# The same guest entrypoints the replication firewall uses: the in-container mgmt API + the loop.
GUEST_ENTRYPOINTS = ["management/api/app.py", "runner/loop.py",
                     "runner/causal_sync.py", "ralph_portable/principle_mining.py"]

# The HOST-ONLY deploy step: the gated executor + the adapters (real ACA shells out to `az`).
HOST_ONLY_DEPLOY_FILES = {
    "ralph_portable/host_deploy_stack.py",
    "ralph_portable/deploy_adapters.py",
}
DEPLOY_STEP_IMPORTS = (
    "ralph_portable.host_deploy_stack",
    "ralph_portable.deploy_adapters",
)
DEPLOY_STEP_NAMES = ("execute_deploy", "rollback_deploy", "AcaDeployAdapter", "get_adapter")


def test_guest_closure_excludes_the_host_deploy_step() -> None:
    """The FULL transitive import closure of the guest entrypoints must not contain — nor import, nor
    name — the host-only deploy executor or the adapters. NORMAL guest execution never reaches them."""
    closure = transitive_import_closure(GUEST_ENTRYPOINTS, repo_root=REPO)

    # teeth: the walk is real and reaches indirect modules
    assert "runner/loop.py" in closure

    leaked = closure & HOST_ONLY_DEPLOY_FILES
    assert not leaked, f"guest closure reaches the host-only deploy step: {sorted(leaked)}"

    violations = forbidden_references(
        sorted(closure), import_prefixes=DEPLOY_STEP_IMPORTS,
        names=DEPLOY_STEP_NAMES, repo_root=REPO)
    assert not violations, f"a guest-reachable module reaches the host deploy step: {violations}"


def test_deploy_request_module_is_guest_safe() -> None:
    """The guest-reachable request module is PURE: its closure must not reach the host deploy step,
    the docker replica step, or any actuation surface — a guest importing it to shape-validate a
    proposal gains no deploy reach."""
    closure = transitive_import_closure(["ralph_portable/deploy_request.py"], repo_root=REPO)
    leaked = closure & (HOST_ONLY_DEPLOY_FILES | {"ralph_portable/host_replica_stack.py"})
    assert not leaked, f"deploy_request reaches host-only code: {sorted(leaked)}"
    violations = forbidden_references(
        sorted(closure), import_prefixes=DEPLOY_STEP_IMPORTS,
        names=DEPLOY_STEP_NAMES, repo_root=REPO)
    assert not violations, f"deploy_request reaches the host deploy step: {violations}"


def test_host_broker_is_on_the_host_side_of_the_wall() -> None:
    """Positive control: the host deploy broker DOES reach the deploy executor + adapters (it is the
    host executor). Proves the exclusion above is meaningful — the deploy step exists and is
    reachable, just only from the HOST broker, never from the guest closure."""
    broker_closure = transitive_import_closure(
        ["scripts/host_deploy_broker.py"], repo_root=REPO)
    assert "ralph_portable/host_deploy_stack.py" in broker_closure, (
        "the host deploy broker must reach the deploy executor (it is the host executor)")
    assert "ralph_portable/deploy_adapters.py" in broker_closure, (
        "the host deploy broker must reach the adapters (it constructs them)")


def test_host_deploy_stack_imports_the_adapters() -> None:
    """Positive control: the executor's own closure reaches the adapters (it deploys through them),
    confirming the host-side wiring is real."""
    closure = transitive_import_closure(["ralph_portable/host_deploy_stack.py"], repo_root=REPO)
    assert "ralph_portable/deploy_adapters.py" in closure
