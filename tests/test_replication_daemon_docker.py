"""DOCKER + MEMORY gated HANDS-OFF end-to-end proof of the auto-executor daemon (#4834 Step 1.5).

This is the "a guest proposes, a replica appears with NO human action" proof. Gated exactly like
the other real-infra tests (``AQ_REPLICATION_DOCKER_TEST=1``) AND additionally skipped unless the
host has >45% free memory — a replica is a heavy ~4-service stack, and spinning one up on a loaded
host is exactly the pressure the memory gate exists to avoid. When it skips, it says so loudly.

    AQ_REPLICATION_DOCKER_TEST=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=10 \
        python3 -m pytest tests/test_replication_daemon_docker.py -q -s

What it proves, hands-off:
  * seed ONE approved straight_copy proposal into an isolated in-memory store (NOT the live loop),
  * run the daemon ONCE (zero manual broker) -> it stands up aq-replica-<id> automatically,
  * the replica's /health is reachable + reports a git_sha, the project is DISTINCT, parent untouched,
  * a second daemon poll is idempotent (no second stand-up),
  * teardown leaves 0 aq-replica-* (nothing orphaned).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from management.api.store import InMemoryStore  # noqa: E402
from ralph_portable import host_replica_stack as hrs  # noqa: E402

_MIN_FREE_MEM_FOR_E2E = 45.0

_free = hrs.host_free_mem_pct()
pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("AQ_REPLICATION_DOCKER_TEST"),
        reason="set AQ_REPLICATION_DOCKER_TEST=1 to run the hands-off daemon end-to-end"),
    pytest.mark.skipif(
        _free < _MIN_FREE_MEM_FOR_E2E,
        reason=f"host free memory {_free:.1f}% < {_MIN_FREE_MEM_FOR_E2E:.0f}% — a replica is a heavy "
               "~4-service stack; DEFERRING the hands-off e2e (unit/gate/lock/firewall proofs cover "
               "the logic without docker)"),
]

PARENT_PROJECT = os.environ.get("AQ_PARENT_PROJECT", "aq-seed-c2f4834")


def _load_daemon():
    spec = importlib.util.spec_from_file_location(
        "host_replication_daemon", str(REPO / "scripts" / "host_replication_daemon.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _docker(args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False).stdout


def _project_container_count(project: str) -> int:
    out = _docker(["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    return len([x for x in out.splitlines() if x.strip()])


def test_hands_off_daemon_stands_up_and_tears_down(tmp_path):
    daemon = _load_daemon()

    # An ISOLATED store + proposal — never the live loop's proposals.
    store = InMemoryStore()
    store.add_replication({
        "mode": "straight_copy", "mission_id": "daemon-e2e",
        "requester_instance_id": "urn:uuid:daemon-e2e", "status": "auto_approved",
        "executed_by": None,
    })

    parent_before = _project_container_count(PARENT_PROJECT)

    project = None
    try:
        summary = daemon.run_once(
            store, repo_root=str(REPO), env=dict(os.environ),
            state_root=tmp_path / "replicas", ledger_path=tmp_path / "ledger.json",
            lock_path=tmp_path / "cap.lock", build=True, do_health_check=True,
            health_timeout_s=float(os.environ.get("AQ_REPLICATION_HEALTH_TIMEOUT", "420")),
        )
        assert summary["executed"] == 1, f"daemon did not stand up the replica hands-off: {summary}"
        project = summary["results"][0]["project"]
        assert project and project.startswith("aq-replica-")
        assert project != PARENT_PROJECT, "replica must be a DISTINCT compose project"
        assert _project_container_count(project) > 0, "the replica stack must really exist"

        # Idempotent: a second poll stands nothing up (the ledger already has it).
        again = daemon.run_once(
            store, repo_root=str(REPO), env=dict(os.environ),
            state_root=tmp_path / "replicas", ledger_path=tmp_path / "ledger.json",
            lock_path=tmp_path / "cap.lock", build=False, do_health_check=False)
        assert again["executed"] == 0 and again["pending"] == 0, f"not idempotent: {again}"
    finally:
        if project:
            print("teardown:", hrs.teardown_replica(
                project, state_root=tmp_path / "replicas", repo_root=str(REPO)))

    # Nothing orphaned; the parent is untouched.
    assert _project_container_count(project) == 0, "replica must be fully gone after teardown"
    assert hrs.list_replica_projects() == [], "no aq-replica-* may remain"
    if parent_before:
        assert _project_container_count(PARENT_PROJECT) == parent_before, "parent must be untouched"
