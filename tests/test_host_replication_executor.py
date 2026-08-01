"""Task #4407 v9 — safe host-side replication copy executor + broker wiring."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ralph_portable.host_replication_executor import (
    allocate_instance_id,
    execute_replication_copy,
)

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "scripts" / "host_replication_broker.py"


def _src(tmp_path: Path) -> Path:
    src = tmp_path / "workspace"
    src.mkdir()
    (src / "instance.yaml").write_text("mission_id: m1\n")
    (src / "runner.txt").write_text("loop\n")
    return src


def test_allocate_instance_id_is_deterministic_and_safe() -> None:
    packet = {"requester_instance_id": "AQ/One", "mission_id": "Mission #1"}
    a = allocate_instance_id(packet)
    b = allocate_instance_id(packet)
    assert a == b
    assert a == "aq-one--mission-1--copy"
    assert "/" not in a and "#" not in a and " " not in a


def test_straight_copy_completes_and_materializes(tmp_path: Path) -> None:
    src = _src(tmp_path)
    packet = {
        "mode": "straight_copy",
        "mission_id": "m1",
        "requester_instance_id": "aq-1",
    }
    rec = execute_replication_copy(
        packet, status="auto_approved", source_workspace=src, dest_root=tmp_path / "instances"
    )
    assert rec["ok"] is True
    assert rec["status"] == "completed"
    assert rec["executed_by"] == "host"
    assert rec["used_docker_sock"] is False
    dest = Path(rec["created_path"])
    assert (dest / "instance.yaml").is_file()
    assert (dest / "runner.txt").is_file()
    manifest = json.loads((dest / "replication_manifest.json").read_text())
    assert manifest["mode"] == "straight_copy"
    assert manifest["used_docker_sock"] is False
    # straight_copy applies no overrides file
    assert not (dest / "instance_overrides.yaml").exists()


def test_copy_with_modifications_writes_overrides(tmp_path: Path) -> None:
    src = _src(tmp_path)
    packet = {
        "mode": "copy_with_modifications",
        "mission_id": "m2",
        "requester_instance_id": "aq-1",
        "modifications": {"cadence": "hourly", "max_attempts": 3},
    }
    rec = execute_replication_copy(
        packet, status="approved", source_workspace=src, dest_root=tmp_path / "instances"
    )
    assert rec["ok"] is True
    assert rec["applied_modifications"] == {"cadence": "hourly", "max_attempts": 3}
    dest = Path(rec["created_path"])
    overrides = (dest / "instance_overrides.yaml").read_text()
    assert "cadence: hourly" in overrides


def test_refuses_execution_without_approval(tmp_path: Path) -> None:
    src = _src(tmp_path)
    packet = {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "aq-1"}
    # awaiting_operator is not an approved gate — must not execute.
    with pytest.raises(ValueError):
        execute_replication_copy(
            packet,
            status="awaiting_operator",
            source_workspace=src,
            dest_root=tmp_path / "instances",
        )


def test_refuses_overwrite_by_default(tmp_path: Path) -> None:
    src = _src(tmp_path)
    packet = {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "aq-1"}
    kw = dict(status="auto_approved", source_workspace=src, dest_root=tmp_path / "instances")
    first = execute_replication_copy(packet, **kw)
    assert first["ok"] is True
    second = execute_replication_copy(packet, **kw)  # same deterministic id -> exists
    assert second["ok"] is False
    assert second["status"] == "failed"
    assert "exists" in second["error"]


def test_rejects_guest_docker_sock_request(tmp_path: Path) -> None:
    src = _src(tmp_path)
    packet = {
        "mode": "straight_copy",
        "mission_id": "m1",
        "requester_instance_id": "aq-1",
        "requires_docker_sock": True,
    }
    rec = execute_replication_copy(
        packet, status="auto_approved", source_workspace=src, dest_root=tmp_path / "instances"
    )
    assert rec["ok"] is False
    assert rec["status"] == "failed"


def test_broker_real_copy_via_override(tmp_path: Path) -> None:
    src = _src(tmp_path)
    req = tmp_path / "req.json"
    req.write_text(
        json.dumps(
            {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "aq-1"}
        )
    )
    dest_root = tmp_path / "instances"
    env = {"AQ_REPLICATION_AUTO_APPROVE": "1", "PATH": "/usr/bin:/bin"}
    out = subprocess.run(
        [
            sys.executable, str(BROKER),
            "--request-file", str(req),
            "--execute",
            "--source-workspace", str(src),
            "--dest-root", str(dest_root),
        ],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr
    payload = json.loads(out.stdout)
    assert payload["status"] == "completed"
    assert payload["mode_of_execution"] == "host_copy"
    assert Path(payload["created_path"]).is_dir()


def test_broker_awaits_operator_without_override(tmp_path: Path) -> None:
    req = tmp_path / "req.json"
    req.write_text(
        json.dumps(
            {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "aq-1"}
        )
    )
    env = {"PATH": "/usr/bin:/bin"}  # no OVERRIDE, no --operator-approve
    out = subprocess.run(
        [sys.executable, str(BROKER), "--request-file", str(req), "--execute"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert out.returncode == 3
    assert "awaiting_operator" in out.stdout
