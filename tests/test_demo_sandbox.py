"""Interactive demo sandbox: real calculations, isolated synthetic effects."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from kitchen_prep import config
from kitchen_prep.demo import (
    DEMO_COVERS,
    DEMO_SHORTFALL,
    DemoInvalidTransition,
    DemoRegistry,
)

ROOT = Path(__file__).resolve().parents[1]


def _completed_registry() -> tuple[DemoRegistry, str, list[dict]]:
    registry = DemoRegistry(event_delay=0)
    session_id = registry.create()["session_id"]
    events = list(registry.run(session_id))
    return registry, session_id, events


def test_demo_pipeline_uses_real_tools_and_produces_one_controlled_shortfall():
    registry, session_id, events = _completed_registry()
    state = registry.get(session_id)
    assert state["status"] == "awaiting_decision"
    assert state["plan"]["expected_covers"] == DEMO_COVERS
    assert len(state["plan"]["forecast"]["dishes"]) == 6
    assert state["plan"]["prep_shortfalls"] == [
        {
            "item_id": "chicken_wings",
            "required": state["plan"]["prep_shortfalls"][0]["required"],
            "available": state["plan"]["prep_shortfalls"][0]["available"],
            "shortfall": DEMO_SHORTFALL,
        }
    ]
    assert [event["type"] for event in events] == [
        "sales_history_loaded",
        "covers_resolved",
        "weather_loaded",
        "forecast_completed",
        "recipes_expanded",
        "inventory_reconciled",
        "shortage_detected",
        "decision_required",
    ]
    assert all(event["tool"] for event in events)


def test_approve_executes_only_a_simulated_order_and_records_audit_events():
    registry, session_id, _ = _completed_registry()
    state = registry.decide(session_id, "approve")
    assert state["status"] == "mitigation_scheduled"
    assert state["order"]["status"] == "submitted"
    assert state["order"]["simulated"] is True
    assert state["order"]["qty"] == DEMO_SHORTFALL
    assert "before service" in state["result"]
    assert [event["type"] for event in state["events"][-3:]] == [
        "decision_approved",
        "order_submitted",
        "mitigation_scheduled",
    ]
    assert "production inventory was not modified" in state["events"][-1]["detail"]


def test_reject_records_decision_without_creating_an_order():
    registry, session_id, _ = _completed_registry()
    state = registry.decide(session_id, "reject")
    assert state["status"] == "rejected"
    assert state["order"] is None
    assert state["events"][-1]["type"] == "decision_rejected"


def test_demo_state_machine_rejects_duplicate_or_out_of_order_actions():
    registry = DemoRegistry(event_delay=0)
    session_id = registry.create()["session_id"]
    with pytest.raises(DemoInvalidTransition):
        registry.decide(session_id, "approve")
    list(registry.run(session_id))
    registry.decide(session_id, "approve")
    with pytest.raises(DemoInvalidTransition):
        registry.decide(session_id, "approve")
    with pytest.raises(DemoInvalidTransition):
        list(registry.run(session_id))


def test_demo_sessions_are_isolated():
    registry = DemoRegistry(event_delay=0)
    first = registry.create()["session_id"]
    second = registry.create()["session_id"]
    list(registry.run(first))
    registry.decide(first, "approve")
    assert registry.get(first)["status"] == "mitigation_scheduled"
    assert registry.get(second)["status"] == "created"
    assert registry.get(second)["events"] == []


@pytest.fixture()
def demo_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    from kitchen_prep import public_server

    monkeypatch.setattr(public_server, "demo_registry", DemoRegistry(event_delay=0))
    return TestClient(public_server.app)


def test_demo_page_is_explicitly_synthetic_and_has_one_run_button(demo_client):
    response = demo_client.get("/demo")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Interactive demo · simulated restaurant data" in response.text
    assert response.text.count("Run morning plan") >= 1
    assert "cannot change production plans, inventory or external systems" in response.text


def test_demo_post_requires_same_origin_custom_header(demo_client):
    assert demo_client.post("/demo/sessions").status_code == 403
    response = demo_client.post("/demo/sessions", headers={"X-Demo-Request": "1"})
    assert response.status_code == 200
    assert response.json()["status"] == "created"


def test_public_demo_route_streams_steps_then_accepts_a_decision(demo_client):
    created = demo_client.post(
        "/demo/sessions",
        headers={"X-Demo-Request": "1"},
    ).json()
    session_id = created["session_id"]
    streamed = demo_client.get(f"/demo/sessions/{session_id}/events")
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.text.count("event: step") == 8
    assert "event: complete" in streamed.text
    awaiting = demo_client.get(f"/demo/sessions/{session_id}").json()
    assert awaiting["status"] == "awaiting_decision"

    approved = demo_client.post(
        f"/demo/sessions/{session_id}/decision",
        headers={"X-Demo-Request": "1"},
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "mitigation_scheduled"


def test_demo_never_writes_to_the_configured_production_store(demo_client):
    created = demo_client.post(
        "/demo/sessions",
        headers={"X-Demo-Request": "1"},
    ).json()
    demo_client.get(f"/demo/sessions/{created['session_id']}/events")
    demo_client.post(
        f"/demo/sessions/{created['session_id']}/decision",
        headers={"X-Demo-Request": "1"},
        json={"action": "approve"},
    )
    assert demo_client.get("/plans").json() == []
    assert demo_client.get("/plans/latest").json() == {"detail": "no plans yet"}


def test_demo_rejects_invalid_or_repeated_decisions(demo_client):
    created = demo_client.post(
        "/demo/sessions",
        headers={"X-Demo-Request": "1"},
    ).json()
    session_id = created["session_id"]
    assert demo_client.post(
        f"/demo/sessions/{session_id}/decision",
        headers={"X-Demo-Request": "1"},
        json={"action": "delete_inventory"},
    ).status_code == 422
    demo_client.get(f"/demo/sessions/{session_id}/events")
    assert demo_client.post(
        f"/demo/sessions/{session_id}/decision",
        headers={"X-Demo-Request": "1"},
        json={"action": "reject"},
    ).status_code == 200
    assert demo_client.post(
        f"/demo/sessions/{session_id}/decision",
        headers={"X-Demo-Request": "1"},
        json={"action": "approve"},
    ).status_code == 409


def test_public_demo_code_has_no_production_write_or_secret_dependency():
    public_source = (ROOT / "kitchen_prep/public_server.py").read_text(encoding="utf-8")
    demo_source = (ROOT / "kitchen_prep/demo.py").read_text(encoding="utf-8")
    assert "from .orchestrator" not in public_source
    assert "from .server" not in public_source
    for forbidden in (
        "get_store(",
        "FirestoreStore",
        "GOOGLE_API_KEY",
        "RealGeminiClient",
        "requests.",
    ):
        assert forbidden not in demo_source
