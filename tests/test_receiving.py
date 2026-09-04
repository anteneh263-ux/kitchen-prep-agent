"""Goods receipt and stock count: physical reality corrects the snapshot chain.

The planner would otherwise assume every order arrives in full and that
theoretical consumption is what happened. These tests pin the corrections and,
just as importantly, pin that a day with no recorded events is left untouched.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.data_access.menu import ingredients_by_id
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import receiving


NEXT_DATE = "2026-08-15"

BATCHES = [
    {"batch_id": "b1", "item_id": "beef_patty", "qty": 10, "expiry_date": "2026-08-20"},
    {"batch_id": "b2", "item_id": "beef_patty", "qty": 5, "expiry_date": "2026-08-16"},
    {
        "batch_id": "delivery-2026-08-14-beef_patty",
        "item_id": "beef_patty",
        "qty": 60,
        "expiry_date": "2026-08-25",
    },
]


def _adjustment(payload: dict, adjustment_id: str = "a1") -> dict:
    return receiving.validate_adjustment(
        payload,
        ingredients_by_id(),
        adjustment_id=adjustment_id,
        effective_date=NEXT_DATE,
        recorded_at="2026-08-15T09:00:00+00:00",
    )


def _qty_by_batch(batches: list[dict]) -> dict[str, float]:
    return {batch["batch_id"]: batch["qty"] for batch in batches}


# --- Validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "receipt", "item_id": "not_an_ingredient", "qty_received": 1},
        {"type": "receipt", "item_id": "beef_patty", "qty_received": -1},
        {"type": "receipt", "item_id": "beef_patty", "qty_received": "abc"},
        {"type": "count", "item_id": "beef_patty"},
        {"type": "shrinkage", "item_id": "beef_patty", "qty": 1},
        {"type": "receipt", "item_id": "beef_patty", "qty_received": 1, "expiry_date": "not-a-date"},
    ],
)
def test_malformed_adjustments_never_enter_the_chain(payload):
    with pytest.raises(receiving.AdjustmentRejected):
        _adjustment(payload)


def test_receipt_derives_the_assumed_arrival_batch_from_the_order_date():
    adjustment = _adjustment(
        {"type": "receipt", "item_id": "beef_patty", "qty_received": 40, "order_by_date": "2026-08-14"}
    )
    assert adjustment["replaces_batch_id"] == "delivery-2026-08-14-beef_patty"
    assert adjustment["unit"] == ingredients_by_id()["beef_patty"]["unit"]


# --- Receipts ------------------------------------------------------------


def test_partial_delivery_replaces_the_assumed_arrival():
    adjustment = _adjustment(
        {"type": "receipt", "item_id": "beef_patty", "qty_received": 40, "order_by_date": "2026-08-14"},
        "r1",
    )
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())
    quantities = _qty_by_batch(result["batches"])

    assert "delivery-2026-08-14-beef_patty" not in quantities
    assert quantities["receipt-r1"] == 40
    assert sum(quantities.values()) == 55
    assert result["variances"][0]["variance_qty"] == -20.0


def test_receipt_without_an_expiry_date_gets_one_from_shelf_life():
    adjustment = _adjustment({"type": "receipt", "item_id": "beef_patty", "qty_received": 4}, "r2")
    result = receiving.apply_adjustments([], [adjustment], NEXT_DATE, ingredients_by_id())

    (batch,) = result["batches"]
    assert batch["expiry_date"] > NEXT_DATE
    # Nothing was expected, so an unsolicited receipt is not reported as a variance.
    assert result["variances"] == []


def test_a_delivery_that_never_arrived_removes_the_assumed_stock():
    adjustment = _adjustment(
        {"type": "receipt", "item_id": "beef_patty", "qty_received": 0, "order_by_date": "2026-08-14"},
        "r3",
    )
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())

    assert sum(_qty_by_batch(result["batches"]).values()) == 15
    assert result["variances"][0]["variance_qty"] == -60.0


# --- Counts --------------------------------------------------------------


def test_count_shortage_is_taken_earliest_expiry_first():
    adjustment = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 70}, "c1")
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())

    # The 5 units missing come out of the batch expiring first, not the newest one.
    assert _qty_by_batch(result["batches"]) == {"b1": 10.0, "delivery-2026-08-14-beef_patty": 60.0}
    assert result["variances"][0]["variance_qty"] == -5.0


def test_a_large_count_shortage_drains_earliest_expiry_first():
    adjustment = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 12}, "c2")
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())

    assert _qty_by_batch(result["batches"]) == {"delivery-2026-08-14-beef_patty": 12.0}


def test_count_surplus_is_added_at_the_latest_known_expiry():
    adjustment = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 80}, "c3")
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())

    surplus = [b for b in result["batches"] if b["batch_id"] == "count-c3"]
    assert surplus == [
        {
            "batch_id": "count-c3",
            "item_id": "beef_patty",
            "qty": 5.0,
            "expiry_date": "2026-08-25",
        }
    ]


def test_a_matching_count_changes_nothing_and_reports_no_variance():
    adjustment = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 75}, "c4")
    result = receiving.apply_adjustments(BATCHES, [adjustment], NEXT_DATE, ingredients_by_id())

    assert _qty_by_batch(result["batches"]) == _qty_by_batch(BATCHES)
    assert result["variances"] == []
    assert result["applied"][0]["effect"]["variance_qty"] == 0.0


def test_a_count_only_touches_its_own_item():
    batches = BATCHES + [
        {"batch_id": "t1", "item_id": "tomato", "qty": 9, "expiry_date": "2026-08-18"}
    ]
    adjustment = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 1}, "c5")
    result = receiving.apply_adjustments(batches, [adjustment], NEXT_DATE, ingredients_by_id())

    assert _qty_by_batch(result["batches"])["t1"] == 9


# --- Ordering and safety -------------------------------------------------


def test_receipts_are_applied_before_counts_whatever_the_input_order():
    receipt = _adjustment(
        {"type": "receipt", "item_id": "beef_patty", "qty_received": 40, "order_by_date": "2026-08-14"},
        "r1",
    )
    count = _adjustment({"type": "count", "item_id": "beef_patty", "counted_qty": 50}, "c1")

    result = receiving.apply_adjustments(BATCHES, [count, receipt], NEXT_DATE, ingredients_by_id())

    assert [a["adjustment_id"] for a in result["applied"]] == ["r1", "c1"]
    # The count reconciles post-delivery stock (55), not pre-delivery stock (75).
    assert result["variances"][-1]["expected_qty"] == 55.0


def test_an_unknown_adjustment_type_never_mutates_stock():
    result = receiving.apply_adjustments(
        BATCHES, [{"type": "shrinkage", "item_id": "beef_patty", "qty": 5}], NEXT_DATE, ingredients_by_id()
    )
    assert result["batches"] == [dict(batch) for batch in BATCHES]
    assert result["applied"] == []


def test_a_quiet_day_hands_the_chain_back_untouched():
    """Ordering matters: the snapshot chain is compared batch-for-batch."""
    result = receiving.apply_adjustments(BATCHES, [], NEXT_DATE, ingredients_by_id())
    assert result["batches"] == [dict(batch) for batch in BATCHES]


# --- Effective date ------------------------------------------------------


def test_a_correction_rolls_forward_past_days_that_are_already_planned():
    frozen = {"2026-08-14", "2026-08-15"}
    assert receiving.resolve_effective_date("2026-08-14", frozen.__contains__) == "2026-08-16"


def test_an_open_day_is_used_as_is():
    assert receiving.resolve_effective_date("2026-08-14", lambda _date: False) == "2026-08-14"


# --- End to end through the orchestrator ---------------------------------


def test_a_short_delivery_reduces_the_next_day_input_and_is_reported(tmp_store):
    first = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    ordered = next(
        order
        for order in first["replenishment_orders"]
        if order["item_id"] == "beef_patty" and order["delivery_date"] == NEXT_DATE
    )
    short_by = 20

    effective = receiving.resolve_effective_date(
        config.DEMO_DATE, tmp_store.inventory_snapshot_exists
    )
    assert effective == NEXT_DATE, "a planned day must not be rewritten"

    tmp_store.append_inventory_adjustment(
        receiving.validate_adjustment(
            {
                "type": "receipt",
                "item_id": "beef_patty",
                "qty_received": ordered["order_qty"] - short_by,
                "order_by_date": config.DEMO_DATE,
                "note": "two crates missing",
            },
            ingredients_by_id(),
            adjustment_id="e2e-receipt",
            effective_date=effective,
            recorded_at="2026-08-15T06:30:00+00:00",
        )
    )

    plan = run_daily_prep(NEXT_DATE, store=tmp_store, client=OfflineClient())
    snapshot = tmp_store.get_inventory_snapshot(NEXT_DATE)
    quantities = _qty_by_batch(snapshot["input_batches"])

    assert "delivery-2026-08-14-beef_patty" not in quantities
    assert quantities["receipt-e2e-receipt"] == ordered["order_qty"] - short_by
    assert plan["stock_variances"][0]["variance_qty"] == -float(short_by)
    assert plan["inventory_adjustments"][0]["note"] == "two crates missing"


def test_a_forced_replay_does_not_apply_an_adjustment_twice(tmp_store):
    run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    tmp_store.append_inventory_adjustment(
        receiving.validate_adjustment(
            {"type": "count", "item_id": "tomato", "counted_qty": 0},
            ingredients_by_id(),
            adjustment_id="e2e-count",
            effective_date=NEXT_DATE,
            recorded_at="2026-08-15T22:00:00+00:00",
        )
    )
    first = run_daily_prep(NEXT_DATE, store=tmp_store, client=OfflineClient())
    frozen_input = tmp_store.get_inventory_snapshot(NEXT_DATE)["input_batches"]

    replay = run_daily_prep(NEXT_DATE, store=tmp_store, client=OfflineClient(), force=True)

    assert tmp_store.get_inventory_snapshot(NEXT_DATE)["input_batches"] == frozen_input
    assert len(replay["inventory_adjustments"]) == len(first["inventory_adjustments"]) == 1


def test_a_day_without_adjustments_keeps_the_plan_fields_empty(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert plan["inventory_adjustments"] == []
    assert plan["stock_variances"] == []


def test_adjustments_are_listed_only_for_their_effective_date(tmp_store):
    adjustment = receiving.validate_adjustment(
        {"type": "count", "item_id": "tomato", "counted_qty": 3},
        ingredients_by_id(),
        adjustment_id="listed",
        effective_date=NEXT_DATE,
        recorded_at="2026-08-15T22:00:00+00:00",
    )
    tmp_store.append_inventory_adjustment(adjustment)

    assert tmp_store.list_inventory_adjustments(NEXT_DATE) == [adjustment]
    assert tmp_store.list_inventory_adjustments(config.DEMO_DATE) == []


# --- Dashboard rendering -------------------------------------------------


def _rendered(plan: dict, *, language: str = "no", interactive: bool = True) -> str:
    from kitchen_prep.render.html import render_home

    return render_home(plan, language=language, interactive=interactive)


def test_the_kitchen_screen_shows_the_forms_and_the_variance(tmp_store):
    run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    tmp_store.append_inventory_adjustment(
        receiving.validate_adjustment(
            {
                "type": "receipt",
                "item_id": "beef_patty",
                "qty_received": 0,
                "order_by_date": config.DEMO_DATE,
                "note": "never arrived",
            },
            ingredients_by_id(),
            adjustment_id="render-1",
            effective_date=NEXT_DATE,
            recorded_at="2026-08-15T06:30:00+00:00",
        )
    )
    plan = run_daily_prep(NEXT_DATE, store=tmp_store, client=OfflineClient())

    page = _rendered(plan)
    assert 'id="inventory-receiving"' in page
    assert 'action="/inventory/receipts"' in page
    assert 'action="/inventory/counts"' in page
    assert 'href="#inventory-receiving"' in page
    assert "manko" in page and "never arrived" in page
    assert "Varemottak" in page

    english = _rendered(plan, language="en")
    assert "Goods receipt and stock count" in english
    assert "short" in english


def test_the_public_read_only_view_cannot_record_anything(tmp_store):
    """The public viewer never carries a write control into an unauthenticated page."""
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())

    page = _rendered(plan, interactive=False)

    assert 'id="inventory-receiving"' in page
    assert "<form" not in page.split('id="inventory-receiving"')[1].split("</section>")[0]
    assert 'action="/inventory/receipts"' not in page
    assert 'action="/inventory/counts"' not in page
