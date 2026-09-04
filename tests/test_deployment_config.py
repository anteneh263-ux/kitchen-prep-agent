"""Deployment configuration and server-startup checks for Cloud Run.

These tests assert only what deployment depends on: that the image starts the
existing FastAPI app on 0.0.0.0 using Cloud Run's $PORT, that local state and
secrets stay out of the build context, and that the four served routes behave as
documented. No business logic is exercised or constrained here.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
DEPLOY_DOC = ROOT / "deploy" / "cloud_run.md"

REQUIRED_DOCKERIGNORE_ENTRIES = [
    ".git",
    ".venv",
    ".env",
    "out",
    "__pycache__",
    ".pytest_cache",
    "*.pyc",
]


# --- Build configuration -------------------------------------------------


def test_dockerfile_exists():
    assert DOCKERFILE.is_file(), "Cloud Run deployment requires a Dockerfile"


def test_dockerfile_uses_supported_python_and_installs_requirements():
    text = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [ln for ln in text.splitlines() if ln.strip().startswith("FROM ")]
    assert from_lines, "Dockerfile must declare a base image"

    # pyproject.toml declares requires-python = ">=3.11".
    base = from_lines[0]
    assert "python:3." in base
    minor = int(base.split("python:3.")[1].split("-")[0].split(".")[0])
    assert minor >= 11, f"base image {base!r} is below the project's requires-python"

    assert "requirements.txt" in text, "dependencies must come from requirements.txt"


def test_dockerfile_start_command_serves_the_real_app_on_cloud_run_port():
    text = DOCKERFILE.read_text(encoding="utf-8")
    cmd = next((ln for ln in text.splitlines() if ln.strip().startswith("CMD")), "")
    assert cmd, "Dockerfile must define a CMD"
    assert "uvicorn" in cmd
    # Must start the existing app object, not a copy or a wrapper.
    assert "kitchen_prep.server:app" in cmd
    assert "--host 0.0.0.0" in cmd
    # Cloud Run injects the port; it must not be hardcoded on the port flag.
    assert "PORT" in cmd


def test_dockerfile_does_not_copy_local_state_or_secrets():
    text = DOCKERFILE.read_text(encoding="utf-8")
    copy_sources = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("COPY "):
            parts = stripped.split()[1:]
            copy_sources.extend(p for p in parts[:-1] if not p.startswith("--"))

    assert copy_sources, "Dockerfile must copy the application in"
    # A blanket `COPY . .` would pull in .env, out/ and caches from the context.
    assert "." not in copy_sources, "copy explicit paths, not the whole context"
    for forbidden in (".env", ".git", ".venv", "out"):
        assert not any(forbidden in src for src in copy_sources)


def test_dockerignore_covers_the_required_entries():
    assert DOCKERIGNORE.is_file(), ".dockerignore is required"
    entries = {
        ln.strip()
        for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    missing = [e for e in REQUIRED_DOCKERIGNORE_ENTRIES if e not in entries]
    assert not missing, f".dockerignore is missing: {missing}"


def test_process_local_public_demo_is_deployed_with_one_instance():
    text = DEPLOY_DOC.read_text(encoding="utf-8")
    viewer_section = text.split("## Public viewer and isolated demo sandbox", 1)[1]
    assert "--max-instances=1" in viewer_section
    assert "roles/datastore.viewer" in viewer_section
    assert "no Gemini secret" in viewer_section


# --- Served routes -------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from kitchen_prep import (
    config,
    orchestrator,
)
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.pipeline.baseline import baseline_forecast


@pytest.fixture()
def offline_env(tmp_path, monkeypatch):
    """Isolate the store and force the offline model path (no network, no key)."""
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(orchestrator, "get_client", lambda: OfflineClient())


@pytest.fixture()
def empty_client(offline_env):
    """A server whose store has no published plan yet."""
    from kitchen_prep import server

    return TestClient(server.app)


def test_healthz_is_ok(empty_client):
    resp = empty_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_home_renders_before_any_plan_exists(empty_client):
    """Cloud Run serves traffic before the first scheduled run; / must not 500."""
    resp = empty_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.text.lower().startswith("<!doctype html")


def test_plans_latest_empty_state_matches_documented_behaviour(empty_client):
    resp = empty_client.get("/plans/latest")
    assert resp.status_code == 200
    assert resp.json() == {"detail": "no plans yet"}


def test_runs_daily_is_the_scheduled_execution_endpoint(empty_client):
    """Cloud Scheduler POSTs to this route; the response is the run summary."""
    client = empty_client
    resp = client.post("/runs/daily", json={"date": config.DEMO_DATE})
    assert resp.status_code == 200

    body = resp.json()
    assert body["date"] == config.DEMO_DATE
    for key in (
        "expected_covers",
        "prep_tasks",
        "prep_shortfalls",
        "replenishment_orders",
        "waste_flagged",
        "forecast_source",
    ):
        assert key in body

    # The plan the run published is what /plans/latest serves.
    latest = client.get("/plans/latest").json()
    assert latest["date"] == config.DEMO_DATE


def test_runs_daily_is_idempotent_for_scheduler_retries(empty_client):
    first = empty_client.post("/runs/daily", json={"date": config.DEMO_DATE}).json()
    second = empty_client.post("/runs/daily", json={"date": config.DEMO_DATE}).json()
    assert first == second


# --- Image completeness --------------------------------------------------


def test_fallback_forecast_needs_the_gitignored_sales_history(tmp_path, monkeypatch):
    """sales_history.csv is gitignored, so the image must generate it at build time.

    First prove the dependency is real, then assert the Dockerfile satisfies it —
    so a future change cannot drop that build step and ship an image that crashes
    whenever the forecast falls back.
    """
    monkeypatch.setattr(config, "SALES_HISTORY_PATH", tmp_path / "absent.csv")
    with pytest.raises(FileNotFoundError):
        baseline_forecast(config.DEMO_DATE, 80)

    assert "generate_sales_history.py" in DOCKERFILE.read_text(encoding="utf-8")
