"""Security contract for the public, read-only Cloud Run surface."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from kitchen_prep import config


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    from kitchen_prep import public_server

    return TestClient(public_server.app)


def test_public_home_renders_without_a_plan(empty_client):
    response = empty_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.lower().startswith("<!doctype html")
    assert '<html lang="en">' in response.text
    assert "No plan has been published yet." in response.text


def test_public_home_can_switch_to_norwegian(empty_client):
    response = empty_client.get("/?lang=no")
    assert response.status_code == 200
    assert '<html lang="no">' in response.text
    assert "Ingen plan er publisert ennå." in response.text


def test_public_latest_returns_empty_state(empty_client):
    response = empty_client.get("/plans/latest")
    assert response.status_code == 200
    assert response.json() == {"detail": "no plans yet"}


def test_public_history_returns_empty_list(empty_client):
    response = empty_client.get("/plans")
    assert response.status_code == 200
    assert response.json() == []


def test_public_plan_by_date_returns_stable_missing_response(empty_client):
    response = empty_client.get("/plans/2026-08-14")
    assert response.status_code == 200
    assert response.json() == {"detail": "plan not found", "date": "2026-08-14"}


def test_public_health_is_ok(empty_client):
    response = empty_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_demo_page_is_available_without_exposing_production_mutations(empty_client):
    response = empty_client.get("/demo")
    assert response.status_code == 200
    assert "Interactive demo" in response.text
    assert empty_client.post("/runs/daily").status_code == 404
    assert empty_client.post(
        "/plans/2026-08-14/actions/beef_patty/approved"
    ).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/runs/daily"),
        ("post", "/plans/2026-08-14/actions/beef_patty/approved"),
        ("get", "/docs"),
        ("get", "/redoc"),
        ("get", "/openapi.json"),
    ],
)
def test_public_surface_does_not_expose_private_or_schema_routes(
    empty_client, method, path
):
    response = getattr(empty_client, method)(path)
    assert response.status_code == 404
