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


def _standup(repo_root, packet, env, tmp_path):
    captured = {}

    def compose_runner(compose_args, *, cwd, env=None, timeout=None):
        captured["args"] = compose_args
        return subprocess.CompletedProcess(compose_args, 0, stdout="", stderr="")

    rec = hrs.stand_up_replica_stack(
        packet, status="auto_approved", repo_root=repo_root, git_sha="cafef00d",
        state_root=tmp_path / "st", env=env, docker=_fake_docker({"ps": ""}),
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


def test_standup_flag_on_intersects_against_ground_truth_parent(tmp_path):
    """Host uses the GROUND-TRUTH parent (instance.yaml), NOT the packet — an unauthorized widen is
    stripped, and the child overlay carries the intersected effective envelope."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"commit": "open"})   # parent opens ONLY commit
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "p",
              "authority_envelope": {"dispositions": {"commit": "open", "deploy": "open"}}}
    rec, captured = _standup(repo, packet, {ra.FLAG: "1"}, tmp_path)
    assert rec["status"] == "executed"
    ae = rec["authority_envelope"]
    assert ae is not None and ae["stripped_widen"] == ["deploy"]
    overlay = (Path(rec["state_dir"]) / "authority.yml").read_text()
    grant = json.loads(_grant_value_from_overlay(overlay))
    assert grant["commit"] == DISPOSITION_OPEN and grant["deploy"] == DISPOSITION_GATED
    assert "authority.yml" in " ".join(captured["args"])


def test_standup_host_token_authorizes_widen(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"commit": "open"})
    token = "operator-signed"
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "p",
              "authority_envelope": {"dispositions": {"commit": "open", "deploy": "open"},
                                     "widen_authorization": token}}
    rec, _ = _standup(repo, packet, {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: token}, tmp_path)
    ae = rec["authority_envelope"]
    assert ae["granted_widen"] == ["deploy"] and ae["authorized"] is True
    grant = json.loads(_grant_value_from_overlay((Path(rec["state_dir"]) / "authority.yml").read_text()))
    assert grant["deploy"] == DISPOSITION_OPEN


def test_standup_unreadable_parent_fails_closed(tmp_path):
    """No parent instance.yaml => parent treated as fully-gated => child cannot open anything
    (unauthorized), even a class it requests."""
    repo = tmp_path / "repo"
    repo.mkdir()   # deliberately NO instance.yaml
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "p",
              "authority_envelope": {"dispositions": {"commit": "open"}}}
    rec, _ = _standup(repo, packet, {ra.FLAG: "1"}, tmp_path)
    grant = json.loads(_grant_value_from_overlay((Path(rec["state_dir"]) / "authority.yml").read_text()))
    assert grant["commit"] == DISPOSITION_GATED


def test_standup_tampered_stored_packet_cannot_widen(tmp_path):
    """A stored packet that bypassed propose (a raw open request with no valid token) is still
    intersected host-side — the host never trusts the stored envelope."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_parent_instance(repo, {"commit": "open"})
    packet = {"mode": "straight_copy", "mission_id": "m", "requester_instance_id": "p",
              "authority_envelope": {"dispositions": {"spend_money": "open"},
                                     "widen_authorization": "forged"}}
    rec, _ = _standup(repo, packet, {ra.FLAG: "1", ra.WIDEN_TOKEN_ENV: "the-real-one"}, tmp_path)
    grant = json.loads(_grant_value_from_overlay((Path(rec["state_dir"]) / "authority.yml").read_text()))
    assert grant["spend_money"] == DISPOSITION_GATED  # forged token rejected host-side


def _grant_value_from_overlay(overlay_text: str) -> str:
    import yaml
    doc = yaml.safe_load(overlay_text)
    return doc["services"]["app"]["environment"][ra.GRANT_ENV]
