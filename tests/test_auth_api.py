from fastapi.testclient import TestClient

from management.api import app as app_module


class _FakeLogin:
    def start(self):
        return {"state": "pending", "connected": False, "pending": True,
                "verification_url": "https://auth.openai.com/codex/device",
                "user_code": "ABCD-12345", "error": None}

    def status(self):
        return {"state": "disconnected", "connected": False, "pending": False,
                "verification_url": None, "user_code": None, "error": None}

    def stop(self):
        pass


def test_device_start_rejects_cross_site_origin(monkeypatch):
    monkeypatch.setattr(app_module, "device_login", _FakeLogin())
    client = TestClient(app_module.app)
    response = client.post("/api/auth/device/start", headers={"origin": "https://evil.example"})
    assert response.status_code == 403


def test_device_start_accepts_loopback_ui_origin(monkeypatch):
    monkeypatch.setattr(app_module, "device_login", _FakeLogin())
    client = TestClient(app_module.app)
    response = client.post("/api/auth/device/start", headers={"origin": "http://localhost:8090"})
    assert response.status_code == 200
    assert response.json()["user_code"] == "ABCD-12345"
