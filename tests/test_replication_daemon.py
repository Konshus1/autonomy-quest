"""Unit tests for the host-side replication auto-executor daemon (#4834 Step 1.5).

These never touch the docker daemon: the daemon's ``run_once`` calls ``stand_up_replica_stack``
with the docker runner / memory reader / compose runner injected (a stateful in-process fake),
so the FLOOD-is-bounded and daemon-drives-the-real-gate proofs run without any container. The
DOCKER-gated + memory-gated hands-off end-to-end proof lives in
``test_replication_daemon_docker.py`` (gated, skips cleanly on low resources)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from management.api.store import InMemoryStore  # noqa: E402
from ralph_portable import host_replica_stack as hrs  # noqa: E402


def _load_daemon():
    """Load the host-only daemon module from scripts/ by path (it is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "host_replication_daemon", str(REPO / "scripts" / "host_replication_daemon.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


daemon = _load_daemon()


# --- a stateful in-process fake "docker world" -------------------------------
class DockerWorld:
    """A fake docker daemon: aq-replica-* projects only appear AFTER compose "creates" them.

    Makes ``list_replica_projects`` reflect real state so the cap gate in
    ``stand_up_replica_stack`` genuinely bounds a flood, and the flock genuinely serializes
    concurrent stand-ups."""

    def __init__(self, initial=()):
        self.projects = set(initial)
        self._lock = threading.Lock()
        self.creates = 0

    def docker(self, args, *, check=True, timeout=None, env=None, cwd=None):
        if args[0] == "ps":
            with self._lock:
                return "\n".join(sorted(self.projects)) + ("\n" if self.projects else "")
        return ""

    def compose_runner(self, compose_args, *, cwd, env, timeout=None):
        time.sleep(0.02)  # widen the race window a touch
        with self._lock:
            self.projects.add(env["COMPOSE_PROJECT_NAME"])
            self.creates += 1
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")


def _seed(store, n, *, mode="straight_copy", status="auto_approved", mission_prefix="m"):
    for i in range(n):
        store.add_replication({
            "mode": mode, "mission_id": f"{mission_prefix}{i}",
            "requester_instance_id": f"urn:uuid:req-{i}",
            "status": status, "executed_by": None,
        })


def _run_once(store, world, tmp_path, **over):
    kwargs = dict(
        repo_root=tmp_path, env={"AQ_REPLICATION_MAX_REPLICAS": "2"},
        state_root=tmp_path / "st", ledger_path=tmp_path / "ledger.json",
        lock_path=tmp_path / "cap.lock", build=False, do_health_check=False,
        docker=world.docker, mem_reader=lambda: 90.0, compose_runner=world.compose_runner,
    )
    kwargs.update(over)
    return daemon.run_once(store, **kwargs)


# --- double opt-in guard -----------------------------------------------------
def test_daemon_off_by_default():
    ok, _ = daemon.daemon_enabled({})
    assert not ok


def test_daemon_needs_both_opt_ins():
    assert not daemon.daemon_enabled({"AQ_REPLICATION_DAEMON_ENABLE": "1"})[0]
    assert not daemon.daemon_enabled({"AQ_REPLICATION_AUTO_APPROVE": "1"})[0]
    ok, reason = daemon.daemon_enabled(
        {"AQ_REPLICATION_DAEMON_ENABLE": "1", "AQ_REPLICATION_AUTO_APPROVE": "1"})
    assert ok and "enabled" in reason


def test_cli_refuses_without_opt_in(monkeypatch):
    monkeypatch.delenv("AQ_REPLICATION_DAEMON_ENABLE", raising=False)
    monkeypatch.delenv("AQ_REPLICATION_AUTO_APPROVE", raising=False)
    assert daemon.main(["--once"]) == 3  # never reaches docker


# --- selection: only approved straight_copy, not-yet-executed ----------------
def test_selects_only_executable_proposals():
    recs = [
        {"mode": "straight_copy", "mission_id": "a", "requester_instance_id": "r", "status": "auto_approved"},
        {"mode": "straight_copy", "mission_id": "b", "requester_instance_id": "r", "status": "awaiting_operator"},
        {"mode": "straight_copy", "mission_id": "c", "requester_instance_id": "r", "status": "rejected"},
        {"mode": "copy_with_modifications", "mission_id": "d", "requester_instance_id": "r",
         "status": "auto_approved", "modifications": {"x": 1}},
        {"mode": "straight_copy", "mission_id": "e", "requester_instance_id": "r", "status": "approved"},
    ]
    pending = daemon.select_pending(recs, executed_ids=set())
    missions = {r["mission_id"] for _pid, r in pending}
    assert missions == {"a", "e"}, "only approved/auto_approved straight_copy are executable"


def test_ledger_makes_selection_idempotent():
    recs = [{"mode": "straight_copy", "mission_id": "a", "requester_instance_id": "r",
             "status": "auto_approved"}]
    pending = daemon.select_pending(recs, executed_ids=set())
    assert len(pending) == 1
    pid = pending[0][0]
    assert daemon.select_pending(recs, executed_ids={pid}) == []


# --- hands-off drain (fake docker) -------------------------------------------
def test_run_once_stands_up_pending_proposal(tmp_path):
    store = InMemoryStore()
    _seed(store, 1)
    world = DockerWorld()
    summary = _run_once(store, world, tmp_path)
    assert summary["executed"] == 1 and summary["refused"] == 0 and summary["failed"] == 0
    assert world.creates == 1
    assert len(world.projects) == 1 and next(iter(world.projects)).startswith("aq-replica-")


def test_run_once_is_idempotent_across_polls(tmp_path):
    store = InMemoryStore()
    _seed(store, 1)
    world = DockerWorld()
    first = _run_once(store, world, tmp_path)
    assert first["executed"] == 1
    # Second poll: the ledger already has it -> nothing pending, no second stand-up.
    second = _run_once(store, world, tmp_path)
    assert second["pending"] == 0 and second["executed"] == 0
    assert world.creates == 1, "a proposal must never be stood up twice"


def test_run_once_ignores_unapproved_and_wrong_mode(tmp_path):
    store = InMemoryStore()
    _seed(store, 1, status="awaiting_operator")
    _seed(store, 1, mode="copy_with_modifications", status="auto_approved", mission_prefix="mod")
    world = DockerWorld()
    summary = _run_once(store, world, tmp_path)
    assert summary["pending"] == 0 and summary["executed"] == 0 and world.creates == 0


# --- FLOOD is bounded by the cap (through the REAL gate) ----------------------
def test_flood_of_proposals_is_bounded_by_cap(tmp_path):
    store = InMemoryStore()
    _seed(store, 5)  # 5 approved straight_copy, cap is 2
    world = DockerWorld()
    summary = _run_once(store, world, tmp_path)
    assert summary["executed"] == 2, f"flood must be bounded to cap: {summary}"
    assert summary["refused"] == 3
    assert all(r["gate"] == "cap" for r in summary["results"] if r["status"] == "refused")
    assert len(world.projects) == 2, "exactly cap replicas exist after a flood"
    # A later poll (still at cap) executes nothing more — the refused rows stay pending, not lost.
    again = _run_once(store, world, tmp_path)
    assert again["executed"] == 0 and again["refused"] == 3 and world.creates == 2


def test_memory_gate_still_fail_closed_via_daemon(tmp_path):
    store = InMemoryStore()
    _seed(store, 1)
    world = DockerWorld()
    summary = _run_once(store, world, tmp_path, mem_reader=lambda: 5.0,
                        env={"AQ_REPLICATION_MIN_FREE_MEM_PCT": "35"})
    assert summary["executed"] == 0 and summary["refused"] == 1
    assert summary["results"][0]["gate"] == "memory"
    assert world.creates == 0, "the memory gate must refuse before any compose"


# --- concurrency: two daemon polls cannot breach the cap ---------------------
def test_two_concurrent_polls_cannot_exceed_cap(tmp_path):
    # Two independent stores each holding a distinct pending proposal, one shared docker world
    # + one shared cap lock. Start at cap-1 (1 existing) so only ONE slot remains.
    world = DockerWorld(initial={"aq-replica-seed00"})
    results = []
    rlock = threading.Lock()

    def poll(idx):
        store = InMemoryStore()
        store.add_replication({"mode": "straight_copy", "mission_id": f"c{idx}",
                               "requester_instance_id": f"urn:uuid:{idx}", "status": "auto_approved"})
        summary = _run_once(store, world, tmp_path,
                            ledger_path=tmp_path / f"ledger-{idx}.json")
        with rlock:
            results.append(summary)

    threads = [threading.Thread(target=poll, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    executed = sum(s["executed"] for s in results)
    refused = sum(s["refused"] for s in results)
    assert executed == 1 and refused == 1, f"cap breached under concurrent polls: {results}"
    assert len(world.projects) == 2  # the seed + exactly one winner


# --- interval loop stops on request ------------------------------------------
def test_run_forever_honors_should_stop(tmp_path):
    store = InMemoryStore()
    _seed(store, 1)
    world = DockerWorld()
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1  # allow exactly one poll

    totals = daemon.run_forever(
        lambda: store, interval_s=0.0, should_stop=should_stop,
        repo_root=tmp_path, env={"AQ_REPLICATION_MAX_REPLICAS": "2"},
        state_root=tmp_path / "st", ledger_path=tmp_path / "ledger.json",
        lock_path=tmp_path / "cap.lock", build=False, do_health_check=False,
        docker=world.docker, mem_reader=lambda: 90.0, compose_runner=world.compose_runner,
    )
    assert totals["polls"] == 1 and totals["executed"] == 1
