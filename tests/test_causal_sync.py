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


def test_loop_imports_bridge_symbol():
    # the loop must actually reference the bridge (guards against a silently dropped wire-in)
    import runner.loop as loop

    assert loop.causal_sync is causal_sync
