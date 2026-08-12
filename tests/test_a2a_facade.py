"""A2A façade conformance-shape + auth + scoping tests (#4834 comms Phase 5, design §10 Phase 5).

Covers the brief's functional deliverables at the JSON-RPC boundary:
  * minimal Agent Card advertises ONLY implemented skills (no streaming, no forbidden skills),
  * version negotiation (supported ok; unsupported rejected),
  * auth (bearer required; bad credential rejected),
  * per-principal task scoping (a principal sees/acts on ONLY its own tasks; cross-principal denied),
  * task replay + cancel idempotent + scoped,
  * MALICIOUS cards/parts/URLs rejected (file uri = SSRF; inline bytes; forbidden artifact keys; oversize).

Honesty guard: this asserts A2A *shape*, NOT that the official inspector/TCK passed (none is available
offline). It never asserts "A2A compliant"; see test_a2a_honest_conformance below + COMMS_PHASE5.md.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api.a2a_translate import (  # noqa: E402
    A2A_CONTENT_TYPE_NOT_SUPPORTED,
    A2A_TASK_NOT_CANCELABLE,
    A2A_TASK_NOT_FOUND,
    A2A_UNSUPPORTED_VERSION,
    JSONRPC_INVALID_PARAMS,
)
from management.api.a2a_router import build_a2a_app  # noqa: E402
from management.api.comms_auth import CommsAuthenticator  # noqa: E402
from management.api.comms_envelope import MAX_TEXT_BYTES  # noqa: E402
from management.api.store import InMemoryStore  # noqa: E402

SELF = "urn:uuid:self"
PARENT = "urn:uuid:parent"
CLIENT = "urn:uuid:client"
OTHER = "urn:uuid:other"


@pytest.fixture
def env(monkeypatch):
    # This replica's identity + configured parent (the inbox ACL keys on these).
    monkeypatch.setenv("AQ_INSTANCE_ID", SELF)
    monkeypatch.setenv("AQ_COMMS_PARENT_INSTANCE_ID", PARENT)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)


@pytest.fixture
def ctx(env):
    store = InMemoryStore()
    auth = CommsAuthenticator(credentials={
        # A publish-capable A2A client (its OWN claims): status.report + experiment.result.
        "tok-client": {
            "principal_id": f"instance:{CLIENT}", "origin_instance_id": CLIENT,
            "lineage": [CLIENT], "kinds": ["status.report", "experiment.result"], "operator": False,
        },
        # A second publish client, to prove cross-principal scoping.
        "tok-other": {
            "principal_id": f"instance:{OTHER}", "origin_instance_id": OTHER,
            "lineage": [OTHER], "kinds": ["status.report", "experiment.result"], "operator": False,
        },
        # The TRUE parent, scoped to deliver inbound work into THIS replica (the inbox ACL).
        "tok-parent": {
            "principal_id": f"instance:{PARENT}", "origin_instance_id": PARENT,
            "lineage": [PARENT], "kinds": ["work.request", "work.response", "receipt"],
            "operator": False, "can_deliver_inbox": True,
        },
    })
    return TestClient(build_a2a_app(store, auth)), store


def _hdr(tok="tok-client"):
    return {"Authorization": f"Bearer {tok}"}


def _rpc(c, method, params, tok="tok-client", rpc_id=1, extra_headers=None):
    headers = dict(_hdr(tok))
    if extra_headers:
        headers.update(extra_headers)
    return c.post("/a2a", headers=headers,
                  json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params})


def _msg(text, skill, message_id="m1", parts=None):
    return {"message": {
        "kind": "message", "role": "user", "messageId": message_id,
        "parts": parts if parts is not None else [{"kind": "text", "text": text}],
        "metadata": {"skillId": skill}}}


# --- Agent Card ----------------------------------------------------------------
def test_agent_card_advertises_only_implemented_skills(ctx):
    c, _ = ctx
    card = c.get("/.well-known/agent-card.json").json()
    assert card["protocolVersion"] == "1.0"
    ids = {s["id"] for s in card["skills"]}
    assert ids == {"aq.report-status", "aq.submit-experiment-result", "aq.request-work"}
    # No aspirational capability, no forbidden skill.
    assert card["capabilities"]["streaming"] is False
    assert card["capabilities"]["pushNotifications"] is False
    for forbidden in ("replicate-host", "run-shell", "change-capability", "approve", "adopt-result"):
        assert forbidden not in ids
    # Security scheme is declared (auth is required for every operation).
    assert "aqCommsBearer" in card["securitySchemes"]
    # The authority disclaimer rides on the card.
    assert card["metadata"]["aq:authority"] == "none"


def test_agent_card_public_but_operations_require_auth(ctx):
    c, _ = ctx
    assert c.get("/.well-known/agent-card.json").status_code == 200  # discovery is public
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/list", "params": {}})
    assert r.status_code == 401  # no credential


# --- version negotiation -------------------------------------------------------
def test_version_negotiation_supported_and_unsupported(ctx):
    c, _ = ctx
    ok = _rpc(c, "message/send", _msg("hi", "aq.report-status"),
              extra_headers={"X-A2A-Version": "1.0"})
    assert ok.json().get("result"), ok.text
    bad = _rpc(c, "message/send", _msg("hi", "aq.report-status", message_id="m2"),
               extra_headers={"X-A2A-Version": "2.0"})
    assert bad.json()["error"]["code"] == A2A_UNSUPPORTED_VERSION


# --- auth ----------------------------------------------------------------------
def test_bad_credential_rejected(ctx):
    c, _ = ctx
    r = _rpc(c, "tasks/list", {}, tok="bogus")
    assert r.status_code == 401


# --- publish path (status.report) ---------------------------------------------
def test_message_send_publish_creates_untrusted_claim_task(ctx):
    c, store = ctx
    r = _rpc(c, "message/send", _msg("cycle 3 healthy", "aq.report-status"))
    task = r.json()["result"]
    assert task["kind"] == "task"
    assert task["metadata"]["aq:trust"] == "untrusted_claim"
    assert task["metadata"]["aq:authority"] == "none"
    # It translated to exactly one stored envelope in the SAME typed queue.
    envs = store.envelopes()
    assert len(envs) == 1 and envs[0]["kind"] == "status.report"
    assert envs[0]["origin_instance_id"] == CLIENT  # server-derived, not from the body


# --- per-principal scoping -----------------------------------------------------
def test_cross_principal_task_get_denied(ctx):
    c, _ = ctx
    created = _rpc(c, "message/send", _msg("mine", "aq.report-status"), tok="tok-client").json()["result"]
    tid = created["id"]
    # The OWNER can read it.
    mine = _rpc(c, "tasks/get", {"id": tid}, tok="tok-client").json()
    assert mine["result"]["id"] == tid
    # A DIFFERENT principal gets TaskNotFound (no cross-principal read, no existence oracle).
    theirs = _rpc(c, "tasks/get", {"id": tid}, tok="tok-other").json()
    assert theirs["error"]["code"] == A2A_TASK_NOT_FOUND


def test_tasks_list_is_scoped_to_the_caller(ctx):
    c, _ = ctx
    _rpc(c, "message/send", _msg("a", "aq.report-status", message_id="ma"), tok="tok-client")
    _rpc(c, "message/send", _msg("b", "aq.report-status", message_id="mb"), tok="tok-other")
    mine = _rpc(c, "tasks/list", {}, tok="tok-client").json()["result"]
    assert mine["count"] == 1
    assert all(t["metadata"]["aq:principalId"] == f"instance:{CLIENT}" for t in mine["tasks"])


# --- idempotent replay ---------------------------------------------------------
def test_replay_same_message_id_is_idempotent(ctx):
    c, store = ctx
    p = _msg("once", "aq.report-status", message_id="dup-1")
    t1 = _rpc(c, "message/send", p).json()["result"]
    t2 = _rpc(c, "message/send", p).json()["result"]
    assert t1["id"] == t2["id"]
    assert len(store.envelopes()) == 1  # no duplicate effect


# --- inbound work request (inert inbox) + cancel ------------------------------
def test_request_work_lands_inert_and_cancel_is_idempotent_and_scoped(ctx):
    c, store = ctx
    # Only the TRUE parent may deliver inbound work (inbox ACL); it lands INERT (submitted).
    created = _rpc(c, "message/send", _msg("summarize E7", "aq.request-work", message_id="w1"),
                   tok="tok-parent").json()["result"]
    assert created["status"]["state"] == "submitted"
    assert created["metadata"]["aq:deliveryState"] == "delivered"
    tid = created["id"]

    # A non-owner cannot cancel it.
    denied = _rpc(c, "tasks/cancel", {"id": tid}, tok="tok-client").json()
    assert denied["error"]["code"] == A2A_TASK_NOT_FOUND

    # The owner cancels: idempotent (two cancels -> one cancel envelope, state canceled both times).
    can1 = _rpc(c, "tasks/cancel", {"id": tid}, tok="tok-parent").json()["result"]
    can2 = _rpc(c, "tasks/cancel", {"id": tid}, tok="tok-parent").json()["result"]
    assert can1["status"]["state"] == "canceled" and can2["status"]["state"] == "canceled"
    cancel_envs = [e for e in store.envelopes()
                   if e.get("kind") == "work.request" and (e.get("payload") or {}).get("cancel_of")]
    assert len(cancel_envs) == 1  # exactly one gated cancellation proposal, not a kill


def test_request_work_from_non_parent_denied(ctx):
    c, _ = ctx
    # A stranger/sibling (not this replica's configured parent) cannot deliver inbound work.
    r = _rpc(c, "message/send", _msg("do X", "aq.request-work", message_id="w9"), tok="tok-client")
    assert r.status_code == 403  # AclDenied -> wrong-parent


def test_completed_claim_task_is_not_cancelable(ctx):
    c, _ = ctx
    t = _rpc(c, "message/send", _msg("res", "aq.submit-experiment-result", message_id="r1")).json()["result"]
    assert t["status"]["state"] == "completed"  # TRANSPORT state only
    r = _rpc(c, "tasks/cancel", {"id": t["id"]}, tok="tok-client").json()
    assert r["error"]["code"] == A2A_TASK_NOT_CANCELABLE


# --- malicious parts / URLs / oversize ----------------------------------------
def test_file_part_with_uri_rejected_ssrf(ctx):
    c, _ = ctx
    parts = [{"kind": "file", "file": {"uri": "http://169.254.169.254/latest/meta-data"}}]
    r = _rpc(c, "message/send", _msg("", "aq.submit-experiment-result", parts=parts)).json()
    assert r["error"]["code"] == A2A_CONTENT_TYPE_NOT_SUPPORTED


def test_file_part_with_inline_bytes_rejected(ctx):
    c, _ = ctx
    parts = [{"kind": "file", "file": {"bytes": "QUJD", "name": "x"}}]
    r = _rpc(c, "message/send", _msg("", "aq.submit-experiment-result", parts=parts)).json()
    assert r["error"]["code"] == A2A_CONTENT_TYPE_NOT_SUPPORTED


def test_artifact_ref_with_forbidden_url_key_rejected(ctx):
    c, _ = ctx
    parts = [
        {"kind": "text", "text": "result"},
        {"kind": "data", "data": {"artifact_ref": {
            "digest": "sha256:" + "a" * 64, "url": "http://evil/x"}}},
    ]
    r = _rpc(c, "message/send", _msg("", "aq.submit-experiment-result", parts=parts)).json()
    assert r["error"]["code"] == JSONRPC_INVALID_PARAMS
    assert "forbidden key" in r["error"]["message"]


def test_valid_digest_artifact_ref_accepted(ctx):
    c, store = ctx
    parts = [
        {"kind": "text", "text": "result summary"},
        {"kind": "data", "data": {"artifact_ref": {
            "digest": "sha256:" + "b" * 64, "media_type": "application/json"}}},
    ]
    r = _rpc(c, "message/send", _msg("", "aq.submit-experiment-result", parts=parts)).json()
    task = r["result"]
    assert task["artifacts"], "a digest artifact ref should project to an A2A Artifact"
    # And the outbound projection carries NO fetchable locator, only the digest.
    art_data = task["artifacts"][0]["parts"][0]["data"]
    assert art_data["digest"].startswith("sha256:")
    assert "url" not in art_data and "uri" not in art_data and "path" not in art_data


def test_oversize_text_rejected(ctx):
    c, _ = ctx
    big = "x" * (MAX_TEXT_BYTES + 10)
    r = _rpc(c, "message/send", _msg(big, "aq.report-status")).json()
    assert r["error"]["code"] == JSONRPC_INVALID_PARAMS


def test_unadvertised_or_missing_skill_rejected(ctx):
    c, _ = ctx
    # unadvertised skill
    p1 = {"message": {"kind": "message", "role": "user", "messageId": "s1",
                      "parts": [{"kind": "text", "text": "x"}], "metadata": {"skillId": "aq.run-shell"}}}
    assert _rpc(c, "message/send", p1).json()["error"]["code"] == JSONRPC_INVALID_PARAMS
    # missing skill
    p2 = {"message": {"kind": "message", "role": "user", "messageId": "s2",
                      "parts": [{"kind": "text", "text": "x"}]}}
    assert _rpc(c, "message/send", p2).json()["error"]["code"] == JSONRPC_INVALID_PARAMS


# --- honest conformance claim --------------------------------------------------
def test_honest_conformance_claim_in_card(ctx):
    """The card must NOT claim verified interoperability; it states shape-only conformance."""
    c, _ = ctx
    card = c.get("/.well-known/agent-card.json").json()
    conf = card["metadata"]["aq:conformance"].lower()
    assert "not verified" in conf and "shaped" in conf
    assert "compliant" not in conf  # never the bare, false "A2A compliant" claim
