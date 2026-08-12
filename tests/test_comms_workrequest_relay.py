"""Host->replica work.request relay SSRF/safety tests (#4834 comms Phase 3, design §10 Phase 3).

Proves the host POST relay:
  * targets ONLY a registry loopback app_mgmt port + the exact /api/agent-comms/inbox route,
  * refuses redirects and rejects a non-loopback / non-inbox / query-bearing URL,
  * never uses a body/caller-supplied host or a torn-down / unknown instance,
  * fails closed with no scoped inbox token,
  * bounds the request body.
"""

from __future__ import annotations

import json

import pytest

from ralph_portable.fleet_registry import FleetRegistryStore
from scripts import host_workrequest_relay as relay

SELF = "urn:uuid:self"
PARENT = "urn:uuid:parent"


def _entry(port=18090, instance_id=SELF, torn_down=False):
    return {
        "instance_id": instance_id, "project": "aq-replica-x",
        "parent_instance_id": PARENT, "ports": {"app_mgmt": port},
        "health_url": f"http://127.0.0.1:{port}/health",
        "lifecycle_state": "torn_down" if torn_down else "live",
    }


def _live_registry(tmp_path, entries):
    reg = FleetRegistryStore(tmp_path / "reg.json")
    for e in entries:
        reg.upsert_standup(
            {"instance_id": e["instance_id"], "project": e["project"],
             "requester_instance_id": PARENT, "ports": e["ports"],
             "health_url": e["health_url"]},
            lifecycle="torn_down" if e["lifecycle_state"] == "torn_down" else "live")
    return reg


# --- URL / SSRF guards ------------------------------------------------------
def test_only_builds_the_exact_loopback_inbox_url():
    seen = {}

    def fake_post(url, *, token, body, timeout_s):
        seen["url"] = url
        return json.dumps({"ok": True, "stored_inert": True}).encode()

    body = relay.build_inbox_body(goal="g", target_instance_id=SELF)
    res = relay.post_workrequest(_entry(port=12345), body, token="t", post=fake_post)
    assert res["posted"] is True
    assert seen["url"] == "http://127.0.0.1:12345/api/agent-comms/inbox"


@pytest.mark.parametrize("bad", [
    "https://127.0.0.1/api/agent-comms/inbox",   # non-http
    "http://evil.example.com/api/agent-comms/inbox",  # non-loopback host
    "http://127.0.0.1:8090/api/replication/actuate",  # wrong route
    "http://127.0.0.1:8090/api/agent-comms/inbox?x=1",  # query string
])
def test_assert_loopback_inbox_url_fails_closed(bad):
    with pytest.raises(ValueError):
        relay._assert_loopback_inbox_url(bad)


def test_no_redirect_handler_refuses_to_follow():
    h = relay._NoRedirectHandler()
    assert h.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:9/evil") is None


def test_port_comes_from_registry_not_body():
    # Even if a caller tried to smuggle a host/port in the body, post_workrequest builds the URL ONLY
    # from the registry entry's app_mgmt port.
    seen = {}

    def fake_post(url, *, token, body, timeout_s):
        seen["url"] = url
        return b"{}"

    body = relay.build_inbox_body(goal="g", target_instance_id=SELF)
    body["target"]["instance_id"] = "urn:uuid:someone-else"  # ignored for URL building
    relay.post_workrequest(_entry(port=17777), body, token="t", post=fake_post)
    assert seen["url"] == "http://127.0.0.1:17777/api/agent-comms/inbox"


def test_oversize_body_is_refused_before_any_network_call():
    called = {"n": 0}

    def fake_post(*a, **k):
        called["n"] += 1
        return b"{}"

    body = relay.build_inbox_body(goal="x" * 4000, target_instance_id=SELF)
    body["_bloat"] = "y" * (70 * 1024)
    with pytest.raises(ValueError):
        relay.post_workrequest(_entry(), body, token="t", post=fake_post)
    assert called["n"] == 0  # never hit the network


# --- deliver_to_instance: registry + token fail-closed ----------------------
def test_deliver_refuses_unknown_or_torn_down_instance(tmp_path):
    reg = _live_registry(tmp_path, [_entry(torn_down=True)])
    body = relay.build_inbox_body(goal="g", target_instance_id=SELF)
    res = relay.deliver_to_instance(reg, SELF, body,
                                    env={"AQ_COMMS_INBOX_TOKEN": "t"},
                                    post=lambda *a, **k: b"{}")
    assert res["posted"] is False and "live registry" in res["reason"]


def test_deliver_fails_closed_without_a_scoped_token(tmp_path):
    reg = _live_registry(tmp_path, [_entry()])
    body = relay.build_inbox_body(goal="g", target_instance_id=SELF)
    res = relay.deliver_to_instance(reg, SELF, body, env={},  # no token
                                    post=lambda *a, **k: b"{}")
    assert res["posted"] is False and "token" in res["reason"]


def test_deliver_posts_to_a_live_instance(tmp_path):
    reg = _live_registry(tmp_path, [_entry(port=19001)])
    seen = {}

    def fake_post(url, *, token, body, timeout_s):
        seen.update(url=url, token=token)
        return json.dumps({"ok": True, "stored_inert": True, "imported": False}).encode()

    body = relay.build_inbox_body(goal="tune", target_instance_id=SELF, idempotency_key="k1")
    res = relay.deliver_to_instance(reg, SELF, body,
                                    env={"AQ_COMMS_INBOX_RELAY_TOKENS": json.dumps({SELF: "scoped-t"})},
                                    post=fake_post)
    assert res["posted"] is True
    assert seen["url"] == "http://127.0.0.1:19001/api/agent-comms/inbox"
    assert seen["token"] == "scoped-t"
    assert res["response"]["stored_inert"] is True
