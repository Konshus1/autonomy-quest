"""Unit tests for the host-only replica stack lifecycle (#4834 replication Step 1).

These never touch the docker daemon: the docker runner, the memory reader, the compose
runner and the health opener are all injected. The DOCKER-GATED end-to-end proof lives in
``test_replica_stack_docker.py`` (gated on AQ_REPLICATION_DOCKER_TEST)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from ralph_portable import host_replica_stack as hrs


def _fake_docker(mapping):
    """Return a docker runner that answers by matching the first arg (ps/volume/network)."""
    def run(args, *, check=True, timeout=None, env=None, cwd=None):
        key = args[0]
        return mapping.get(key, "")
    return run


# --- gate 2: bounded ---------------------------------------------------------
def test_cap_gate_refuses_at_capacity():
    ok, reason = hrs.check_cap_gate(replica_count=2, cap=2)
    assert not ok and "cap reached" in reason


def test_cap_gate_refuses_over_capacity():
    ok, _ = hrs.check_cap_gate(replica_count=5, cap=2)
    assert not ok


def test_cap_gate_allows_below_capacity():
    ok, _ = hrs.check_cap_gate(replica_count=1, cap=2)
    assert ok


def test_list_replica_projects_counts_only_replica_prefix_across_generations():
    # ps -a returns labels incl the parent + non-replica projects; only aq-replica-* count,
    # de-duplicated across a project's many containers.
    labels = "\n".join([
        "aq-seed-c2f4834", "autonomy-quest",
        "aq-replica-aaaa", "aq-replica-aaaa", "aq-replica-bbbb", "",
    ])
    docker = _fake_docker({"ps": labels})
    assert hrs.list_replica_projects(docker=docker) == ["aq-replica-aaaa", "aq-replica-bbbb"]
    assert hrs.count_live_replicas(docker=docker) == 2


def test_replica_cap_env_override():
    assert hrs.replica_cap({}) == hrs.DEFAULT_MAX_REPLICAS
    assert hrs.replica_cap({"AQ_REPLICATION_MAX_REPLICAS": "5"}) == 5
    assert hrs.replica_cap({"AQ_REPLICATION_MAX_REPLICAS": "junk"}) == hrs.DEFAULT_MAX_REPLICAS


# --- gate 3: resource-gated --------------------------------------------------
def test_mem_gate_refuses_below_threshold():
    ok, reason = hrs.check_mem_gate(free_pct=20.0, min_pct=35.0)
    assert not ok and "insufficient free memory" in reason


def test_mem_gate_allows_at_or_above_threshold():
    assert hrs.check_mem_gate(35.0, 35.0)[0]
    assert hrs.check_mem_gate(58.0, 35.0)[0]


def test_min_free_mem_env_override():
    assert hrs.min_free_mem_pct({}) == hrs.DEFAULT_MIN_FREE_MEM_PCT
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "15"}) == 15.0


def test_mem_reader_fails_closed_when_unmeasurable(monkeypatch):
    # No psutil, no /proc, vm_stat raises -> report 0.0 so the gate REFUSES (fail closed).
    monkeypatch.setitem(__import__("sys").modules, "psutil", None)
    monkeypatch.setattr(hrs.Path, "is_file", lambda self: False)
    monkeypatch.setattr(hrs.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no vm_stat")))
    assert hrs._default_mem_reader() == 0.0


# --- identity + ports: collision-free + traversal-safe -----------------------
def test_allocate_identity_is_prefixed_and_safe():
    project, instance_id, shortid = hrs.allocate_replica_identity({"requester_instance_id": "x"})
    assert project.startswith("aq-replica-")
    assert instance_id.startswith("urn:uuid:")
    # traversal-safe: the shortid component has no separators or dots
    assert "/" not in project and "\\" not in project and ".." not in project
    assert shortid.isalnum() and shortid == shortid.lower()


def test_allocate_identity_avoids_collisions():
    seen = set()
    for _ in range(50):
        project, _iid, _sid = hrs.allocate_replica_identity(existing=seen)
        assert project not in seen
        seen.add(project)


def test_allocate_identity_ignores_guest_controlled_traversal():
    # A malicious requester/mission cannot steer the project name into a traversal — the id is
    # fresh entropy, not derived from these fields.
    project, _iid, _sid = hrs.allocate_replica_identity(
        {"requester_instance_id": "../../etc", "mission_id": "../../../root"})
    assert project.startswith("aq-replica-")
    assert ".." not in project and "/" not in project


def test_probe_free_ports_distinct_and_excludes_parent():
    ports = hrs.probe_free_ports(4)
    assert len(ports) == 4 and len(set(ports)) == 4
    assert not (set(ports) & hrs._KNOWN_PARENT_HOST_PORTS)


def test_render_ports_overlay_uses_override_and_loopback():
    overlay = hrs.render_ports_overlay(hrs.ReplicaPorts(11111, 22222, 33333, 44444))
    # !override so the base file's fixed ports are REPLACED, not appended (collision-free).
    assert overlay.count("!override") == 3
    for p in (11111, 22222, 33333, 44444):
        assert f"127.0.0.1:{p}:" in overlay
    assert "0.0.0.0" not in overlay


def test_render_secret_file_has_all_required_keys():
    body = hrs.render_secret_file("urn:uuid:abc")
    for key in hrs._SECRET_KEYS:
        assert f"{key}=" in body
    assert "AQ_INSTANCE_ID=urn:uuid:abc" in body
    assert "AQ_GOVERNANCE_ADJUDICATOR=" in body


# --- stand-up: gates fire BEFORE any side effect -----------------------------
def _valid_packet():
    return {"mode": "straight_copy", "mission_id": "m1", "requester_instance_id": "req-1"}


def test_stand_up_refuses_on_cap_without_spinning_up(tmp_path):
    calls = []
    docker = _fake_docker({"ps": "aq-replica-a\naq-replica-b\n"})  # already at cap 2

    def compose_runner(*a, **k):  # must NOT be called
        calls.append((a, k))
        return subprocess.CompletedProcess([], 0)

    rec = hrs.stand_up_replica_stack(
        _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="deadbeef",
        state_root=tmp_path / "st", env={"AQ_REPLICATION_MAX_REPLICAS": "2"},
        docker=docker, mem_reader=lambda: 90.0, compose_runner=compose_runner,
        lock_path=tmp_path / "cap.lock")
    assert rec["status"] == "refused" and rec["gate"] == "cap"
    assert rec["used_docker_sock"] is False
    assert calls == [], "compose must not run when the cap gate refuses"
    assert not (tmp_path / "st").exists(), "no replica files when refused"


def test_stand_up_refuses_on_memory_without_spinning_up(tmp_path):
    calls = []
    docker = _fake_docker({"ps": ""})  # no existing replicas

    def compose_runner(*a, **k):
        calls.append(1)
        return subprocess.CompletedProcess([], 0)

    rec = hrs.stand_up_replica_stack(
        _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="deadbeef",
        state_root=tmp_path / "st", env={"AQ_REPLICATION_MIN_FREE_MEM_PCT": "35"},
        docker=docker, mem_reader=lambda: 12.0, compose_runner=compose_runner,
        lock_path=tmp_path / "cap.lock")
    assert rec["status"] == "refused" and rec["gate"] == "memory"
    assert calls == [], "compose must not run when the memory gate refuses"


def test_stand_up_refuses_invalid_request():
    rec = hrs.stand_up_replica_stack(
        {"mode": "straight_copy"}, status="auto_approved", repo_root=".",
        docker=_fake_docker({}), mem_reader=lambda: 90.0,
        compose_runner=lambda *a, **k: subprocess.CompletedProcess([], 0))
    assert rec["status"] == "refused" and rec["gate"] == "validate"


def test_stand_up_rejects_non_approved_status():
    with pytest.raises(ValueError):
        hrs.stand_up_replica_stack(
            _valid_packet(), status="awaiting_operator", repo_root=".",
            docker=_fake_docker({"ps": ""}), mem_reader=lambda: 90.0,
            compose_runner=lambda *a, **k: subprocess.CompletedProcess([], 0))


def test_stand_up_happy_path_materializes_and_records(tmp_path):
    captured = {}

    def compose_runner(compose_args, *, cwd, env, timeout=None):
        captured["args"] = compose_args
        captured["project"] = env["COMPOSE_PROJECT_NAME"]
        captured["git_sha"] = env["GIT_SHA"]
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    rec = hrs.stand_up_replica_stack(
        _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="cafef00d",
        state_root=tmp_path / "st", env={}, docker=_fake_docker({"ps": ""}),
        mem_reader=lambda: 90.0, compose_runner=compose_runner, do_health_check=False,
        lock_path=tmp_path / "cap.lock")

    assert rec["ok"] and rec["status"] == "executed"
    assert rec["project"].startswith("aq-replica-")
    assert rec["distinct_project"] is True and rec["used_docker_sock"] is False
    assert rec["git_sha"] == "cafef00d"
    assert set(rec["ports"]) == {"postgres", "governance", "app_ui", "app_mgmt"}
    # the compose invocation used the isolated project + overlay + build
    assert captured["project"] == rec["project"]
    assert "--build" in captured["args"] and "-f" in captured["args"]
    # files materialized: secret (0600), overlay, manifest
    rdir = Path(rec["state_dir"])
    assert (rdir / "compose-secrets").exists()
    assert oct((rdir / "compose-secrets").stat().st_mode)[-3:] == "600"
    assert (rdir / "ports.yml").exists()
    manifest = json.loads((rdir / "replica.json").read_text())
    assert manifest["status"] == "executed" and manifest["instance_id"] == rec["instance_id"]


def test_stand_up_failed_compose_tears_down(tmp_path):
    removed = {"count": 0}

    def docker(args, *, check=True, timeout=None, env=None, cwd=None):
        if args[0] == "ps":
            return ""  # no existing replicas / nothing to reap
        removed["count"] += 1
        return ""

    def compose_runner(compose_args, *, cwd, env, timeout=None):
        return subprocess.CompletedProcess(compose_args, 1, stdout="", stderr="boom")

    rec = hrs.stand_up_replica_stack(
        _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="x",
        state_root=tmp_path / "st", env={}, docker=docker, mem_reader=lambda: 90.0,
        compose_runner=compose_runner, do_health_check=False, lock_path=tmp_path / "cap.lock")
    assert rec["status"] == "failed" and "exited 1" in rec["error"]
    # files were cleaned up by the best-effort teardown
    assert not Path(rec["state_dir"]).exists()


# --- health assertion --------------------------------------------------------
def test_poll_health_ok_when_reachable_with_sha():
    body = json.dumps({"ok": True, "git_sha": "abc123def456"}).encode()
    res = hrs.poll_replica_health(9999, opener=lambda u: body, timeout_s=5, interval_s=0)
    assert res.ok and res.reachable and res.git_sha == "abc123def456"


def test_poll_health_fails_when_sha_missing():
    body = json.dumps({"ok": True, "git_sha": None}).encode()
    res = hrs.poll_replica_health(9999, opener=lambda u: body, timeout_s=0.2, interval_s=0.05)
    assert not res.ok and "git_sha missing" in res.reason


def test_poll_health_times_out_when_unreachable():
    def boom(u):
        raise ConnectionError("refused")
    res = hrs.poll_replica_health(9999, opener=boom, timeout_s=0.2, interval_s=0.05)
    assert not res.ok and "timed out" in res.reason


# --- teardown (gate 4): reversible + scoped ----------------------------------
def test_teardown_refuses_non_replica_project():
    rec = hrs.teardown_replica("aq-seed-c2f4834", docker=_fake_docker({}))
    assert not rec["ok"] and "non-replica" in rec["error"]


def test_teardown_reaps_by_label_and_removes_files(tmp_path):
    project = "aq-replica-dead00"
    rdir = hrs.replica_dir(project, state_root=tmp_path)
    rdir.mkdir(parents=True)
    (rdir / "compose-secrets").write_text("x")
    (rdir / "ports.yml").write_text("y")
    (rdir / "replica.json").write_text("{}")

    swept = {"rm": [], "vol": [], "net": []}

    def docker(args, *, check=True, timeout=None, env=None, cwd=None):
        if args[0] == "ps":
            return "cid1\ncid2\n"
        if args[0] == "volume" and args[1] == "ls":
            return "vol1\n"
        if args[0] == "network" and args[1] == "ls":
            return "net1\n"
        if args[0] == "rm":
            swept["rm"] = args[2:]
        if args[0] == "volume" and args[1] == "rm":
            swept["vol"] = args[3:]
        if args[0] == "network" and args[1] == "rm":
            swept["net"] = args[2:]
        return ""

    rec = hrs.teardown_replica(project, state_root=tmp_path, docker=docker)
    assert rec["ok"]
    assert rec["containers_removed"] == ["cid1", "cid2"]
    assert rec["volumes_removed"] == ["vol1"]
    assert rec["networks_removed"] == ["net1"]
    assert not rdir.exists(), "generated files must be removed (nothing orphaned)"
    assert swept["rm"] == ["cid1", "cid2"] and swept["vol"] == ["vol1"]


# --- TOCTOU cap lock (review note #2): flock serializes the count->stand-up ---
def test_cap_lock_path_default_and_override():
    # A FIXED host path so daemon + broker contend on the SAME lock, env-overridable for tests.
    assert hrs.default_cap_lock_path({"AQ_REPLICATION_LOCK_FILE": "/x/y.lock"}) == Path("/x/y.lock")
    p = hrs.default_cap_lock_path({"AQ_STATE_DIR": "/var/aq"})
    assert p == Path("/var/aq/.aq-replica-cap.lock")


def test_cap_lock_is_exclusive_across_fds(tmp_path):
    # Two independent open descriptions cannot both hold the lock: while one holds it, a
    # bounded acquire by the other times out (proves mutual exclusion under concurrency).
    lock = tmp_path / "cap.lock"
    with hrs.replica_cap_lock(lock):
        with pytest.raises(TimeoutError):
            with hrs.replica_cap_lock(lock, timeout_s=0.3, poll_s=0.02):
                pass
    # released now -> re-acquirable
    with hrs.replica_cap_lock(lock, timeout_s=0.3):
        pass


def test_concurrent_standups_cannot_exceed_cap(tmp_path):
    """Two concurrent stand-ups at cap-1 (1 existing, cap 2) -> exactly ONE succeeds.

    The stateful fake docker only reflects a new aq-replica-* AFTER compose_runner "creates"
    it; a compose_runner sleep forces the two threads to overlap. Without the flock BOTH would
    read count==1<2 and breach to 3. With the flock the second re-counts under the lock and
    the cap gate refuses it. This is the concurrency proof for review note #2."""
    import threading

    world_lock = threading.Lock()
    projects = {"aq-replica-seed00"}  # 1 existing; cap 2 -> exactly one slot left

    def docker(args, *, check=True, timeout=None, env=None, cwd=None):
        if args[0] == "ps":
            with world_lock:
                return "\n".join(sorted(projects)) + ("\n" if projects else "")
        return ""

    def compose_runner(compose_args, *, cwd, env, timeout=None):
        time.sleep(0.1)  # widen the race window
        with world_lock:
            projects.add(env["COMPOSE_PROJECT_NAME"])
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    results: list[dict] = []
    res_lock = threading.Lock()

    def worker():
        rec = hrs.stand_up_replica_stack(
            _valid_packet(), status="auto_approved", repo_root=tmp_path, git_sha="x",
            state_root=tmp_path / "st", env={"AQ_REPLICATION_MAX_REPLICAS": "2"},
            docker=docker, mem_reader=lambda: 90.0, compose_runner=compose_runner,
            do_health_check=False, lock_path=tmp_path / "cap.lock")
        with res_lock:
            results.append(rec)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    statuses = sorted(r["status"] for r in results)
    assert statuses == ["executed", "refused"], f"cap breached under concurrency: {results}"
    refused = next(r for r in results if r["status"] == "refused")
    assert refused["gate"] == "cap"
    # exactly cap projects exist (the 1 seed + the 1 winner); the loser created nothing
    assert len(projects) == 2


# --- Codex adversary #5: memory gate must fail CLOSED on non-finite values ----
def test_mem_gate_fails_closed_on_non_finite():
    """NaN comparisons are always False, so `free < min` would silently PASS on a NaN reading or a
    NaN threshold — opening a mandatory fail-closed gate exactly when the measurement is bad."""
    from math import nan, inf
    assert hrs.check_mem_gate(nan, 35.0)[0] is False
    assert hrs.check_mem_gate(inf, 35.0)[0] is False
    assert hrs.check_mem_gate(1.0, nan)[0] is False
    # the legit path is intact
    assert hrs.check_mem_gate(50.0, 35.0)[0] is True
    assert hrs.check_mem_gate(20.0, 35.0)[0] is False


def test_min_free_mem_pct_rejects_non_finite_threshold():
    """A NaN/inf/out-of-range threshold would disable the gate; fall back to the conservative default."""
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "NaN"}) == hrs.DEFAULT_MIN_FREE_MEM_PCT
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "inf"}) == hrs.DEFAULT_MIN_FREE_MEM_PCT
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "-5"}) == hrs.DEFAULT_MIN_FREE_MEM_PCT
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "150"}) == hrs.DEFAULT_MIN_FREE_MEM_PCT
    assert hrs.min_free_mem_pct({"AQ_REPLICATION_MIN_FREE_MEM_PCT": "40"}) == 40.0  # valid passes through
