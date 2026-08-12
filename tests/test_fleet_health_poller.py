"""Host fleet /health poller tests (#4834 comms Phase 1, design §10 Phase 1).

Pure observability: the host observes replicas and writes host_observed health.observed events to
the parent journal + updates topology health. Proves (design §10 Phase 1 test list):
  * two fake replicas with distinct ports/IDs -> two observed health events;
  * timeout / refusal / version-mismatch -> correct down / stale + failure reason;
  * restart/reconciliation (registry reopened) -> health resumes, watermark preserved;
  * monotonic status events (ULID ascending);
  * stale-threshold transition (live -> stale -> down as the healthy watermark ages);
  * the poller IGNORES arbitrary proposal-row ports (only the host-registry endpoints);
  * the poller can hit ONLY loopback /health, never a write/replication/actuation route;
  * bounded concurrency;
  * a real-socket end-to-end poll (genuine TCP, not a fake opener).

Import-firewall proof that the poller is host-only lives in tests/test_import_firewall.py.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from management.api.store import InMemoryStore  # noqa: E402
from ralph_portable import fleet_registry as fr  # noqa: E402


def _load_poller():
    """Load the host-only poller from scripts/ by path (it is not a package). Registered in
    sys.modules so its dataclasses resolve their annotations."""
    spec = importlib.util.spec_from_file_location(
        "host_fleet_poller", str(REPO / "scripts" / "host_fleet_poller.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


poller = _load_poller()


# --- a fake /health world keyed by loopback port -----------------------------
class FakeFleet:
    """Maps a loopback port -> a canned /health outcome. Records every URL the poller requested,
    so a test can prove the poller polls ONLY the registry endpoints and ONLY /health."""

    def __init__(self, by_port):
        self.by_port = by_port
        self.requested_urls = []

    def opener(self, url, *, timeout_s):
        self.requested_urls.append(url)
        from urllib.parse import urlparse

        port = urlparse(url).port
        outcome = self.by_port.get(port)
        if outcome is None or outcome == "refused":
            raise ConnectionRefusedError(f"connection refused on {port}")
        if outcome == "timeout":
            raise TimeoutError("timed out")
        return json.dumps(outcome).encode()


def _register(reg, instance_id, project, port, *, git_sha="cafe", parent="urn:uuid:parent"):
    reg.upsert_standup(
        {"instance_id": instance_id, "project": project, "git_sha": git_sha,
         "requester_instance_id": parent,
         "ports": {"postgres": port - 3, "governance": port - 2, "app_ui": port - 1, "app_mgmt": port}},
        lifecycle=fr.LIFECYCLE_LIVE, credential_secret="s3cr3t")


def _ok_health(git_sha="cafe", cycling=True):
    return {"ok": True, "git_sha": git_sha, "cycling": cycling, "seconds_since_last_cycle": 12.0}


# --- two replicas -> two observed events -------------------------------------
def test_two_fake_replicas_produce_observed_health_events(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104, git_sha="cafe")
    _register(reg, "urn:uuid:b", "aq-replica-b", 5204, git_sha="beef")
    store = InMemoryStore()
    world = FakeFleet({5104: _ok_health("cafe"), 5204: _ok_health("beef")})

    summary = poller.run_once(reg, store, opener=world.opener)

    assert summary["polled"] == 2 and summary["live"] == 2 and summary["events"] == 2
    envs = store.envelopes()
    assert len(envs) == 2
    # Every event is HOST-OBSERVED ground truth (never a replica claim), on the right instance.
    for env in envs:
        assert env["kind"] == "health.observed"
        assert env["trust"] == "host_observed"
        assert env["principal_id"] == "host:fleet-poller"
    ids = {e["target"]["instance_id"] for e in envs}
    assert ids == {"urn:uuid:a", "urn:uuid:b"}
    # Registry topology health updated to live for both.
    assert reg.get("urn:uuid:a")["health"]["state"] == fr.HEALTH_LIVE
    assert reg.get("urn:uuid:b")["health"]["state"] == fr.HEALTH_LIVE


# --- failure modes -> correct state + reason ---------------------------------
def test_refusal_with_no_prior_success_is_down(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    world = FakeFleet({5104: "refused"})

    summary = poller.run_once(reg, store, opener=world.opener)
    assert summary["down"] == 1
    payload = store.envelopes()[0]["payload"]
    assert payload["health_state"] == "down"
    assert payload["reachable"] is False
    assert payload["http_result"] == "refused"
    assert "unreachable" in payload["reason"]


def test_timeout_is_reported_as_timeout(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    world = FakeFleet({5104: "timeout"})
    poller.run_once(reg, store, opener=world.opener)
    assert store.envelopes()[0]["payload"]["http_result"] == "timeout"


def test_version_mismatch_is_stale_with_reason(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104, git_sha="EXPECTED_SHA")
    store = InMemoryStore()
    world = FakeFleet({5104: _ok_health("DIFFERENT_SHA")})  # reachable but the wrong build

    summary = poller.run_once(reg, store, opener=world.opener)
    assert summary["stale"] == 1
    payload = store.envelopes()[0]["payload"]
    assert payload["health_state"] == "stale"
    assert payload["reachable"] is True  # reachable, just the wrong version
    assert "version mismatch" in payload["reason"]
    assert payload["observed_git_sha"] == "DIFFERENT_SHA"


# --- monotonic events --------------------------------------------------------
def test_events_are_monotonic_across_polls(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    world = FakeFleet({5104: _ok_health()})
    for _ in range(4):
        poller.run_once(reg, store, opener=world.opener)
        time.sleep(0.002)
    ids = [e["id"] for e in store.envelopes()]
    assert ids == sorted(ids), "ULID event ids must be monotonically ascending"
    assert len(ids) == 4


# --- stale-threshold transition ----------------------------------------------
def test_stale_then_down_transition_by_threshold(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def iso(dt):
        return dt.isoformat()

    # Poll 1: healthy -> live, watermark set at t0.
    ok = FakeFleet({5104: _ok_health()})
    poller.run_once(reg, store, opener=ok.opener, now=iso(t0), stale_threshold_s=90)
    assert reg.get("urn:uuid:a")["health"]["state"] == fr.HEALTH_LIVE

    # Poll 2: now unreachable, +30s (within threshold) -> stale (recently alive).
    down = FakeFleet({5104: "refused"})
    poller.run_once(reg, store, opener=down.opener, now=iso(t0 + timedelta(seconds=30)),
                    stale_threshold_s=90)
    assert reg.get("urn:uuid:a")["health"]["state"] == fr.HEALTH_STALE

    # Poll 3: still unreachable, +200s (beyond threshold from the last healthy poll) -> down.
    poller.run_once(reg, store, opener=down.opener, now=iso(t0 + timedelta(seconds=200)),
                    stale_threshold_s=90)
    assert reg.get("urn:uuid:a")["health"]["state"] == fr.HEALTH_DOWN
    # The watermark did NOT advance on the failed polls (last healthy is still t0).
    assert reg.get("urn:uuid:a")["health"]["last_successful_poll"] == iso(t0)


# --- restart / reconciliation -> health resumes ------------------------------
def test_health_resumes_after_registry_reopen(tmp_path):
    reg_path = tmp_path / "reg.json"
    reg = fr.FleetRegistryStore(reg_path)
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    world = FakeFleet({5104: _ok_health()})
    poller.run_once(reg, store, opener=world.opener)
    watermark = reg.get("urn:uuid:a")["health"]["last_successful_poll"]
    assert watermark

    # Simulate a host/broker restart: a brand-new store handle reads the SAME persisted registry.
    reg2 = fr.FleetRegistryStore(reg_path)
    assert reg2.last_successful_poll("urn:uuid:a") == watermark  # watermark survived
    poller.run_once(reg2, store, opener=world.opener)
    assert reg2.get("urn:uuid:a")["health"]["state"] == fr.HEALTH_LIVE
    assert reg2.get("urn:uuid:a")["health"]["poll_count"] == 2  # resumed, count advanced


# --- SSRF / route safety -----------------------------------------------------
def test_poller_polls_only_registry_ports_ignoring_proposal_rows(tmp_path):
    """The poller reads endpoints ONLY from the host registry; a bogus port smuggled into a
    guest-writable proposal row is never contacted (SSRF-safe: no arbitrary scanning)."""
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    # A guest proposes a replication with an attacker-chosen port; the poller must ignore it.
    store.add_replication({"mode": "straight_copy", "mission_id": "m",
                           "requester_instance_id": "urn:uuid:x",
                           "ports": {"app_mgmt": 6666}, "status": "approved"})
    world = FakeFleet({5104: _ok_health(), 6666: _ok_health()})

    poller.run_once(reg, store, opener=world.opener)
    ports_hit = {int(u.rsplit(":", 1)[1].split("/")[0]) for u in world.requested_urls}
    assert ports_hit == {5104}, f"poller must hit only the registry port, hit {ports_hit}"


def test_poller_only_ever_requests_loopback_health(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    _register(reg, "urn:uuid:a", "aq-replica-a", 5104)
    store = InMemoryStore()
    world = FakeFleet({5104: _ok_health()})
    poller.run_once(reg, store, opener=world.opener)
    for url in world.requested_urls:
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("/health"), f"poller requested a non-/health route: {url}"


def test_route_guard_rejects_non_health_and_non_loopback():
    # Belt-and-suspenders guard: even a hand-crafted URL cannot reach a write/actuation route.
    for bad in ["http://127.0.0.1:9/api/replication/propose",
                "http://127.0.0.1:9/health?x=1",
                "http://evil.example/health",
                "https://127.0.0.1:9/health"]:
        with pytest.raises(ValueError):
            poller._assert_loopback_health_url(bad)
    poller._assert_loopback_health_url("http://127.0.0.1:5104/health")  # the only allowed shape


def test_poll_one_ignores_entry_health_url_uses_registry_port(tmp_path):
    """poll_one builds the URL from the registry app_mgmt port ONLY — a manipulated health_url in
    the entry is not used as the request target."""
    world = FakeFleet({5104: _ok_health()})
    entry = {"instance_id": "urn:uuid:a", "ports": {"app_mgmt": 5104},
             "health_url": "http://10.0.0.9:80/admin"}  # must be ignored
    result = poller.poll_one(entry, opener=world.opener)
    assert result.http_result == "ok"
    assert world.requested_urls == ["http://127.0.0.1:5104/health"]


# --- bounded concurrency -----------------------------------------------------
def test_bounded_concurrency(tmp_path):
    reg = fr.FleetRegistryStore(tmp_path / "reg.json")
    for i in range(8):
        _register(reg, f"urn:uuid:{i}", f"aq-replica-{i}", 5100 + i * 10)
    store = InMemoryStore()

    in_flight = {"cur": 0, "max": 0}
    lock = threading.Lock()

    def slow_opener(url, *, timeout_s):
        with lock:
            in_flight["cur"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["cur"])
        try:
            time.sleep(0.05)
            return json.dumps(_ok_health()).encode()
        finally:
            with lock:
                in_flight["cur"] -= 1

    summary = poller.run_once(reg, store, opener=slow_opener, max_workers=3)
    assert summary["events"] == 8
    assert in_flight["max"] <= 3, f"concurrency exceeded the bound: {in_flight['max']}"


# --- a replica cannot mint host_observed (invariant) -------------------------
def test_replica_published_health_observed_is_not_host_observed():
    """Only the host poller can stamp trust=host_observed. A replica publishing the SAME kind
    through the authenticated API is untrusted_claim and cannot overwrite the host observation."""
    from management.api.comms_envelope import build_envelope

    # What the poller writes:
    host = build_envelope(origin_instance_id="urn:uuid:parent", principal_id="host:fleet-poller",
                          channel="instance/urn:uuid:a/health", kind="health.observed",
                          payload={"health_state": "live"}, trust="host_observed")
    # What the API path produces for a replica (build_envelope with the default trust):
    replica = build_envelope(origin_instance_id="urn:uuid:a", principal_id="instance:urn:uuid:a",
                             channel="instance/urn:uuid:a/health", kind="health.observed",
                             payload={"health_state": "live"})
    assert host["trust"] == "host_observed"
    assert replica["trust"] == "untrusted_claim"  # the API cannot set host_observed


# --- real-socket end-to-end (genuine TCP, not a fake opener) -----------------
class _HealthHandler(BaseHTTPRequestHandler):
    git_sha = "realsha"

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"ok": True, "git_sha": self.git_sha,
                           "cycling": True, "seconds_since_last_cycle": 5.0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def test_real_socket_end_to_end_observe(tmp_path):
    """A genuine live check without docker: stand up a real loopback HTTP /health server, register
    it, poll it over real TCP (default opener), observe host_observed live -> tear down, 0 orphans."""
    _HealthHandler.git_sha = "realsha"
    srv = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        reg = fr.FleetRegistryStore(tmp_path / "reg.json")
        _register(reg, "urn:uuid:real", "aq-replica-real", port, git_sha="realsha")
        store = InMemoryStore()
        summary = poller.run_once(reg, store, timeout_s=3)  # real default opener, real socket
        assert summary["live"] == 1
        env = store.envelopes()[0]
        assert env["trust"] == "host_observed"
        assert env["payload"]["observed_git_sha"] == "realsha"
        assert env["payload"]["health_state"] == "live"
    finally:
        srv.shutdown()
        srv.server_close()
    # After teardown the port is closed: the next poll observes it down (no orphan left polling).
    reg2 = fr.FleetRegistryStore(tmp_path / "reg.json")
    store2 = InMemoryStore()
    poller.run_once(reg2, store2, timeout_s=1)
    assert reg2.get("urn:uuid:real")["health"]["state"] in (fr.HEALTH_STALE, fr.HEALTH_DOWN)
