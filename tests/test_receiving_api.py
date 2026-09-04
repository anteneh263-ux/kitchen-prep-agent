"""The receiving routes: JSON for machines, form posts for the kitchen screen.

The kitchen screen has no JavaScript and the service has no form-parsing
dependency, so both content types are handled by the same endpoint. These tests
pin that, and pin that a malformed event is refused rather than stored.
"""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from kitchen_prep import config, orchestrator
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import today_oslo


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A server with an isolated store and no model calls."""
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(orchestrator, "get_client", lambda: OfflineClient())
    from kitchen_prep import server

    return TestClient(server.app)


def test_a_receipt_is_recorded_and_returned(client):
    response = client.post(
        "/inventory/receipts",
        json={"item_id": "beef_patty", "qty_received": 38, "note": "one crate short"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "receipt"
    assert body["item_id"] == "beef_patty"
    assert body["qty"] == 38.0
    assert body["note"] == "one crate short"
    assert body["effective_date"] == today_oslo()
    assert body["adjustment_id"]
    assert body["recorded_at"]


def test_a_count_is_recorded_and_returned(client):
    response = client.post("/inventory/counts", json={"item_id": "tomato", "counted_qty": 2.5})

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "count"
    assert body["qty"] == 2.5


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/inventory/receipts", {"item_id": "not_an_ingredient", "qty_received": 1}),
        ("/inventory/receipts", {"item_id": "beef_patty", "qty_received": -5}),
        ("/inventory/receipts", {"item_id": "beef_patty"}),
        ("/inventory/receipts", {"item_id": "beef_patty", "qty_received": 1, "expiry_date": "soon"}),
        ("/inventory/counts", {"item_id": "beef_patty", "counted_qty": "plenty"}),
        ("/inventory/counts", {"counted_qty": 3}),
    ],
)
def test_a_malformed_event_is_refused_and_not_stored(client, path, payload):
    assert client.post(path, json=payload).status_code == 422
    listed = client.get("/inventory/adjustments").json()["adjustments"]
    assert listed == []


def test_a_body_that_is_not_json_is_a_client_error(client):
    response = client.post(
        "/inventory/counts",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_a_form_post_redirects_back_to_the_receiving_panel(client):
    response = client.post(
        "/inventory/counts",
        data={"item_id": "tomato", "counted_qty": "4", "lang": "en"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/?lang=en#inventory-receiving"
    assert client.get("/inventory/adjustments").json()["adjustments"][0]["qty"] == 4.0


def test_recorded_events_are_listed_for_their_effective_date(client):
    client.post("/inventory/receipts", json={"item_id": "bacon", "qty_received": 1})

    listing = client.get("/inventory/adjustments").json()
    assert listing["effective_date"] == today_oslo()
    assert [item["item_id"] for item in listing["adjustments"]] == ["bacon"]

    assert client.get("/inventory/adjustments", params={"date": "2026-01-01"}).json()["adjustments"] == []


def test_an_event_recorded_after_the_run_targets_the_next_open_day(client):
    """A planned day is frozen, so a late delivery must not rewrite it."""
    client.post("/runs/daily", json={"date": config.DEMO_DATE})

    response = client.post(
        "/inventory/receipts",
        json={
            "item_id": "beef_patty",
            "qty_received": 10,
            "order_by_date": config.DEMO_DATE,
            "effective_date": config.DEMO_DATE,
        },
    )

    assert response.status_code == 201
    assert response.json()["effective_date"] == "2026-08-15"


def test_the_kitchen_screen_offers_both_forms(client):
    client.post("/runs/daily", json={"date": config.DEMO_DATE})

    page = client.get("/").text

    assert 'id="inventory-receiving"' in page
    assert 'action="/inventory/receipts"' in page
    assert 'action="/inventory/counts"' in page
    assert 'href="#inventory-receiving"' in page


def test_a_recorded_correction_reaches_the_next_published_plan(client):
    client.post("/runs/daily", json={"date": config.DEMO_DATE})
    client.post(
        "/inventory/receipts",
        json={
            "item_id": "beef_patty",
            "qty_received": 0,
            "order_by_date": config.DEMO_DATE,
            "effective_date": "2026-08-15",
            "note": "delivery never arrived",
        },
    )

    client.post("/runs/daily", json={"date": "2026-08-15"})
    plan = client.get("/plans/latest").json()

    assert [item["note"] for item in plan["inventory_adjustments"]] == ["delivery never arrived"]
    assert plan["stock_variances"][0]["item_id"] == "beef_patty"
    assert plan["stock_variances"][0]["variance_qty"] < 0
