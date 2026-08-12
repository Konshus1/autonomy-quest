"""DOCKER + MEMORY-gated end-to-end proof of replication Step 2 (#4834 copy_with_modifications).

Gated like the other real-infra tests (AQ_REPLICATION_DOCKER_TEST=1) AND, because a replica is a
full ~4-service stack, additionally gated on host free memory > 45% (psutil). If either gate is
unmet the test SKIPS LOUDLY rather than silently passing.

    AQ_REPLICATION_DOCKER_TEST=1 AQ_REPLICATION_MIN_FREE_MEM_PCT=10 \
        python3 -m pytest tests/test_replica_modifications_docker.py -q -s

It stands up a real ``aq-replica-<id>`` with an ALLOWLISTED config delta
(AQ_WORKFLOW_SELECTOR), proves the running replica REFLECTS it via /health/config, proves it is
still bounded (no docker.sock, distinct project), then tears it down (0 orphans) leaving the parent
untouched. It NEVER runs against the live loop — a fresh tmp state_root + its own aq-replica-* project.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

import pytest

from ralph_portable import host_replica_stack as hrs

pytestmark = pytest.mark.skipif(
    not os.environ.get("AQ_REPLICATION_DOCKER_TEST"),
    reason="set AQ_REPLICATION_DOCKER_TEST=1 to run the real docker replica stand-up/teardown",
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_PROJECT = os.environ.get("AQ_PARENT_PROJECT", "aq-seed-c2f4834")
_MIN_MEM_PCT_FOR_E2E = 45.0


def _require_memory_headroom() -> None:
    """Skip LOUDLY unless psutil reports > 45% free — a replica is heavy; do not thrash the host."""
    try:
        import psutil  # type: ignore
    except Exception:  # pragma: no cover - env-dependent
        pytest.skip("psutil unavailable — cannot confirm >45% free memory for a heavy replica e2e")
    vm = psutil.virtual_memory()
    free_pct = float(vm.available) / float(vm.total) * 100.0
    if free_pct <= _MIN_MEM_PCT_FOR_E2E:
        pytest.skip(
            f"SKIP LOUDLY: free memory {free_pct:.1f}% <= {_MIN_MEM_PCT_FOR_E2E}%; a replica is a "
            "full ~4-service stack — refusing to run the heavy e2e under memory pressure")


def _docker(args):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False).stdout


def _project_container_count(project: str) -> int:
    out = _docker(["ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    return len([x for x in out.splitlines() if x.strip()])


def _project_volume_count(project: str) -> int:
    out = _docker(["volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"])
    return len([x for x in out.splitlines() if x.strip()])


def test_copy_with_modifications_config_delta_reflected_then_torn_down(tmp_path):
    _require_memory_headroom()

    delta_value = "explore-e2e"
    packet = {
        "mode": "copy_with_modifications",
        "mission_id": "docker-e2e-mods",
        "requester_instance_id": "urn:uuid:test-requester",
        "modifications": {"config": {"AQ_WORKFLOW_SELECTOR": delta_value}},
    }
    parent_before = _project_container_count(PARENT_PROJECT)
    assert parent_before > 0, f"parent {PARENT_PROJECT} must be running for the untouched check"

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
        assert project and project.startswith("aq-replica-") and project != PARENT_PROJECT
        # the validated config delta was applied
        assert record["applied_config"] == {"AQ_WORKFLOW_SELECTOR": delta_value}

        # the RUNNING replica reflects the delta via /health/config (the observable proof)
        mgmt_port = record["ports"]["app_mgmt"]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{mgmt_port}/health/config", timeout=15
        ) as resp:
            cfg = json.loads(resp.read())
        assert cfg["instance_config"].get("AQ_WORKFLOW_SELECTOR") == delta_value, cfg
        # and it never exposes a secret/gate key — only the allowlist is echoed
        assert set(cfg["instance_config"]) <= set(cfg["allowlist"])
        for leaked in ("AQ_DB_OWNER_PASSWORD", "AQ_REPLICATION_AUTO_APPROVE", "AQ_GOVERNANCE_TOKEN"):
            assert leaked not in cfg["instance_config"]

        assert _project_container_count(project) > 0
    finally:
        if project:
            print("teardown:", hrs.teardown_replica(
                project, state_root=tmp_path / "replicas", repo_root=REPO_ROOT))

    # nothing orphaned; parent untouched
    assert _project_container_count(project) == 0
    assert _project_volume_count(project) == 0
    assert hrs.list_replica_projects() == []
    assert _project_container_count(PARENT_PROJECT) == parent_before, "parent stack must be untouched"
