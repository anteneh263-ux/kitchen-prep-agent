"""Today's prep: kitchen tasks, FEFO consumption, and prep shortfalls.

Strictly today-facing. Future replenishment is handled separately in
``replenishment.py`` and must not be conflated with today's shortfalls.
"""
from __future__ import annotations

from collections import defaultdict

from ..contracts import Forecast
from ..data_access import menu as menu_da
from . import fefo


def build_prep_tasks(forecast: Forecast) -> list[dict]:
    """One task per dish, id = ``prep_<dish_id>``, ordered by total prep minutes
    descending (longest jobs start first). Ordering is stable and deterministic."""
    menu = menu_da.menu_by_id()
    tasks = []
    for dish in forecast.dishes:
        prep_min = menu[dish.dish_id]["prep_min_per_portion"] * dish.expected_qty
        tasks.append(
            {
                "task_id": f"prep_{dish.dish_id}",
                "dish_id": dish.dish_id,
                "qty": dish.expected_qty,
                "prep_minutes": round(prep_min, 1),
            }
        )
    tasks.sort(key=lambda t: (-t["prep_minutes"], t["task_id"]))
    for i, t in enumerate(tasks):
        t["priority"] = i + 1
    return tasks


def group_batches_by_item(batches: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for b in batches:
        grouped[b["item_id"]].append(dict(b))
    return grouped


def consume_today(
    required: dict[str, float], batches: list[dict], run_date: str
) -> dict:
    """Cover today's ingredient requirements from valid batches via FEFO.

    Returns a dict with:
      waste_flagged        - batches already expired on run_date
      fefo_consumption     - list of {batch_id, item_id, qty_consumed}
      prep_shortfalls      - list of {item_id, required, available, shortfall}
      remaining_by_item    - leftover batches per item AFTER today's consumption
    """
    valid, waste = fefo.split_expired(batches, run_date)
    by_item = group_batches_by_item(valid)

    fefo_consumption: list[dict] = []
    prep_shortfalls: list[dict] = []
    remaining_by_item: dict[str, list[dict]] = {}

    # Consume for items that are required today.
    for item_id, req in required.items():
        item_batches = by_item.get(item_id, [])
        available = round(sum(b["qty"] for b in item_batches), 3)
        consumption, uncovered, remaining = fefo.consume(req, item_batches)
        fefo_consumption.extend(consumption)
        remaining_by_item[item_id] = remaining
        if uncovered > 1e-9:
            prep_shortfalls.append(
                {
                    "item_id": item_id,
                    "required": round(req, 3),
                    "available": available,
                    "shortfall": round(uncovered, 3),
                }
            )

    # Items with stock but no demand today keep all their valid batches.
    for item_id, item_batches in by_item.items():
        remaining_by_item.setdefault(item_id, [dict(b) for b in item_batches])

    prep_shortfalls.sort(key=lambda s: s["item_id"])
    return {
        "waste_flagged": waste,
        "fefo_consumption": fefo_consumption,
        "prep_shortfalls": prep_shortfalls,
        "remaining_by_item": remaining_by_item,
    }
