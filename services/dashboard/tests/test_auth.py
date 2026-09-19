from fastapi.testclient import TestClient

from app import main


def test_dashboard_is_open_without_password(monkeypatch):
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "")
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200


def test_dashboard_requires_password_when_configured(monkeypatch):
    monkeypatch.setattr(main, "DASHBOARD_USER", "demo")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "secret")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 401
        assert client.get("/", auth=("demo", "wrong")).status_code == 401
        assert client.get("/", auth=("demo", "secret")).status_code == 200
        assert client.get("/api/routes").status_code == 401
