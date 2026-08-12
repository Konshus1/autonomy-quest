"""DOCKER-GATED end-to-end proof of replication Step 1 (#4834).

Gated exactly like the repo's other real-infra tests: set AQ_REPLICATION_DOCKER_TEST=1 to run.
It performs a FULL straight_copy: host stands up a real ``aq-replica-<id>`` compose stack, asserts
its /health is reachable + reports a SHA, verifies it is a DISTINCT project from the parent, then
tears it down and proves nothing is left (0 aq-replica-* containers/volumes), parent untouched.

    AQ_REPLICATION_DOCKER_TEST=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=10 \
        python3 -m pytest tests/test_replica_stack_docker.py -q -s

The lowered memory threshold is a SCOPED testing override (the committed default stays 35). Real
docker builds take minutes on a cold cache; the parent image is normally already built so it's fast.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from ralph_portable import host_replica_stack as hrs

pytestmark = pytest.mark.skipif(
    not os.environ.get("AQ_REPLICATION_DOCKER_TEST"),
    reason="set AQ_REPLICATION_DOCKER_TEST=1 to run the real docker replica stand-up/teardown",
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_PROJECT = os.environ.get("AQ_PARENT_PROJECT", "aq-seed-c2f4834")


def _docker(args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False).stdout


def _project_container_count(project: str) -> int:
    out = _docker(["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    return len([x for x in out.splitlines() if x.strip()])


def _project_volume_count(project: str) -> int:
    out = _docker(["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    return len([x for x in out.splitlines() if x.strip()])


def test_full_straight_copy_standup_and_teardown(tmp_path):
    packet = {
        "mode": "straight_copy",
        "mission_id": "docker-e2e",
        "requester_instance_id": "urn:uuid:test-requester",
    }
    parent_before = _project_container_count(PARENT_PROJECT)
    assert parent_before > 0, f"parent {PARENT_PROJECT} must be running for the untouched check"

    record = None
    project = None
    try:
        record = hrs.stand_up_replica_stack(
            packet,
            status="auto_approved",
            repo_root=REPO_ROOT,
            state_root=tmp_path / "replicas",
            health_timeout_s=float(os.environ.get("AQ_REPLICATION_HEALTH_TIMEOUT", "420")),
            build=True,
        )
        project = record.get("project")
        assert record["status"] == "executed", f"stand-up failed: {record}"
        assert record["ok"] and record["used_docker_sock"] is False
        # distinct project from the parent
        assert project and project.startswith("aq-replica-")
        assert project != PARENT_PROJECT and record["distinct_project"] is True
        # health asserted: reachable + a present SHA
        assert record["health"]["ok"] and record["health"]["reachable"]
        assert record["health"]["git_sha"], "replica /health must report a git_sha"
        # it really exists as its own stack
        assert _project_container_count(project) > 0
        assert _project_volume_count(project) > 0
    finally:
        if project:
            teardown = hrs.teardown_replica(project, state_root=tmp_path / "replicas",
                                            repo_root=REPO_ROOT)
            print("teardown:", teardown)

    # nothing orphaned: this replica fully gone
    assert _project_container_count(project) == 0, "replica containers must be gone after teardown"
    assert _project_volume_count(project) == 0, "replica volumes must be gone after teardown"
    # no aq-replica-* left at all, and the parent is untouched
    assert hrs.list_replica_projects() == []
    assert _project_container_count(PARENT_PROJECT) == parent_before, "parent stack must be untouched"
