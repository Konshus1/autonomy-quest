"""Slice-4 replication authority-envelope INHERITANCE (the compounding-autonomy safeguard).

The load-bearing invariant proved here: a replicated child can NEVER silently have a WIDER
authority envelope than its parent. The child's effective envelope is parent ∩ request UNLESS a
widen is authorized by a HOST-only secret — which a guest can never mint. Red-first: several of
these tests FAIL against a naive impl that just applies the requested envelope.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from runner.authority_envelope import (
    ALL_CLASSES, DISPOSITION_GATED, DISPOSITION_OPEN, DEFAULT_ENVELOPE, from_config,
)
from ralph_portable import replication_authority as ra
from ralph_portable import host_replica_stack as hrs
from ralph_portable import replication_modifications as mp


# --- pure inheritance algebra -----------------------------------------------
def _env(**opened):
    """Build an envelope with the named classes OPEN (rest default-gated)."""
    return from_config({c: DISPOSITION_OPEN for c in opened})


def test_intersection_is_more_restrictive_of_both():
    parent = _env(spend_money=1, commit=1)          # parent opens spend_money + commit
    requested = _env(commit=1, deploy=1)            # child asks commit + deploy
    eff = ra.intersect(parent, requested)
    # open only where BOTH are open: commit. spend_money (parent-only) and deploy (request-only) gated.
    assert eff.disposition("commit") == DISPOSITION_OPEN
    assert eff.disposition("spend_money") == DISPOSITION_GATED
    assert eff.disposition("deploy") == DISPOSITION_GATED
    assert ra.subset_of(eff, parent)


def test_widen_classes_are_the_openings_beyond_parent():
    parent = _env(commit=1)
    requested = _env(commit=1, spend_money=1, deploy=1)
    assert ra.widen_classes(parent, requested) == ("deploy", "spend_money")


# --- RED-FIRST: unauthorized widen stays GATED ------------------------------
def test_unauthorized_widen_yields_gated_child_for_that_class():
    """A child request that OPENS a class the parent GATED, with NO authorization, must yield a
    child still GATED for that class. This FAILS a naive impl that applies the requested envelope."""
    parent = _env(commit=1)                          # parent: only commit open
    request = {"dispositions": {"deploy": "open", "commit": "open"}}  # child tries to open deploy
    res = ra.resolve_child_envelope(parent, request, host_env={})     # no widen token in host env
    assert res.effective.disposition("deploy") == DISPOSITION_GATED   # STRIPPED, not applied
    assert res.effective.disposition("commit") == DISPOSITION_OPEN    # allowed (parent had it)
    assert res.stripped_widen == ("deploy",)
    assert res.granted_widen == ()
    assert res.authorized is False
    assert ra.subset_of(res.effective, parent)       # child ⊆ parent on EVERY class


def test_host_authorized_widen_does_open_the_class():
    parent = _env(commit=1)
    token = "host-review-granted-42"
    request = {"dispositions": {"deploy": "open", "commit": "open"},
               "widen_authorization": token}
    res = ra.resolve_child_envelope(parent, request, host_env={ra.WIDEN_TOKEN_ENV: token})
    assert res.effective.disposition("deploy") == DISPOSITION_OPEN    # AUTHORIZED widen honored
    assert res.granted_widen == ("deploy",)
    assert res.stripped_widen == ()
    assert res.authorized is True


def test_wrong_token_does_not_authorize_widen():
    parent = _env()
    request = {"dispositions": {"spend_money": "open"}, "widen_authorization": "guessed"}
    res = ra.resolve_child_envelope(parent, request, host_env={ra.WIDEN_TOKEN_ENV: "the-real-secret"})
    assert res.effective.disposition("spend_money") == DISPOSITION_GATED
    assert res.authorized is False and res.stripped_widen == ("spend_money",)


def test_empty_host_token_never_authorizes_even_with_matching_marker():
    # Fail-closed: an unset/empty host token authorizes nothing, even if the packet's marker is "".
    parent = _env()
    request = {"dispositions": {"deploy": "open"}, "widen_authorization": ""}
    res = ra.resolve_child_envelope(parent, request, host_env={ra.WIDEN_TOKEN_ENV: ""})
    assert res.authorized is False
    assert res.effective.disposition("deploy") == DISPOSITION_GATED


# --- may_self_widen requires authorization ----------------------------------
def test_may_self_widen_stripped_without_authorization():
    parent = _env(commit=1)
    request = {"dispositions": {"commit": "open"}, "may_self_widen": True}
    res = ra.resolve_child_envelope(parent, request, host_env={})
    assert res.may_self_widen is False           # the right to self-widen is NOT silently delegable
    assert res.self_widen_stripped is True


def test_may_self_widen_granted_with_authorization():
    parent = _env(commit=1)
    token = "tok"
    request = {"dispositions": {"commit": "open"}, "may_self_widen": True,
               "widen_authorization": token}
    res = ra.resolve_child_envelope(parent, request, host_env={ra.WIDEN_TOKEN_ENV: token})
    assert res.may_self_widen is True and res.authorized is True


# --- fail-closed on missing / garbage ---------------------------------------
def test_missing_envelope_yields_fully_gated_child():
    parent = _env(spend_money=1, commit=1, deploy=1)   # a WIDE parent
    res = ra.resolve_child_envelope(parent, None, host_env={})
    for cls in ra.HIGH_CONSEQUENCE_CLASSES:
        assert res.effective.disposition(cls) == DISPOSITION_GATED  # child fully gated, not parent-wide
    assert ra.subset_of(res.effective, parent)


def test_garbage_request_fails_closed_to_gated():
    parent = _env(spend_money=1)
    # dispositions is not a mapping -> _requested_envelope falls back to fully-gated default
    res = ra.resolve_child_envelope(parent, {"dispositions": "not-a-map"}, host_env={})
    assert res.effective.disposition("spend_money") == DISPOSITION_GATED


def test_child_subset_of_parent_on_every_class_property():
    parent = _env(commit=1, spend_money=1)
    # A request that tries to open EVERYTHING, unauthorized, must still be ⊆ parent.
    request = {"dispositions": {c: "open" for c in ALL_CLASSES}}
    res = ra.resolve_child_envelope(parent, request, host_env={})
    assert ra.subset_of(res.effective, parent)
    # exactly the parent-open classes survive
    assert res.effective.disposition("commit") == DISPOSITION_OPEN
    assert res.effective.disposition("spend_money") == DISPOSITION_OPEN
    assert res.effective.disposition("deploy") == DISPOSITION_GATED


def test_child_may_be_narrower_than_parent():
    parent = _env(commit=1, spend_money=1)
    request = {"dispositions": {"commit": "gated", "spend_money": "open"}}  # child gives up commit
    res = ra.resolve_child_envelope(parent, request, host_env={})
    assert res.effective.disposition("commit") == DISPOSITION_GATED
    assert res.effective.disposition("spend_money") == DISPOSITION_OPEN


# --- GUEST can never self-authorize -----------------------------------------
def test_guest_cannot_self_authorize_a_widen():
    # A guest calling the pure authorizer with its OWN env (no host secret) can never open a widen.
    assert ra.authorize_widen("anything", host_env={}) is False
    assert ra.authorize_widen("anything", host_env={"AQ_SOMETHING": "x"}) is False
    # AQ_REPLICATION_WIDEN_TOKEN is an AQ_REPLICATION_* key the modification allowlist FORBIDS a
    # guest from setting on itself — so a guest cannot inject it via a config modification.
    assert mp.forbidden_key_reason(ra.WIDEN_TOKEN_ENV) is not None


# --- shape validation --------------------------------------------------------
def test_request_shape_validation_rejects_unknown_class_and_bad_disposition():
    assert ra.validate_child_envelope_request({"dispositions": {"spend_money": "open"}}) == []
    assert ra.validate_child_envelope_request(None) == []
    bad_class = ra.validate_child_envelope_request({"dispositions": {"nope": "open"}})
    assert bad_class and "unknown class" in bad_class[0]
    bad_disp = ra.validate_child_envelope_request({"dispositions": {"deploy": "sometimes"}})
    assert bad_disp and "open" in bad_disp[0]
    bad_bool = ra.validate_child_envelope_request({"may_self_widen": "yes"})
    assert bad_bool and "boolean" in bad_bool[0]
    bad_key = ra.validate_child_envelope_request({"surprise": 1})
    assert bad_key and "unknown key" in bad_key[0]


# --- guest-un-settable effective env ----------------------------------------
def test_authenticated_identity_env_is_not_guest_settable():
    # HIGH-#1 load-bearing invariant: the authenticated requester = the mgmt API's own AQ_INSTANCE_ID.
    # If a guest could set AQ_INSTANCE_ID via a config modification it could re-forge its identity and
    # reopen the ancestor-forgery escalation. It must stay guest-forbidden (host-written secret only).
    assert mp.forbidden_key_reason("AQ_INSTANCE_ID") is not None
    errs = mp.validate_modification_packet({"config": {"AQ_INSTANCE_ID": "urn:uuid:forged"}})
    assert errs and any("forbidden" in e for e in errs)


def test_guest_cannot_set_effective_envelope_env_directly():
    for key in (ra.GRANT_ENV, ra.SELF_WIDEN_ENV):
        assert mp.forbidden_key_reason(key) is not None
        errs = mp.validate_modification_packet({"config": {key: "x"}})
        assert errs and any("forbidden" in e for e in errs)
    # and the denylist addition did not break the allowlist self-consistency invariant
    mp.assert_allowlist_is_self_consistent()


# --- grant env materialization ----------------------------------------------
def test_grant_env_carries_effective_and_self_widen():
    parent = _env(commit=1)
    res = ra.resolve_child_envelope(parent, {"dispositions": {"commit": "open"}}, host_env={})
    genv = ra.grant_env(res)
    parsed = json.loads(genv[ra.GRANT_ENV])
    assert parsed["commit"] == DISPOSITION_OPEN
    assert parsed["deploy"] == DISPOSITION_GATED
    assert genv[ra.SELF_WIDEN_ENV] == "0"


def test_authority_overlay_injects_guest_unsettable_env():
    parent = _env(commit=1)
    res = ra.resolve_child_envelope(parent, {"dispositions": {"commit": "open"}}, host_env={})
    overlay = ra.render_authority_overlay(res)
    assert ra.GRANT_ENV in overlay and ra.SELF_WIDEN_ENV in overlay
    assert "services:" in overlay and "app:" in overlay and "environment:" in overlay


# --- config.py reads the host-written grant env -----------------------------
def test_config_grant_env_overrides_instance_yaml(monkeypatch):
    from runner import config as cfg
    grant = json.dumps({"deploy": "open"})
    monkeypatch.setenv(ra.GRANT_ENV, grant)
    # instance.yaml said nothing (raw=None) but the host grant opens deploy => operative envelope.
    env = cfg._resolve_authority_envelope(None)
    assert env.disposition("deploy") == DISPOSITION_OPEN


def test_config_grant_env_garbage_fails_closed(monkeypatch):
    from runner import config as cfg
    monkeypatch.setenv(ra.GRANT_ENV, "{not json")
    env = cfg._resolve_authority_envelope({"spend_money": "open"})  # yaml would have opened it
    # garbage grant => fully-gated default, NOT the yaml's wider spend_money-open
    assert env.disposition("spend_money") == DISPOSITION_GATED


def test_config_no_grant_env_is_byte_identical(monkeypatch):
    from runner import config as cfg
    monkeypatch.delenv(ra.GRANT_ENV, raising=False)
    env = cfg._resolve_authority_envelope({"deploy": "open"})
    assert env.disposition("deploy") == DISPOSITION_OPEN  # exactly what instance.yaml declared


# --- host stand-up wiring (docker/compose injected) -------------------------
def _fake_docker(mapping):
    def run(args, *, check=True, timeout=None, env=None, cwd=None):
        return mapping.get(args[0], "")
    return run


def _write_parent_instance(repo_root: Path, envelope: dict | None):
    body = {"mission": {"objective": "x", "measure": {"what": "y", "where": "z"}}}
    if envelope is not None:
        body["authority_envelope"] = envelope
    (repo_root / "instance.yaml").write_text(json.dumps(body))  # JSON is valid YAML


#: The host identity secret used to sign/verify the authenticated requester id in tests. Present in
#: the host env passed to stand_up (so it can VERIFY) and used by _packet to mint valid signatures
#: (modelling the propose endpoint, which holds the same host secret).
_ID_SECRET = "test-host-identity-secret-0xdeadbeef"


def _standup(repo_root, packet, env, tmp_path):
    captured = {}

    def compose_runner(compose_args, *, cwd, env=None, timeout=None):
        captured["args"] = compose_args
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    e = dict(env)
    e.setdefault(ra.IDENTITY_SECRET_ENV, _ID_SECRET)  # host holds the secret to VERIFY signatures
    rec = hrs.stand_up_replica_stack(
        packet, status="auto_approved", repo_root=repo_root, git_sha="cafef00d",
        state_root=tmp_path / "st", env=e, docker=_fake_docker({"ps": ""}),
        mem_reader=lambda: 90.0, compose_runner=compose_runner, do_health_check=False,
        write_registry=False, lock_path=tmp_path / "cap.lock")
    return rec, captured


def test_standup_flag_off_writes_no_authority_overlay(tmp_path):
    """Flag OFF => byte-identical: no authority overlay, no authority record, no -f authority.yml."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"spend_money": "open"})
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "p",
              "authority_envelope": {"dispositions": {"deploy": "open"}}}
    rec, captured = _standup(repo, packet, {}, tmp_path)  # flag not in env
    assert rec["status"] == "executed"
    assert rec.get("authority_envelope") is None   # key absent flag-off (byte-identical record)
    assert not (Path(rec["state_dir"]) / "authority.yml").exists()
    assert "authority.yml" not in " ".join(captured["args"])


_SAME = object()  # sentinel: authenticated id defaults to the (honest) requester id


def _packet(requester, dispositions=None, *, token=None, authenticated=_SAME, sign=True):
    """Build a stored packet AS THE PROPOSE ENDPOINT WOULD after HIGH-#1 binding: the host resolves
    the parent from the SIGNED ``authenticated_requester_instance_id`` (stamped from the caller's own
    AQ_INSTANCE_ID + an HMAC signature under the host secret), NOT the guest ``requester_instance_id``.

    ``authenticated`` defaults to ``requester`` (honest caller). Pass a DIFFERENT value to model a
    FORGERY, or ``None`` to model a packet with no authenticated field. ``sign=True`` mints a VALID
    signature (a real propose-originated row, which holds the host secret); ``sign=False`` mints NO
    signature (a direct-SQL / raw daemon/broker row from a caller WITHOUT the secret)."""
    ae = {"dispositions": dispositions} if dispositions is not None else {"dispositions": {}}
    if token is not None:
        ae["widen_authorization"] = token
    pkt = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": requester,
           "authority_envelope": ae}
    auth = requester if authenticated is _SAME else authenticated
    if auth is not None:
        pkt["authenticated_requester_instance_id"] = auth
        if sign:
            sig = ra.sign_requester_identity(auth, env={ra.IDENTITY_SECRET_ENV: _ID_SECRET})
            if sig is not None:
                pkt["authenticated_requester_sig"] = sig
    return pkt


def _grant(rec) -> dict:
    overlay = (Path(rec["state_dir"]) / "authority.yml").read_text()
    return json.loads(_grant_value_from_overlay(overlay))


def test_standup_intersects_against_requester_recorded_effective(tmp_path):
    """The host intersects against the REQUESTER's recorded effective envelope (persisted at ITS
    stand-up), NOT repo_root/instance.yaml. An unauthorized widen beyond that parent is stripped."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"commit": "open"})   # source yaml is irrelevant to inheritance now
    token = "op-signed"
    # gen-1: root-seed requester is unknown => DEFAULT parent; a host token opens `commit`.
    r1, cap1 = _standup(repo, _packet("root-seed", {"commit": "open"}, token=token),
                        {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    assert _grant(r1)["commit"] == DISPOSITION_OPEN
    assert "authority.yml" in " ".join(cap1["args"])
    # gen-2: child of R1 requests commit + deploy WITHOUT a token. commit survives (parent had it);
    # deploy is a widen beyond R1 => stripped.
    r2, _ = _standup(repo, _packet(r1["instance_id"], {"commit": "open", "deploy": "open"}),
                     {ra.FLAG: "1"}, tmp_path)
    assert r2["authority_envelope"]["stripped_widen"] == ["deploy"]
    assert _grant(r2)["commit"] == DISPOSITION_OPEN
    assert _grant(r2)["deploy"] == DISPOSITION_GATED


def test_standup_host_token_authorizes_widen(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    token = "operator-signed"
    # unknown requester => DEFAULT parent; the token authorizes opening `deploy` beyond it.
    rec, _ = _standup(repo, _packet("root-seed", {"deploy": "open"}, token=token),
                      {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    ae = rec["authority_envelope"]
    assert ae["granted_widen"] == ["deploy"] and ae["authorized"] is True
    assert _grant(rec)["deploy"] == DISPOSITION_OPEN


def test_standup_unknown_requester_fails_closed(tmp_path):
    """A requester that is not a known replica (e.g. the root seed) => parent is the fully-gated
    DEFAULT => the child cannot open a class it requests without a token."""
    repo = tmp_path / "repo"
    repo.mkdir()
    rec, _ = _standup(repo, _packet("nobody-knows-me", {"commit": "open"}),
                      {ra.FLAG: "1"}, tmp_path)
    assert _grant(rec)["commit"] == DISPOSITION_GATED


def test_standup_tampered_stored_packet_cannot_widen(tmp_path):
    """A stored packet that BYPASSED authenticated propose (forged token, and — modelling the
    tamper — NO authenticated requester field) is fail-closed host-side: no authenticated requester
    => fully-gated DEFAULT parent, and the forged token is rejected. The host never trusts the
    stored envelope."""
    repo = tmp_path / "repo"
    repo.mkdir()
    rec, _ = _standup(
        repo,
        _packet("root-seed", {"spend_money": "open"}, token="forged", authenticated=None),
        {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: "the-real-one"}, tmp_path)
    assert _grant(rec)["spend_money"] == DISPOSITION_GATED  # no authenticated requester + forged token


def test_three_generation_narrowing_constrains_grandchild(tmp_path):
    """HIGH finding #1 (red-first): a gen-2 replica whose envelope was NARROWED must constrain its
    gen-3 grandchild. The grandchild reopening that class WITHOUT a token stays GATED — it does NOT
    reset to the wide ROOT. This FAILS the repo_root-parent impl (which would read the root's wide
    instance.yaml and open the class for gen-3)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"spend_money": "open"})  # WIDE root/source — the trap for the old impl
    token = "op-signed"
    # gen-1: root-seed (unknown) => DEFAULT parent; token opens spend_money on R1.
    r1, _ = _standup(repo, _packet("root-seed", {"spend_money": "open"}, token=token),
                     {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    assert _grant(r1)["spend_money"] == DISPOSITION_OPEN
    # gen-2: child of R1 requests nothing => spend_money NARROWED back to gated (no token needed).
    r2, _ = _standup(repo, _packet(r1["instance_id"], {}), {ra.FLAG: "1"}, tmp_path)
    assert _grant(r2)["spend_money"] == DISPOSITION_GATED
    # gen-3: grandchild of R2 tries to REOPEN spend_money WITHOUT a token.
    r3, _ = _standup(repo, _packet(r2["instance_id"], {"spend_money": "open"}),
                     {ra.FLAG: "1"}, tmp_path)  # NO token
    # Constrained by gen-2's narrowed RECORDED envelope, NOT reset to the wide root.
    assert _grant(r3)["spend_money"] == DISPOSITION_GATED
    assert r3["authority_envelope"]["stripped_widen"] == ["spend_money"]


def test_requester_recorded_envelope_fails_closed(tmp_path):
    """Unit: unknown requester => DEFAULT; a known replica record with NO recorded envelope =>
    DEFAULT (fail-closed), never something wider."""
    from runner.authority_envelope import DEFAULT_ENVELOPE
    st = tmp_path / "st"
    (st / "aq-replica-x").mkdir(parents=True)
    (st / "aq-replica-x" / "replica.json").write_text(json.dumps(
        {"instance_id": "known-1"}))  # a known replica, but NO authority_envelope recorded
    assert hrs.requester_recorded_envelope("does-not-exist", state_root=st).as_dict() \
        == DEFAULT_ENVELOPE.as_dict()
    assert hrs.requester_recorded_envelope("known-1", state_root=st).as_dict() \
        == DEFAULT_ENVELOPE.as_dict()
    # empty / None (unauthenticated requester) => fail-closed DEFAULT, never a scan for "".
    assert hrs.requester_recorded_envelope("", state_root=st).as_dict() \
        == DEFAULT_ENVELOPE.as_dict()
    assert hrs.requester_recorded_envelope(None, state_root=st).as_dict() \
        == DEFAULT_ENVELOPE.as_dict()


def test_ADVERSARIAL_forged_requester_id_inherits_wider_ancestor(tmp_path):
    """RED (attack #1 escalation — daemon/direct-SQL forgery): stamping the authenticated id at
    PROPOSE is not enough. The daemon and broker pass STORED / operator packets straight to stand_up,
    and aq_loop (the mgmt API's own DB role) can INSERT a forged proposal row DIRECTLY — setting
    ``authenticated_requester_instance_id`` = a WIDER ancestor R1, status=auto_approved, bypassing
    propose. Without a host signature on that id it would inherit R1's width with no token.

    After the fix, stand_up VERIFIES an HMAC signature of the authenticated id under the host secret
    on EVERY path. The forger (aq_loop / guest) lacks the secret, so it cannot mint a valid signature
    for R1 — nor does replaying R2's own valid signature help (it signs R2, not R1). The unsigned /
    mismatched-signature row FAILS CLOSED to the fully-gated DEFAULT parent => the grandchild stays
    GATED. This asserts the SECURE outcome; it FAILS on the pre-fix branch (which trusts the
    unsigned forged id and inherits R1's width)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"spend_money": "open"})
    token = "op-signed"
    # gen-1 (R1): token opens spend_money -> R1's RECORDED envelope has spend_money OPEN.
    r1, _ = _standup(repo, _packet("root-seed", {"spend_money": "open"}, token=token),
                     {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    assert _grant(r1)["spend_money"] == DISPOSITION_OPEN
    # gen-2 (R2): honest child of R1, NARROWED (requests nothing) -> spend_money GATED.
    r2, _ = _standup(repo, _packet(r1["instance_id"], {}), {ra.FLAG: "1"}, tmp_path)
    assert _grant(r2)["spend_money"] == DISPOSITION_GATED
    # ATTACK (direct-SQL / raw daemon row, bypassing propose): forge authenticated id = R1 (wider
    # grandparent), NO valid signature — the forger lacks the host secret. Model the strongest replay:
    # attach R2's OWN legitimate signature (which the attacker can read from R2's stored row).
    forged = _packet(r1["instance_id"], {"spend_money": "open"}, sign=False)  # no valid sig
    forged["authenticated_requester_sig"] = ra.sign_requester_identity(
        r2["instance_id"], env={ra.IDENTITY_SECRET_ENV: _ID_SECRET})  # replayed R2 sig (mismatched)
    r3, _ = _standup(repo, forged, {ra.FLAG: "1"}, tmp_path)
    # SECURE: signature of R2 does not verify against forged id R1 -> unauthenticated -> DEFAULT -> gated.
    assert _grant(r3)["spend_money"] == DISPOSITION_GATED
    assert r3["authority_envelope"]["stripped_widen"] == ["spend_money"]


def test_standup_valid_authenticated_id_but_missing_signature_fails_closed(tmp_path):
    """The signature gate specifically: a KNOWN wide ancestor id with NO signature (a raw
    daemon/broker packet, or a direct-SQL insert) resolves to the fully-gated DEFAULT, not the wide
    ancestor. Proves the id alone is never trusted without a valid host signature."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"spend_money": "open"})
    token = "op-signed"
    r1, _ = _standup(repo, _packet("root-seed", {"spend_money": "open"}, token=token),
                     {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    assert _grant(r1)["spend_money"] == DISPOSITION_OPEN
    # A child naming R1 as its parent but carrying NO signature (sign=False) => fail-closed DEFAULT.
    child, _ = _standup(repo, _packet(r1["instance_id"], {"spend_money": "open"}, sign=False),
                        {ra.FLAG: "1"}, tmp_path)
    assert _grant(child)["spend_money"] == DISPOSITION_GATED


def test_standup_signature_under_wrong_secret_fails_closed(tmp_path):
    """A signature minted under a DIFFERENT secret (a guest that guessed/forged its own secret) does
    not verify against the host secret => fail-closed DEFAULT."""
    repo = tmp_path / "repo"
    repo.mkdir()
    token = "op-signed"
    r1, _ = _standup(repo, _packet("root-seed", {"spend_money": "open"}, token=token),
                     {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    forged = _packet(r1["instance_id"], {"spend_money": "open"}, sign=False)
    forged["authenticated_requester_sig"] = ra.sign_requester_identity(
        r1["instance_id"], env={ra.IDENTITY_SECRET_ENV: "the-WRONG-secret"})
    child, _ = _standup(repo, forged, {ra.FLAG: "1"}, tmp_path)
    assert _grant(child)["spend_money"] == DISPOSITION_GATED


def test_non_ascii_widen_marker_fails_closed_not_crash(tmp_path):
    """LOW-#5 regression: a guest-controlled NON-ASCII widen_authorization must fail-closed (widen
    stripped), never raise. hmac.compare_digest on a non-ASCII *str* raises TypeError; comparing as
    bytes avoids the crash. Exercises both the unit and the full stand-up path."""
    parent = _env(commit=1)
    request = {"dispositions": {"deploy": "open"}, "widen_authorization": "é-forged"}
    # Unit: must not raise; the (non-matching, non-ASCII) marker is simply unauthorized.
    assert ra.authorize_widen("é-forged", {ra.WIDEN_TOKEN_ENV: "real"}) is False
    res = ra.resolve_child_envelope(parent, request, host_env={ra.WIDEN_TOKEN_ENV: "real"})
    assert res.effective.disposition("deploy") == DISPOSITION_GATED
    assert res.stripped_widen == ("deploy",)
    # Even if host token itself is non-ASCII and the marker matches, it must still compare cleanly.
    assert ra.authorize_widen("tökén", {ra.WIDEN_TOKEN_ENV: "tökén"}) is True


# --- identity sign/verify unit -----------------------------------------------
def test_sign_and_verify_requester_identity_roundtrip():
    env = {ra.IDENTITY_SECRET_ENV: "s3cret"}
    sig = ra.sign_requester_identity("urn:uuid:abc", env=env)
    assert isinstance(sig, str) and sig
    assert ra.verify_requester_identity("urn:uuid:abc", sig, env=env) is True
    # wrong id, wrong secret, tampered sig, missing sig, non-str, non-ASCII all fail-closed (no crash)
    assert ra.verify_requester_identity("urn:uuid:OTHER", sig, env=env) is False
    assert ra.verify_requester_identity("urn:uuid:abc", sig, env={ra.IDENTITY_SECRET_ENV: "x"}) is False
    assert ra.verify_requester_identity("urn:uuid:abc", sig[:-1] + ("0" if sig[-1] != "0" else "1"), env=env) is False
    assert ra.verify_requester_identity("urn:uuid:abc", None, env=env) is False
    assert ra.verify_requester_identity("urn:uuid:abc", 12345, env=env) is False
    assert ra.verify_requester_identity("urn:uuid:abc", "é-not-a-valid-sig", env=env) is False


def test_sign_requires_secret_and_id():
    assert ra.sign_requester_identity("id", env={}) is None            # no secret => cannot sign
    assert ra.sign_requester_identity("", env={ra.IDENTITY_SECRET_ENV: "s"}) is None  # no id
    # verify fails closed when the host secret is unset (nothing can be authenticated)
    assert ra.verify_requester_identity("id", "anything", env={}) is False


def test_guest_cannot_forge_identity_signature():
    # A guest lacks the host identity secret; its env cannot produce a signature that verifies.
    host_env = {ra.IDENTITY_SECRET_ENV: "the-host-secret"}
    guest_attempt = ra.sign_requester_identity("wide-ancestor", env={})  # guest has no secret
    assert guest_attempt is None
    assert ra.verify_requester_identity("wide-ancestor", "guessed-sig", env=host_env) is False
    # AQ_REPLICATION_IDENTITY_SECRET is guest-forbidden (AQ_REPLICATION_* prefix denylist).
    assert mp.forbidden_key_reason(ra.IDENTITY_SECRET_ENV) is not None
    assert mp.forbidden_key_reason(ra.WIDEN_TOKEN_ENV) is not None


def test_denylist_is_case_and_whitespace_insensitive():
    # #5 defense-in-depth: normalized (strip+upper) exact-match so variants are denied by the denylist
    # too (deny-by-default already blocks unknown keys; this makes the reason specific for variants).
    for variant in ("aq_instance_id", " AQ_INSTANCE_ID ", "Aq_Instance_Id"):
        assert mp.forbidden_key_reason(variant) is not None
    for variant in ("aq_authority_envelope_grant", " AQ_AUTHORITY_MAY_SELF_WIDEN"):
        assert mp.forbidden_key_reason(variant) is not None


def _grant_value_from_overlay(overlay_text: str) -> str:
    import yaml
    doc = yaml.safe_load(overlay_text)
    return doc["services"]["app"]["environment"][ra.GRANT_ENV]
