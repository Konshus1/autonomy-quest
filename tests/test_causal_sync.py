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


# --- BB #2430: the RETIRED T11 reflect-phase detector must self-label -------------------
#
# Kevin chose option (b) 2026-08-09: leave the non-discriminating detector running for
# continuity, but stamp its output so nobody can cite it as evidence. These tests assert the
# stamp REACHES THE PAYLOAD — not merely that a constant exists. A test that only checked the
# constant would pass against a build where the stamp was never applied, which is exactly the
# "did it fire?" failure this whole retirement is about.


def test_frame_expansion_episode_id_carries_known_artifact_stamp(monkeypatch):
    """The stamp must be ON the posted episode_id, because propose_dimension() carries
    episode_id into source_episodes — so this is what persists on every candidate."""
    captured = {}

    def fake_post(base_url, path, payload, timeout):
        captured["path"] = path
        captured["payload"] = payload
        return {"ok": True, "result": {}}

    monkeypatch.setattr(causal_sync, "_post_json", fake_post)
    causal_sync.feed_frame_expansion(
        "http://x:9", "outreach", "summary", "some insight about pricing verification", "ok", True)

    assert captured, "feed_frame_expansion posted nothing — the stamp cannot be asserted"
    episode_id = captured["payload"]["episodes"][0]["episode_id"]
    assert episode_id.startswith(causal_sync.KNOWN_ARTIFACT_PREFIX), (
        f"episode_id {episode_id!r} is unstamped — a candidate built from it would be "
        f"indistinguishable from evidence")
    # the original identity must survive the prefix, or provenance is lost in the other direction
    assert "cycle_outreach" in episode_id


def test_known_artifact_stamp_names_the_superseding_record():
    """A stamp that does not say WHAT superseded it sends the reader nowhere."""
    assert "2430" in causal_sync.KNOWN_ARTIFACT_PREFIX
    assert "NOT-EVIDENCE" in causal_sync.KNOWN_ARTIFACT_PREFIX


def test_frame_expansion_still_best_effort_on_dead_endpoint():
    """The stamp must not have turned a best-effort call into one that can raise into the loop."""
    assert causal_sync.feed_frame_expansion(
        "http://127.0.0.1:1", "outreach", "s", "an insight with several longer words", "ok", True
    ) is None


def test_live_outcome_carries_trusted_governance_evidence(monkeypatch):
    captured = {}
    def fake_post(base, path, payload, timeout, headers=None):
        captured.update(path=path, payload=payload, headers=headers)
        return {"ok": True}
    monkeypatch.setenv("AQ_GOVERNANCE_EVIDENCE_TOKEN", "secret")
    monkeypatch.setattr(causal_sync, "_post_json", fake_post)
    environment = {"environment_id": "run:7", "domain": "docs",
                   "mission_id": "m", "harness": "h"}
    causal_sync.record_outcome_surprise("http://x", "research", "measure_up", 0.8, False,
                                        environment=environment, evidence_ref="run:7",
                                        observed_delta=-2)
    assert captured["path"] == "/api/causal/record-outcome"
    assert captured["payload"]["environment"] == environment
    assert captured["payload"]["observed_delta"] == -2
    assert captured["headers"]["x-aq-governance-evidence-token"] == "secret"
