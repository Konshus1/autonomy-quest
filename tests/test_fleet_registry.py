"""Tests for the container/fleet registry (CONTAINER_FLEET_REGISTRY_SPEC). Each asserts one of the
spec's security properties: host-minted identity (no forgery), fleet-scoped discovery/messaging (no
cross-fleet leak), forbidden-field rejection (no docker.sock / no self-asserted identity),
traversal-safe ids, honest stale-liveness."""
import pytest

from ralph_portable.fleet_registry import FleetRegistryStore, validate_registration, mint_identity


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def tick(self, dt): self.t += dt


def _store(stale_ttl=90):
    return FleetRegistryStore("host-secret-xyz", now=Clock(), stale_ttl=stale_ttl)


def _reg(store, requester="c1", fleet="alpha", caps=None):
    return store.register({"requester_instance_id": requester, "fleet_id_requested": fleet,
                           "capabilities": caps or ["mission"]})


# ---- registration + host-minted identity ---------------------------------------------------
def test_register_mints_host_identity_and_token():
    s = _store()
    r = _reg(s)
    assert r["member_id"] and r["fleet_id"] == "alpha" and len(r["token"]) == 64
    # heartbeat with the real token works
    s.heartbeat(r["member_id"], r["token"])


def test_registration_rejects_forbidden_and_self_asserted_fields():
    assert "requires_docker_sock is forbidden for guest registration" in \
        "; ".join(validate_registration({"requester_instance_id": "c", "requires_docker_sock": True}))
    assert any("guest_executes" in e for e in
               validate_registration({"requester_instance_id": "c", "guest_executes": True}))
    for forged in ("member_id", "host_token", "fleet_assigned"):
        errs = validate_registration({"requester_instance_id": "c", forged: "x"})
        assert any("host-minted" in e for e in errs), f"{forged} must be rejected as self-asserted"


def test_register_raises_on_invalid_packet():
    s = _store()
    with pytest.raises(ValueError):
        s.register({"requester_instance_id": "c", "member_id": "i-want-this-id"})  # self-asserted


def test_member_id_is_traversal_safe():
    s = _store()
    r = s.register({"requester_instance_id": "../../etc/passwd", "fleet_id_requested": "a/b"})
    assert "/" not in r["member_id"] and ".." not in r["member_id"] and "\\" not in r["member_id"]


# ---- no identity forgery -------------------------------------------------------------------
def test_forged_token_is_rejected():
    s = _store()
    a = _reg(s, "a", "alpha")
    b = _reg(s, "b", "alpha")
    # member A presenting B's token for A's id -> rejected (A can't compute A's real token itself)
    with pytest.raises(PermissionError):
        s.heartbeat(a["member_id"], b["token"])
    with pytest.raises(PermissionError):
        s.members(a["member_id"], "deadbeef" * 8)


# ---- fleet-scoped discovery ----------------------------------------------------------------
def test_discovery_is_fleet_scoped():
    s = _store()
    a = _reg(s, "a", "alpha")
    _reg(s, "a2", "alpha")
    _reg(s, "b", "beta")
    ids = {m["member_id"] for m in s.members(a["member_id"], a["token"])}
    assert all("alpha" in mid for mid in ids), "a member must only discover its own fleet"
    assert not any("beta" in mid for mid in ids), "no cross-fleet enumeration"


# ---- host-brokered comms + no cross-fleet leak ---------------------------------------------
def test_same_fleet_message_delivers():
    s = _store()
    a = _reg(s, "a", "alpha")
    b = _reg(s, "b", "alpha")
    s.send(a["member_id"], a["token"], b["member_id"], {"hi": 1})
    got = s.inbox(b["member_id"], b["token"])
    assert len(got) == 1 and got[0]["from"] == a["member_id"]
    assert s.inbox(b["member_id"], b["token"]) == []  # drained


def test_cross_fleet_message_denied_by_default_then_allow_listed():
    s = _store()
    a = _reg(s, "a", "alpha")
    b = _reg(s, "b", "beta")
    with pytest.raises(PermissionError):
        s.send(a["member_id"], a["token"], b["member_id"], "x")
    s.grant_cross_fleet("alpha", "beta")  # operator/CI grants the directed edge
    assert s.send(a["member_id"], a["token"], b["member_id"], "x")["delivered"] is True


def test_message_payload_is_bounded():
    s = _store()
    a = _reg(s, "a", "alpha")
    b = _reg(s, "b", "alpha")
    s.send(a["member_id"], a["token"], b["member_id"], "S" * 50000)
    assert len(s.inbox(b["member_id"], b["token"])[0]["payload"]) <= 16 * 1024


# ---- honest stale-liveness -----------------------------------------------------------------
def test_stale_liveness_is_labeled_not_deleted_and_heartbeat_revives():
    clock = Clock()
    s = FleetRegistryStore("secret", now=clock, stale_ttl=90)
    a = s.register({"requester_instance_id": "a", "fleet_id_requested": "alpha"})
    clock.tick(200)  # past the TTL
    assert s.mark_stale() == [a["member_id"]]
    # still present (labeled, not deleted) and reported stale, never active
    listing = s.members(a["member_id"], a["token"])
    assert listing[0]["status"] == "stale"
    # a heartbeat revives it
    s.heartbeat(a["member_id"], a["token"])
    assert s.members(a["member_id"], a["token"])[0]["status"] == "active"
