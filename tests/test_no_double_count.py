"""Today's demand is removed before the par calculation and never re-counted."""
from kitchen_prep.pipeline import prep as prep_pipe
from kitchen_prep.pipeline import replenishment as replen_pipe


BEEF = [
    {"batch_id": "b01", "item_id": "beef_patty", "qty": 22, "expiry_date": "2026-08-14"},
    {"batch_id": "b02", "item_id": "beef_patty", "qty": 18, "expiry_date": "2026-08-16"},
]


def test_order_uses_post_consumption_stock():
    required = {"beef_patty": 30}
    consumption = prep_pipe.consume_today(required, BEEF, "2026-08-14")
    orders = replen_pipe.compute_orders(consumption["remaining_by_item"], "2026-08-14")
    beef = next(o for o in orders if o["item_id"] == "beef_patty")

    # par 60, remaining after today's consumption = 10 (b02), all valid at delivery.
    # Correct: 60 - 10 = 50. Double-counting today (extra -30) would give 20.
    assert beef["stock_at_delivery"] == 10
    assert beef["order_qty"] == 50


def test_fully_stocked_item_not_ordered():
    # fries par 25, one big batch far from expiry, tiny demand -> no order.
    batches = [{"batch_id": "f", "item_id": "fries", "qty": 30, "expiry_date": "2026-12-01"}]
    consumption = prep_pipe.consume_today({"fries": 5}, batches, "2026-08-14")
    orders = replen_pipe.compute_orders(consumption["remaining_by_item"], "2026-08-14")
    assert all(o["item_id"] != "fries" for o in orders)
