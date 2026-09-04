"""Future replenishment orders using planning_basis = today_consumption_plus_par.

Rules (locked):
  1. Today's demand is covered first from valid batches via FEFO (see prep.py).
  2. Uncovered today-demand is reported separately as prep_shortfalls (not here).
  3. Remaining stock is computed AFTER today's consumption.
  4. Batches expiring before the delivery date are removed (they will spoil).
  5. Order up to par_level.
  6. Today's demand is never double-counted (it is already removed in step 1/3).
  7. Intermediate demand during lead times > 1 day is NOT modelled (documented).
"""
from __future__ import annotations

from datetime import date as _date, timedelta

from ..data_access import menu as menu_da

PLANNING_BASIS = "today_consumption_plus_par"


def compute_orders(
    remaining_by_item: dict[str, list[dict]],
    run_date: str,
    pending_orders: list[dict] | None = None,
) -> list[dict]:
    ingredients = menu_da.ingredients_by_id()
    run = _date.fromisoformat(run_date)
    orders: list[dict] = []

    for item_id, meta in ingredients.items():
        lead = int(meta["lead_time_days"])
        delivery = run + timedelta(days=lead)

        # Stock that will still be valid at delivery (drop batches expiring before it).
        remaining = remaining_by_item.get(item_id, [])
        stock_at_delivery = sum(
            b["qty"] for b in remaining if _date.fromisoformat(b["expiry_date"]) >= delivery
        )
        stock_at_delivery += sum(
            order["order_qty"]
            for order in (pending_orders or [])
            if order["item_id"] == item_id
            and run < _date.fromisoformat(order["delivery_date"]) <= delivery
        )

        par = meta["par_level"]
        order_qty = par - stock_at_delivery
        if order_qty <= 1e-9:
            continue
        order_qty = round(order_qty, 3)
        if meta["unit"] == "stk":
            order_qty = int(round(order_qty))
        orders.append(
            {
                "item_id": item_id,
                "order_qty": order_qty,
                "unit": meta["unit"],
                "supplier": meta["supplier"],
                "order_by_date": run_date,
                "delivery_date": delivery.isoformat(),
                "stock_at_delivery": round(stock_at_delivery, 3),
                "par_level": par,
            }
        )

    orders.sort(key=lambda o: o["item_id"])
    return orders
