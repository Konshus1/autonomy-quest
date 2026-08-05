"""Task #4407 slice 2c — loop -> management causal-principle refresh bridge (best-effort)."""

from __future__ import annotations

from runner import causal_sync


def test_mgmt_base_url_prefers_explicit_url():
    assert causal_sync.mgmt_base_url({"AQ_MGMT_URL": "http://x:9/"}) == "http://x:9"


def test_mgmt_base_url_builds_from_port():
    assert causal_sync.mgmt_base_url({"AQ_MGMT_PORT": "8090"}) == "http://127.0.0.1:8090"


def test_mgmt_base_url_none_when_unconfigured():
    # the unit-test path (drive Loop.cycle with no API up) must resolve to a clean skip
    assert causal_sync.mgmt_base_url({}) is None


def test_mgmt_base_url_disabled_by_flag():
    assert causal_sync.mgmt_base_url({"AQ_MGMT_PORT": "8090", "AQ_CAUSAL_AUTOMINE": "0"}) is None


def test_refresh_is_best_effort_on_dead_endpoint():
    # nothing listening -> connection refused -> None, never an exception into the loop
    assert causal_sync.refresh_causal_principles("http://127.0.0.1:1", timeout=0.5) is None


def test_consult_helpers_are_best_effort_on_dead_endpoint():
    # both consult helpers must swallow a dead endpoint and return None, never raise into the loop
    assert causal_sync.assess_plan_certainty("http://127.0.0.1:1", "outreach", "measure_up", timeout=0.5) is None
    assert causal_sync.record_outcome_surprise(
        "http://127.0.0.1:1", "outreach", "measure_up", 0.3, True, timeout=0.5) is None


def test_scheme_less_base_url_never_raises():
    # a garbage/scheme-less base_url raises 'unknown url type' at Request construction; that must
    # be swallowed (it now sits inside _post_json's try) so the PRE-ACT consult can't wedge the loop
    assert causal_sync.assess_plan_certainty("myhost-no-scheme", "outreach", "measure_up", timeout=0.5) is None
    assert causal_sync.refresh_causal_principles("myhost-no-scheme", timeout=0.5) is None


def test_assess_plan_certainty_survives_malformed_shapes(monkeypatch):
    # adversarial finding 1: a contract-drifted (but valid-JSON) response must yield None, not raise
    bad_bodies = [
        {},                                                   # no per_step
        {"per_step": []},                                     # empty
        {"per_step": [{"covered": True}]},                    # missing certainty -> KeyError
        {"per_step": [{"covered": True, "certainty": "hi"}]},  # non-float -> ValueError
        {"per_step": ["oops"]},                               # non-dict step -> AttributeError
    ]
    for body in bad_bodies:
        monkeypatch.setattr(causal_sync, "_post_json", lambda *a, **k: body)
        assert causal_sync.assess_plan_certainty("http://x", "a", "b") is None
    # a well-formed covered step still parses
    monkeypatch.setattr(causal_sync, "_post_json",
                        lambda *a, **k: {"per_step": [{"covered": True, "certainty": 0.42}]})
    assert causal_sync.assess_plan_certainty("http://x", "a", "b") == 0.42


def test_refresh_survives_malformed_mined(monkeypatch):
    # adversarial finding 2: {"mined": null} / non-numeric must yield None, not raise (post-commit)
    for body in ({"mined": None}, {"mined": "abc"}):
        monkeypatch.setattr(causal_sync, "_post_json", lambda *a, **k: body)
        assert causal_sync.refresh_causal_principles("http://x") is None
    # a response missing the key just defaults to 0 (responded, no count) — no crash, no false count
    monkeypatch.setattr(causal_sync, "_post_json", lambda *a, **k: {"other": 1})
    assert causal_sync.refresh_causal_principles("http://x") == 0
    monkeypatch.setattr(causal_sync, "_post_json", lambda *a, **k: {"mined": 3})
    assert causal_sync.refresh_causal_principles("http://x") == 3


def test_loop_imports_bridge_symbol():
    # the loop must actually reference the bridge (guards against a silently dropped wire-in)
    import runner.loop as loop

    assert loop.causal_sync is causal_sync


# ---- G1 / BB #2301: coherence-frontier fetch bridge (best-effort) ----

def test_fetch_high_surprise_edges_best_effort_on_dead_endpoint():
    # nothing listening -> None, never an exception into the loop
    assert causal_sync.fetch_high_surprise_edges("http://127.0.0.1:1", timeout=0.5) is None


def test_fetch_high_surprise_edges_scheme_less_never_raises():
    assert causal_sync.fetch_high_surprise_edges("myhost-no-scheme", timeout=0.5) is None


def test_fetch_high_surprise_edges_survives_malformed_shapes(monkeypatch):
    # contract-drifted bodies must yield None, not raise
    for body in ({}, {"items": "nope"}, {"items": 7}, {"other": 1}):
        monkeypatch.setattr(causal_sync, "_get_json", lambda *a, **k: body)
        assert causal_sync.fetch_high_surprise_edges("http://x") is None


def test_fetch_high_surprise_edges_empty_items_yields_empty_frontier(monkeypatch):
    monkeypatch.setattr(causal_sync, "_get_json", lambda *a, **k: {"items": []})
    assert causal_sync.fetch_high_surprise_edges("http://x") == []


def test_fetch_high_surprise_edges_derives_seed_first_frontier(monkeypatch):
    # a surprised edge + a confirm-only neighbor: seed first, neighbor still surfaced, no confirms
    items = [
        {"cause": "deploy", "effect": "measure_up", "formality": "evidential",
         "strictness": "advisory", "directness": "predicate",
         "executor": {"kind": "predicate", "ref": "x.sql"}, "predicted_certainty": 0.9,
         "evidence": [{"signal": "demote", "surprise": 0.9}]},
        {"cause": "deploy", "effect": "cache_warm", "formality": "evidential",
         "strictness": "advisory", "directness": "predicate",
         "executor": {"kind": "predicate", "ref": "y.sql"}, "predicted_certainty": 0.5,
         "evidence": [{"signal": "confirm", "surprise": 0.5}]},
    ]
    monkeypatch.setattr(causal_sync, "_get_json", lambda *a, **k: {"items": items})
    f = causal_sync.fetch_high_surprise_edges("http://x", surprised_edge=("deploy", "measure_up"))
    assert f is not None and len(f) == 2
    assert f[0]["seed"] is True and f[0]["cause"] == "deploy"
    assert f[1]["neighbor"] is True and f[1]["effect"] == "cache_warm"


def test_fetch_high_surprise_edges_max_items_bounds(monkeypatch):
    items = [
        {"cause": f"c{i}", "effect": f"e{i}", "formality": "evidential",
         "strictness": "advisory", "directness": "predicate",
         "executor": {"kind": "predicate", "ref": f"r{i}.sql"},
         "predicted_certainty": 0.9 - i * 0.05,
         "evidence": [{"signal": "demote", "surprise": 0.9 - i * 0.05}]}
        for i in range(4)
    ]
    monkeypatch.setattr(causal_sync, "_get_json", lambda *a, **k: {"items": items})
    f = causal_sync.fetch_high_surprise_edges("http://x", max_items=2)
    assert f is not None and len(f) == 2
    assert causal_sync.fetch_high_surprise_edges("http://x", max_items=0) == []


def test_loop_imports_fetch_symbol():
    # guards against the bridge symbol being silently dropped from the loop's import surface
    assert hasattr(causal_sync, "fetch_high_surprise_edges")
