"""Today's prep: kitchen tasks, FEFO consumption, and prep shortfalls.

Strictly today-facing. Future replenishment is handled separately in
``replenishment.py`` and must not be conflated with today's shortfalls.

Prep is described on two axes, because a kitchen works on both:

``build_prep_tasks``
    One task per dish — assembly and service. How many portions of each dish.

``build_station_tasks``
    One task per ingredient that needs component prep, aggregated across every
    dish that uses it and grouped by the station that does the work. This is the
    axis a cook actually stands at.
"""
from __future__ import annotations

from collections import defaultdict

from .. import config
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


# --- Component prep, grouped by station -----------------------------------

def station_label(station: str, language: str) -> str:
    """Display label for a station. An unlabelled station is named, never dropped."""
    labels = config.STATION_LABELS.get(station)
    if labels is None:
        return station.replace("_", " ").capitalize()
    return labels["en" if language == "en" else "no"]


def build_station_tasks(
    requirement_detail: dict[str, dict],
    ingredients: dict[str, dict] | None = None,
) -> list[dict]:
    """One component task per ingredient that needs prep, aggregated across dishes.

    A dish task is the wrong axis for a cook. BBQ sauce goes into ribs *and*
    wings, tomato into four of the six dishes — that is one pot and one chopping
    board, not two and four jobs. This aggregates the day's requirement per
    ingredient and hands it to the station that does the work.

    Each task carries both quantities, because they are different numbers and a
    cook needs both: ``prepare_qty`` is what must exist when prep is finished,
    ``draw_qty`` is what to pull from the walk-in to get there.
    """
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()
    tasks: list[dict] = []

    for item_id, values in requirement_detail.items():
        meta = ingredients[item_id]
        station = meta.get("station")
        if not station:  # bought ready to use — no component prep exists
            continue
        purchased = values["purchased"]
        minutes = round(float(meta.get("prep_min_per_purchased_unit", 0)) * purchased, 1)
        tasks.append(
            {
                "task_id": f"prep_{station}_{item_id}",
                "station": station,
                "item_id": item_id,
                "action": meta.get("prep_action") or "",
                "prepare_qty": values["prepared"],
                "draw_qty": purchased,
                "trim_loss": values["trim_loss"],
                "unit": meta["unit"],
                "prep_minutes": minutes,
            }
        )

    # Longest jobs first, the same rule the dish tasks use, so the two lists can
    # be read together without switching mental models.
    tasks.sort(key=lambda t: (-t["prep_minutes"], t["task_id"]))
    for index, task in enumerate(tasks):
        task["priority"] = index + 1
    return tasks


def station_summary(station_tasks: list[dict]) -> list[dict]:
    """Per station: how many tasks and how many minutes, busiest station first.

    Ordering by workload is the useful answer to "where do we start?", and ties
    break on the station id so the order never wobbles between runs.
    """
    totals: dict[str, dict] = {}
    for task in station_tasks:
        entry = totals.setdefault(
            task["station"], {"station": task["station"], "tasks": 0, "prep_minutes": 0.0}
        )
        entry["tasks"] += 1
        entry["prep_minutes"] = round(entry["prep_minutes"] + task["prep_minutes"], 1)
    return sorted(totals.values(), key=lambda s: (-s["prep_minutes"], s["station"]))
