"""DEPLOY-HYGIENE (#4834) — SHA visibility, cycle heartbeat, /health liveness.

Additive ops tooling. These tests pin the observable contract:
  * GIT_SHA flows build-arg -> ENV -> /health.
  * a completed cycle records a heartbeat; a DOWN loop does not.
  * a heartbeat-write failure is non-fatal (the loop is unaffected).
  * /health reports cycling true/false correctly by heartbeat age.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 1. SHA VISIBILITY: build-arg -> ENV -> file, and wired through compose.
# ---------------------------------------------------------------------------
def test_dockerfile_stamps_git_sha() -> None:
    text = (ROOT / "container/Dockerfile").read_text()
    assert "ARG GIT_SHA" in text
    assert "ENV AQ_GIT_SHA=$GIT_SHA" in text
    # The commit is also baked to a file for a fallback read path.
    assert "/app/GIT_SHA" in text


def test_compose_wires_git_sha_build_arg_into_every_built_service() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    built = [name for name, svc in compose["services"].items() if "build" in svc]
    # postgres is image-only; every OTHER service is built and must carry the build-arg.
    assert set(built) == {"migrate", "governance", "evaluator", "app"}
    for name in built:
        args = compose["services"][name]["build"].get("args") or {}
        assert args.get("GIT_SHA") == "${GIT_SHA:-}", f"{name} missing GIT_SHA build-arg"


# ---------------------------------------------------------------------------
# 2. MIGRATION: loop_heartbeat table + aq_loop INSERT/UPDATE grant.
# ---------------------------------------------------------------------------
def test_migration_creates_loop_heartbeat_and_grants_upsert() -> None:
    sql = (ROOT / "schema/028_loop_heartbeat.sql").read_text()
    lowered = sql.lower()
    assert "create table if not exists loop_heartbeat" in lowered
    assert "last_cycle_at" in lowered and "cycle_count" in lowered
    assert "singleton" in lowered  # single-row upsert key
    # aq_loop must keep INSERT *and* UPDATE (upsert), gated on the role existing.
    assert "grant select, insert, update on loop_heartbeat to aq_loop" in lowered
    assert "pg_roles where rolname = 'aq_loop'" in lowered


def test_999_keeps_loop_heartbeat_writable() -> None:
    # loop_heartbeat must NOT be revoked from the blanket loop grant (it is upsert, not append-only).
    grants = (ROOT / "schema/999_container_role_grants.sql").read_text()
    revoke_lines = [ln for ln in grants.splitlines()
                    if "REVOKE" in ln and "loop_heartbeat" in ln and not ln.strip().startswith("--")]
    assert revoke_lines == [], f"loop_heartbeat must stay writable, found revoke(s): {revoke_lines}"


# ---------------------------------------------------------------------------
# 3. HEARTBEAT WRITE: db.beat_cycle upserts; loop hook is fail-safe.
# ---------------------------------------------------------------------------
def test_beat_cycle_upserts_singleton() -> None:
    from runner.db import Db

    captured: list[str] = []

    class FakeSelf:
        def _q(self, sql, args=(), one=False):
            captured.append(sql)

    Db.beat_cycle(FakeSelf())
    assert len(captured) == 1
    sql = captured[0].lower()
    assert "insert into loop_heartbeat" in sql
    assert "on conflict(singleton) do update" in sql
    assert "cycle_count=loop_heartbeat.cycle_count+1" in sql.replace(" ", "")


def _loop_with(db):
    """Build a Loop around a fake db (no real DB, no real executor)."""
    from runner.loop import Loop
    from tests.test_loop_approval_execution import instance

    class FakeExecutor:  # never invoked by these tests
        pass

    return Loop(instance(), db, FakeExecutor())


def test_record_cycle_heartbeat_writes_once() -> None:
    class Db:
        def __init__(self):
            self.beats = 0

        def beat_cycle(self):
            self.beats += 1

    db = Db()
    loop = _loop_with(db)
    loop._record_cycle_heartbeat()
    assert db.beats == 1


def test_record_cycle_heartbeat_is_fail_safe() -> None:
    class Db:
        def beat_cycle(self):
            raise RuntimeError("db down mid-write")

    loop = _loop_with(Db())
    # A heartbeat-write failure must NEVER propagate — the loop is unaffected.
    loop._record_cycle_heartbeat()  # must not raise


# ---------------------------------------------------------------------------
# 4. LOOP WIRING: heartbeat on a completed cycle, NEVER on the crash path.
# ---------------------------------------------------------------------------
class _ForeverDb:
    """Minimal db for driving Loop.forever() to a clean exit."""

    url = "postgresql://invalid:invalid@127.0.0.1:1/x"

    def __init__(self):
        self.beats = 0

    def beat_cycle(self):
        self.beats += 1

    def beat(self, *a, **k):  # rate_limited / hibernating state writes
        pass

    def open_hibernation(self):
        return None


def _run_forever(monkeypatch, cycle_side_effects):
    """Run Loop.forever() with cycle() driven by a scripted list of side effects.

    Each element is either None (a completed cycle) or an exception instance to raise. A trailing
    BudgetExceeded cleanly exits forever(). The process-pulse thread's Db is stubbed out.
    """
    import runner.loop as loop_mod

    # Stub the pulse-thread Db so no real connection is attempted.
    class _PulseDb:
        def __init__(self, *a, **k):
            pass

        def beat_process(self, pid):
            pass

    monkeypatch.setattr(loop_mod, "Db", _PulseDb)

    db = _ForeverDb()
    loop = _loop_with(db)

    calls = {"i": 0}

    def fake_cycle():
        i = calls["i"]
        calls["i"] += 1
        effect = cycle_side_effects[i]
        if effect is not None:
            raise effect
        return None

    monkeypatch.setattr(loop, "cycle", fake_cycle)
    loop.forever(interval_s=0)
    return db


def test_forever_beats_after_a_completed_cycle(monkeypatch) -> None:
    from runner.budget import BudgetExceeded

    db = _run_forever(monkeypatch, [None, BudgetExceeded("stop")])
    # Exactly one completed cycle -> exactly one heartbeat (the BudgetExceeded exit does not beat).
    assert db.beats == 1


def test_forever_does_not_beat_on_crash_path(monkeypatch) -> None:
    from runner.budget import BudgetExceeded
    from runner.executor import AgentFailed

    db = _run_forever(monkeypatch, [AgentFailed("codex auth missing"), BudgetExceeded("stop")])
    # A crash-looping (DOWN) loop must record NO heartbeat -> /health reads not-cycling.
    assert db.beats == 0


# ---------------------------------------------------------------------------
# 5. /health: git_sha from env; cycling true/false/none by heartbeat age.
# ---------------------------------------------------------------------------
pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")
from fastapi.testclient import TestClient  # noqa: E402

import management.api.app as app_module  # noqa: E402

client = TestClient(app_module.app)


def test_health_reports_git_sha_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AQ_GIT_SHA", "deadbeefcafe")
    monkeypatch.setattr(app_module, "_loop_heartbeat_snapshot", lambda: None)
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["git_sha"] == "deadbeefcafe"
    assert body["merge_gate"] == "manager_gated"  # backward-compatible fields preserved


def test_health_cycling_true_when_fresh(monkeypatch) -> None:
    monkeypatch.setattr(
        app_module, "_loop_heartbeat_snapshot",
        lambda: {"last_cycle_at": "2026-08-11T00:00:00Z", "cycle_count": 5,
                 "seconds_since_last_cycle": 10.0})
    body = client.get("/health").json()
    assert body["cycling"] is True
    assert body["seconds_since_last_cycle"] == 10.0
    assert body["cycle_count"] == 5


def test_health_cycling_false_when_stale(monkeypatch) -> None:
    thr = app_module._CYCLING_THRESHOLD_S
    monkeypatch.setattr(
        app_module, "_loop_heartbeat_snapshot",
        lambda: {"last_cycle_at": "2026-08-10T00:00:00Z", "cycle_count": 5,
                 "seconds_since_last_cycle": float(thr + 1)})
    body = client.get("/health").json()
    assert body["cycling"] is False


def test_health_cycling_false_when_no_heartbeat(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_loop_heartbeat_snapshot", lambda: None)
    body = client.get("/health").json()
    assert body["cycling"] is False
    assert body["last_cycle_at"] is None
    assert body["seconds_since_last_cycle"] is None


def test_cycling_threshold_comfortably_exceeds_one_loop_interval() -> None:
    """REGRESSION (de-correlated review): threshold==interval read a HEALTHY loop as cycling:false.

    The heartbeat beats at cycle END, then the loop sleeps a full interval before the next cycle,
    which runs for T seconds before the next beat — so seconds_since_last_cycle peaks near
    interval + T each period. The threshold must exceed one interval by a real margin (>= 2x) or a
    healthy loop false-alarms during every execution window. Keyed off the loop's ACTUAL default
    interval so a change to either side can't silently reintroduce the bug.
    """
    import inspect

    from runner.loop import Loop

    default_interval = inspect.signature(Loop.forever).parameters["interval_s"].default
    assert isinstance(default_interval, int) and default_interval > 0
    assert app_module._CYCLING_THRESHOLD_S >= 2 * default_interval, (
        f"cycling threshold {app_module._CYCLING_THRESHOLD_S}s must be >= 2x the loop interval "
        f"{default_interval}s so a healthy loop never reads cycling:false mid-cycle"
    )


def test_health_cycling_true_across_a_full_interval_gap(monkeypatch) -> None:
    """A healthy loop mid-cycle: last beat was one full interval + some execution ago. With the
    fixed default threshold this MUST still read cycling:true (the false-alarm is closed)."""
    import inspect

    from runner.loop import Loop

    default_interval = inspect.signature(Loop.forever).parameters["interval_s"].default
    # Worst case within a period: a full inter-cycle sleep plus a slow cycle still running.
    worst_case_gap = float(default_interval) + 120.0
    monkeypatch.setattr(
        app_module, "_loop_heartbeat_snapshot",
        lambda: {"last_cycle_at": "2026-08-11T00:00:00Z", "cycle_count": 9,
                 "seconds_since_last_cycle": worst_case_gap})
    body = client.get("/health").json()
    assert body["cycling"] is True, (
        f"gap {worst_case_gap}s should be within threshold "
        f"{body['cycling_threshold_seconds']}s for a healthy loop")
