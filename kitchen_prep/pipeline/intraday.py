"""Intraday re-planning: what to cook now, from what has actually sold.

The morning plan answers "what do we prep today". It is published at 07:00 and
then never looks up again — if the day runs 30 % hot, the kitchen finds out
during service. This module closes that loop.

Given the morning forecast, cumulative sales so far, and what each station has
already produced, it answers four questions in order:

1. **Is the day running to plan?** Sales so far are compared against the share of
   the day the service curve says should be done by now.
2. **What will the day actually total?** The morning forecast is rescaled by that
   pace — clamped, and only once enough of the day has happened to mean anything.
3. **What is still to come?** Revised day total minus what is already sold.
4. **What must be cooked now?** Remaining demand exploded to components, minus
   what the station already made, with the on-hand prepared stock and the minutes
   it will last at the current burn rate.

Locked rules:
  - Every number here is computed in Python. The model never enters this path.
  - Below ``SERVICE_MIN_SHARE_FOR_REVISION`` the pace signal is discarded rather
    than extrapolated: three covers at 11:05 must not imply a 400-cover day.
  - The revision is clamped to ``SERVICE_REVISION_BAND`` around the morning
    forecast. A strange hour may bend the plan; it may not rewrite it.
  - The dish *mix* stays as the morning forecast had it. Only the total is
    revised. Mix genuinely shifts between lunch and dinner, but modelling that
    needs per-dish curves this system does not have, and inventing them would be
    guessing.
"""
from __future__ import annotations

import json
from datetime import date as _date, datetime, time as _time
from functools import lru_cache
from typing import Any

from .. import config
from ..data_access import menu as menu_da

_EPS = 1e-9


class ServiceCurveError(ValueError):
    """The service curve is missing or not a usable cumulative distribution."""


class SalesRejected(ValueError):
    """A sales observation is malformed and must not enter the loop."""


# --- The service curve ----------------------------------------------------


@lru_cache(maxsize=1)
def load_curve() -> dict[str, Any]:
    with open(config.SERVICE_CURVE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _minutes(value: str) -> int:
    hour, _, minute = str(value).partition(":")
    return int(hour) * 60 + int(minute)


def validate_curve(curve: dict[str, Any]) -> list[str]:
    """Return the reasons this curve is unusable. Empty means it is fine."""
    errors: list[str] = []
    points = curve.get("points") or []
    if len(points) < 2:
        return ["service curve needs at least two points"]

    previous_time = previous_share = None
    for point in points:
        try:
            minute = _minutes(point["time"])
            share = float(point["share"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"service curve point {point!r} is malformed")
            continue
        if previous_time is not None and minute <= previous_time:
            errors.append(f"service curve time {point['time']} does not move forward")
        if previous_share is not None and share < previous_share - _EPS:
            errors.append(f"service curve share at {point['time']} decreases")
        previous_time, previous_share = minute, share

    if abs(float(points[0]["share"])) > _EPS:
        errors.append("service curve must start at share 0")
    if abs(float(points[-1]["share"]) - 1.0) > _EPS:
        errors.append("service curve must end at share 1")
    return errors


def service_share(as_of: str, curve: dict[str, Any] | None = None) -> float:
    """Share of the day's dish sales that should be done by ``as_of`` (HH:MM).

    Before opening this is 0 and after closing it is 1; in between it is linearly
    interpolated between the two surrounding points.
    """
    curve = curve if curve is not None else load_curve()
    problems = validate_curve(curve)
    if problems:
        raise ServiceCurveError("; ".join(problems))

    minute = _minutes(as_of)
    points = [( _minutes(p["time"]), float(p["share"])) for p in curve["points"]]

    if minute <= points[0][0]:
        return 0.0
    if minute >= points[-1][0]:
        return 1.0
    for (start_minute, start_share), (end_minute, end_share) in zip(points, points[1:]):
        if start_minute <= minute <= end_minute:
            span = end_minute - start_minute
            if span <= 0:
                return round(end_share, 6)
            position = (minute - start_minute) / span
            return round(start_share + position * (end_share - start_share), 6)
    return 1.0


def elapsed_service_minutes(as_of: str, curve: dict[str, Any] | None = None) -> int:
    """Minutes of service completed at ``as_of``. Zero before opening."""
    curve = curve if curve is not None else load_curve()
    opens = _minutes(curve.get("opens") or curve["points"][0]["time"])
    closes = _minutes(curve.get("closes") or curve["points"][-1]["time"])
    return max(0, min(_minutes(as_of), closes) - opens)


# --- Sales observations ---------------------------------------------------


def validate_sales(payload: dict, *, recorded_at: str) -> dict:
    """Return a stored-shape sales observation, or raise ``SalesRejected``.

    Quantities are **cumulative** for the day, not increments. A point-of-sale
    posting a running total can retry, duplicate or arrive out of order without
    corrupting the picture; incremental counts cannot.
    """
    raw_sales = payload.get("sales")
    if not isinstance(raw_sales, dict) or not raw_sales:
        raise SalesRejected("sales must be a non-empty object of dish_id -> quantity")

    menu = menu_da.menu_by_id()
    sales: dict[str, float] = {}
    for dish_id, value in raw_sales.items():
        if dish_id not in menu:
            raise SalesRejected(f"unknown dish_id: {dish_id}")
        try:
            qty = float(value)
        except (TypeError, ValueError) as exc:
            raise SalesRejected(f"quantity for {dish_id} must be a number") from exc
        if qty != qty or qty in (float("inf"), float("-inf")):
            raise SalesRejected(f"quantity for {dish_id} must be finite")
        if qty < 0:
            raise SalesRejected(f"quantity for {dish_id} must not be negative")
        sales[dish_id] = round(qty, 3)

    as_of = str(payload.get("as_of") or "").strip()
    if as_of:
        try:
            _minutes(as_of)
        except ValueError as exc:
            raise SalesRejected("as_of must be a clock time (HH:MM)") from exc
    else:
        as_of = datetime.now().strftime("%H:%M")

    return {
        "as_of": as_of,
        "sales": dict(sorted(sales.items())),
        "recorded_at": recorded_at,
        "recorded_by": str(payload.get("recorded_by") or "pos")[:60],
        "source": str(payload.get("source") or "manual")[:60],
    }


# --- Revision -------------------------------------------------------------


def revise_day_forecast(plan: dict, observation: dict, curve: dict | None = None) -> dict:
    """Rescale the morning forecast by the pace the day is actually running at."""
    dishes = plan.get("forecast", {}).get("dishes", []) or []
    morning = {str(d["dish_id"]): int(d["expected_qty"]) for d in dishes}
    morning_total = sum(morning.values())

    sold = {k: float(v) for k, v in (observation.get("sales") or {}).items()}
    sold_total = round(sum(sold.values()), 3)

    share = service_share(observation["as_of"], curve)
    band = config.SERVICE_REVISION_BAND

    if share < config.SERVICE_MIN_SHARE_FOR_REVISION or morning_total <= 0:
        factor, basis = 1.0, "too_early_to_revise"
        implied_total = None
    else:
        implied_total = round(sold_total / share, 1)
        raw_factor = implied_total / morning_total
        clamped = min(1 + band, max(1 - band, raw_factor))
        # Compare before rounding: rounding to four places is presentation, and
        # must not be reported as though the band had bitten.
        basis = "clamped_to_band" if abs(raw_factor - clamped) > 1e-9 else "pace"
        factor = round(clamped, 4)

    revised = {
        dish_id: max(0, round(qty * factor)) for dish_id, qty in morning.items()
    }
    return {
        "as_of": observation["as_of"],
        "service_share": share,
        "sold_total": sold_total,
        "morning_total": morning_total,
        "implied_day_total": implied_total,
        "revision_factor": factor,
        "revision_basis": basis,
        "revised_dishes": revised,
        "revised_total": sum(revised.values()),
    }


def remaining_dishes(revision: dict, observation: dict) -> dict[str, int]:
    """Portions per dish still expected to sell. Never negative."""
    sold = {k: float(v) for k, v in (observation.get("sales") or {}).items()}
    return {
        dish_id: max(0, int(round(qty - sold.get(dish_id, 0.0))))
        for dish_id, qty in revision["revised_dishes"].items()
    }


# --- Cook now -------------------------------------------------------------


def _prepared_for(dishes: dict[str, float]) -> dict[str, float]:
    """Prepared component quantities a set of dish portions consumes."""
    menu = menu_da.menu_by_id()
    required: dict[str, float] = {}
    for dish_id, qty in dishes.items():
        for item_id, per_portion in menu[dish_id]["recipe"].items():
            required[item_id] = required.get(item_id, 0.0) + per_portion * qty
    return {item_id: round(value, 3) for item_id, value in required.items()}


def cook_now(plan: dict, observation: dict, curve: dict | None = None) -> dict:
    """What each station should put on now, and how long the line will hold.

    ``on_hand`` is what was produced minus what the sales already ate. It can go
    negative when production was under-recorded; that is reported as a negative
    figure rather than clamped away, because a line that says it has less than
    nothing is telling you the records are wrong.
    """
    revision = revise_day_forecast(plan, observation, curve)
    remaining = remaining_dishes(revision, observation)

    sold = {k: float(v) for k, v in (observation.get("sales") or {}).items()}
    consumed = _prepared_for(sold)
    still_needed = _prepared_for({k: float(v) for k, v in remaining.items()})

    produced = {
        record["item_id"]: float(record.get("produced_qty", 0))
        for record in (plan.get("production_actuals") or {}).values()
    }
    elapsed = elapsed_service_minutes(observation["as_of"], curve)

    tasks = []
    for task in plan.get("station_tasks", []) or []:
        item_id = task["item_id"]
        made = round(produced.get(item_id, 0.0), 3)
        eaten = round(consumed.get(item_id, 0.0), 3)
        on_hand = round(made - eaten, 3)
        needed = round(still_needed.get(item_id, 0.0), 3)
        shortfall = round(max(0.0, needed - max(0.0, on_hand)), 3)

        burn = round(eaten / elapsed, 5) if elapsed > 0 and eaten > _EPS else 0.0
        if burn <= _EPS:
            # Nothing of this has sold yet, so there is no rate to project from.
            minutes_left = None
        elif on_hand > 0:
            minutes_left = int(on_hand / burn)
        else:
            # Already out — and being out is the most urgent state there is, so
            # it must sort ahead of everything with time left, not after it.
            minutes_left = 0

        tasks.append(
            {
                "task_id": task["task_id"],
                "station": task["station"],
                "item_id": item_id,
                "action": task.get("action", ""),
                "unit": task["unit"],
                "produced_qty": made,
                "consumed_qty": eaten,
                "on_hand_qty": on_hand,
                "still_needed_qty": needed,
                "cook_now_qty": shortfall,
                "burn_per_minute": burn,
                "minutes_left": minutes_left,
            }
        )

    # Most urgent first: what runs out soonest, then the largest gap. A job with
    # no burn rate yet cannot be ranked by time, so it sorts after those that can.
    tasks.sort(
        key=lambda t: (
            0 if t["minutes_left"] is not None else 1,
            t["minutes_left"] if t["minutes_left"] is not None else 0,
            -t["cook_now_qty"],
            t["task_id"],
        )
    )
    for index, task in enumerate(tasks):
        task["priority"] = index + 1

    return {
        "as_of": observation["as_of"],
        "elapsed_service_minutes": elapsed,
        "revision": revision,
        "remaining_dishes": remaining,
        "tasks": tasks,
        "cook_now_tasks": [task for task in tasks if task["cook_now_qty"] > _EPS],
    }
