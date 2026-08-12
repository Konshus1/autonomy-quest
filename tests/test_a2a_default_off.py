"""DEFAULT-OFF proof (#4834 comms Phase 5): with AQ_A2A_ENABLE unset, the /a2a surface is NOT mounted.

The A2A façade is a NEW external-facing network surface, so it LANDS INERT: a default checkout and the
live deployment expose NO /a2a and NO /.well-known/agent-card.json (404). Only an explicit truthy
AQ_A2A_ENABLE mounts it. This reloads the REAL management app both ways to prove the mount is gated.
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi", reason="fastapi is a Docker-kit dependency (requirements.txt)")

from fastapi.testclient import TestClient  # noqa: E402

from management.api.a2a_router import a2a_enabled  # noqa: E402


def _reload_app(monkeypatch, enabled: bool):
    if enabled:
        monkeypatch.setenv("AQ_A2A_ENABLE", "1")
    else:
        monkeypatch.delenv("AQ_A2A_ENABLE", raising=False)
    monkeypatch.delenv("AQ_MGMT_DB_URL", raising=False)
    monkeypatch.delenv("AQ_DB_URL", raising=False)
    import management.api.app as app_module
    return importlib.reload(app_module)


@pytest.fixture(autouse=True)
def _restore_app_module():
    """Always reload app.py with the flag OFF after each test so we never leak a mounted /a2a into
    the rest of the suite (the default-off invariant must also hold across tests)."""
    yield
    import os
    os.environ.pop("AQ_A2A_ENABLE", None)
    import management.api.app as app_module
    importlib.reload(app_module)


def test_flag_helper_defaults_off():
    assert a2a_enabled({}) is False
    assert a2a_enabled({"AQ_A2A_ENABLE": ""}) is False
    assert a2a_enabled({"AQ_A2A_ENABLE": "0"}) is False
    assert a2a_enabled({"AQ_A2A_ENABLE": "false"}) is False
    assert a2a_enabled({"AQ_A2A_ENABLE": "1"}) is True
    assert a2a_enabled({"AQ_A2A_ENABLE": "on"}) is True


def test_default_deployment_exposes_no_a2a_surface(monkeypatch):
    app_module = _reload_app(monkeypatch, enabled=False)
    c = TestClient(app_module.app)
    # The façade routes are simply not mounted -> 404 (not merely 401/403).
    assert c.get("/.well-known/agent-card.json").status_code == 404
    assert c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/list", "params": {}}).status_code == 404
    # The native comms API is unchanged and still present (this phase touches only the new surface).
    assert c.get("/api/agent-comms").status_code == 200
    assert c.get("/health").status_code == 200


def test_enabling_the_flag_mounts_the_surface(monkeypatch):
    app_module = _reload_app(monkeypatch, enabled=True)
    c = TestClient(app_module.app)
    # Discovery is now served (public), and /a2a exists (401 = route present but needs a credential).
    assert c.get("/.well-known/agent-card.json").status_code == 200
    r = c.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/list", "params": {}})
    assert r.status_code == 401  # mounted, but auth is required (not 404)
