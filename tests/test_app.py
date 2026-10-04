import pytest

from app import create_app


def test_home_page_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Real-Time EV Fleet Monitoring" in resp.data


def test_healthz_reports_database_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "database": "ok"}


def test_unknown_page_returns_html_404(client):
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    assert b"couldn't find that page" in resp.data


def test_unknown_api_route_returns_json_404(client):
    resp = client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Not Found"


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError, match="Unknown APP_ENV"):
        create_app("staging")


def test_production_refuses_placeholder_secret(monkeypatch):
    monkeypatch.setattr("app.config.ProductionConfig.SECRET_KEY", "change-me")
    with pytest.raises(RuntimeError, match="placeholder"):
        create_app("production")


def test_favicon(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 301 and resp.location.endswith("/static/favicon.svg")
    assert client.get("/static/favicon.svg").status_code == 200
    assert b'rel="icon"' in client.get("/").data
