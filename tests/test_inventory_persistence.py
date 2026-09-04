"""Inventory snapshots carry stock forward without double-consuming on replay."""
import json

from kitchen_prep import config
from kitchen_prep.data_access.store import LocalJsonStore
from kitchen_prep.data_access.menu import ingredients_by_id
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep


def _snapshot(store: LocalJsonStore, date: str) -> dict:
    return json.loads((store.inventory_dir / f"{date}.json").read_text())


def test_next_day_starts_from_previous_output(tmp_store):
    first = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert first["inventory_basis"] == "date_input_output_snapshot"
    next_date = "2026-08-15"
    run_daily_prep(next_date, store=tmp_store, client=OfflineClient())

    first_snapshot = _snapshot(tmp_store, config.DEMO_DATE)
    next_snapshot = _snapshot(tmp_store, next_date)
    carried = [
        batch
        for batch in next_snapshot["input_batches"]
        if not batch["batch_id"].startswith("delivery-")
    ]
    assert carried == first_snapshot["output_batches"]
    assert sum(first["remaining_stock"].values()) == sum(
        batch["qty"]
        for batch in next_snapshot["input_batches"]
        if not batch["batch_id"].startswith("delivery-")
    )


def test_orders_become_dated_batches_on_delivery_day(tmp_store):
    first = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    next_date = "2026-08-15"
    due = {
        order["item_id"]: order
        for order in first["replenishment_orders"]
        if order["delivery_date"] == next_date
    }

    run_daily_prep(next_date, store=tmp_store, client=OfflineClient())
    arrivals = {
        batch["item_id"]: batch
        for batch in _snapshot(tmp_store, next_date)["input_batches"]
        if batch["batch_id"].startswith("delivery-")
    }

    assert arrivals.keys() == due.keys()
    for item_id, batch in arrivals.items():
        assert batch["qty"] == due[item_id]["order_qty"]
        assert batch["batch_id"] == f"delivery-{config.DEMO_DATE}-{item_id}"


def test_pending_delivery_prevents_duplicate_order(tmp_store):
    first = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert any(
        order["item_id"] == "chicken_wings" and order["delivery_date"] == "2026-08-16"
        for order in first["replenishment_orders"]
    )

    second = run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())
    assert all(order["item_id"] != "chicken_wings" for order in second["replenishment_orders"])


def test_inventory_epoch_starts_new_chain_without_old_pending_orders(tmp_store, monkeypatch):
    first = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert first["replenishment_orders"]

    epoch = "2026-08-15"
    monkeypatch.setenv("KP_INVENTORY_EPOCH", epoch)
    reset = run_daily_prep(epoch, store=tmp_store, client=OfflineClient())
    snapshot = _snapshot(tmp_store, epoch)

    assert reset["inventory_basis"] == "epoch_seed_snapshot"
    assert all(not batch["batch_id"].startswith("delivery-") for batch in snapshot["input_batches"])
    assert sum(batch["qty"] for batch in snapshot["input_batches"]) == sum(
        item["par_level"] for item in ingredients_by_id().values()
    )
    assert all(batch["batch_id"].startswith(f"epoch-{epoch}-") for batch in snapshot["input_batches"])
    assert not reset["waste_flagged"]


def test_force_replays_same_input_and_does_not_double_consume(tmp_store):
    original = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    before = _snapshot(tmp_store, config.DEMO_DATE)

    replayed = run_daily_prep(
        config.DEMO_DATE,
        store=tmp_store,
        client=OfflineClient(),
        force=True,
    )
    after = _snapshot(tmp_store, config.DEMO_DATE)

    assert after["input_batches"] == before["input_batches"]
    assert after["output_batches"] == before["output_batches"]
    assert replayed["remaining_stock"] == original["remaining_stock"]
    assert replayed["fefo_consumption"] == original["fefo_consumption"]


def test_idempotent_hit_does_not_change_inventory_snapshot(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    before = _snapshot(tmp_store, config.DEMO_DATE)
    returned = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    after = _snapshot(tmp_store, config.DEMO_DATE)
    assert returned == plan
    assert after == before


def test_empty_previous_output_is_not_reseeded(tmp_store):
    date1 = "2026-08-14"
    date2 = "2026-08-15"
    tmp_store.get_or_create_inventory_input(date1, [{"batch_id": "x"}])
    tmp_store.save_inventory_output(date1, [])
    assert tmp_store.get_or_create_inventory_input(date2, [{"batch_id": "seed"}]) == []
