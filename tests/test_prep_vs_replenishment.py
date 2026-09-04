"""Today's prep shortfalls are separate from future replenishment; batches that
expire before delivery are dropped from the reorder basis."""
from kitchen_prep.pipeline import prep as prep_pipe
from kitchen_prep.pipeline import replenishment as replen_pipe


def test_covered_today_but_still_reordered_when_stock_spoils_before_delivery():
    # chicken_wings: par 10, lead_time 2 -> delivery 08-16.
    batches = [
        {"batch_id": "b05", "item_id": "chicken_wings", "qty": 9, "expiry_date": "2026-08-15"}
    ]
    required = {"chicken_wings": 8.8}
    consumption = prep_pipe.consume_today(required, batches, "2026-08-14")

    # Covered today (9 >= 8.8) -> no shortfall.
    assert all(s["item_id"] != "chicken_wings" for s in consumption["prep_shortfalls"])

    orders = replen_pipe.compute_orders(consumption["remaining_by_item"], "2026-08-14")
    wings = next(o for o in orders if o["item_id"] == "chicken_wings")
    # Remaining 0.2 kg expires 08-15, before delivery 08-16 -> dropped. Order to par.
    assert wings["stock_at_delivery"] == 0
    assert wings["order_qty"] == 10


def test_uncovered_today_is_a_shortfall_not_just_an_order():
    batches = [
        {"batch_id": "x", "item_id": "beef_patty", "qty": 5, "expiry_date": "2026-08-16"}
    ]
    consumption = prep_pipe.consume_today({"beef_patty": 30}, batches, "2026-08-14")
    short = next(s for s in consumption["prep_shortfalls"] if s["item_id"] == "beef_patty")
    assert short["shortfall"] == 25
    assert short["available"] == 5


def test_expired_batch_is_waste_not_shortfall():
    batches = [
        {"batch_id": "old", "item_id": "pork_ribs", "qty": 7, "expiry_date": "2026-08-13"},
        {"batch_id": "new", "item_id": "pork_ribs", "qty": 6, "expiry_date": "2026-08-17"},
    ]
    consumption = prep_pipe.consume_today({"pork_ribs": 6}, batches, "2026-08-14")
    assert [w["batch_id"] for w in consumption["waste_flagged"]] == ["old"]
    # 6 required, 6 available from the valid batch -> no shortfall.
    assert all(s["item_id"] != "pork_ribs" for s in consumption["prep_shortfalls"])
