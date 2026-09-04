"""Closing the day: what was actually sold, what was actually thrown, what it means.

Until now the system had no memory across midnight. It forecasts from
``sales_history.csv``, which is generated from a fixed seed and never grows — so
on any date past the seed window the baseline is reading weeks-old history and
will keep doing so forever. It also had no idea whether yesterday's forecast was
any good.

Closing a day fixes both ends:

**Recorded actuals extend the history.** They are stored beside the seed, never
written into it, so the seeded file stays byte-identical and reproducible while
the baseline still learns from real days.

**Recorded waste says what the plan could not see.** FEFO already catches a batch
that expired; it cannot see the eight portions of prepped chicken thrown out at
close because the kitchen made too much. That is precisely the number that says
whether the intraday loop is over-producing, so it is recorded with a reason and
kept apart from expiry waste.

Two rules keep this honest:

  - **Covers are never invented.** A history row is a quantity *per cover*, so a
    day closed without a real covers count is recorded for variance analysis but
    excluded from the baseline history. Dividing by the forecast instead of the
    actual would quietly make the forecast judge itself.
  - **Error is measured and reported, never silently corrected.** Below a minimum
    number of observations a weekday's bias is not a bias, it is noise.
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any, Iterable

from .. import config
from ..data_access import menu as menu_da

# Why something was thrown. "expired" is what FEFO already detects; the rest is
# what only a person standing at the bin can tell you.
WASTE_REASONS = ("expired", "overprepped", "spillage", "quality", "other")

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Below this many closed days for a weekday, a difference is noise, not a bias.
MIN_OBSERVATIONS_FOR_BIAS = 3

_EPS = 1e-9


class CloseRejected(ValueError):
    """A day-close payload is malformed and must not enter the history."""


class WasteRejected(ValueError):
    """A waste record is malformed and must not enter the log."""


def _quantity(value: Any, what: str) -> float:
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise CloseRejected(f"{what} must be a number") from exc
    if qty != qty or qty in (float("inf"), float("-inf")):
        raise CloseRejected(f"{what} must be finite")
    if qty < 0:
        raise CloseRejected(f"{what} must not be negative")
    return round(qty, 3)


# --- Closing the day ------------------------------------------------------


def validate_close(payload: dict, plan: dict, *, recorded_at: str) -> dict:
    """Freeze a day's actual sales and score the morning forecast against them.

    ``covers`` is optional and consequential: with it the day becomes a usable
    history row, without it the day is still scored but cannot teach the
    baseline anything, because a per-cover ratio needs a real denominator.
    """
    menu = menu_da.menu_by_id()
    raw_sales = payload.get("sales")
    if not isinstance(raw_sales, dict) or not raw_sales:
        raise CloseRejected("sales must be a non-empty object of dish_id -> quantity")

    sales: dict[str, float] = {}
    for dish_id, value in raw_sales.items():
        if dish_id not in menu:
            raise CloseRejected(f"unknown dish_id: {dish_id}")
        sales[dish_id] = _quantity(value, f"quantity for {dish_id}")

    covers_raw = payload.get("covers")
    covers = None
    if covers_raw not in (None, ""):
        covers = int(_quantity(covers_raw, "covers"))
        if covers <= 0:
            raise CloseRejected("covers must be greater than zero when given")

    forecast = {
        str(d["dish_id"]): int(d["expected_qty"])
        for d in plan.get("forecast", {}).get("dishes", []) or []
    }
    variance = []
    for dish_id in sorted(set(forecast) | set(sales)):
        expected = forecast.get(dish_id, 0)
        actual = sales.get(dish_id, 0.0)
        variance.append(
            {
                "dish_id": dish_id,
                "forecast_qty": expected,
                "actual_qty": actual,
                "variance_qty": round(actual - expected, 3),
                "variance_pct": round((actual - expected) / expected, 4) if expected else None,
            }
        )

    forecast_total = sum(forecast.values())
    actual_total = round(sum(sales.values()), 3)
    return {
        "date": plan["date"],
        "weekday": WEEKDAY_NAMES[_date.fromisoformat(plan["date"]).weekday()],
        "covers": covers,
        "expected_covers": plan.get("expected_covers"),
        "sales": dict(sorted(sales.items())),
        "forecast_total": forecast_total,
        "actual_total": actual_total,
        "variance_total": round(actual_total - forecast_total, 3),
        "variance_pct": round((actual_total - forecast_total) / forecast_total, 4)
        if forecast_total
        else None,
        "dish_variance": variance,
        "usable_as_history": covers is not None,
        "recorded_at": recorded_at,
        "recorded_by": str(payload.get("recorded_by") or "kitchen")[:60],
        "source": str(payload.get("source") or "manual")[:60],
    }


def history_rows(actuals: Iterable[dict]) -> list[dict]:
    """Closed days as sales-history rows the baseline can read.

    Only days that carried a real covers count appear. The seeded history file is
    never touched: recorded days sit beside it, so a fresh clone still generates
    byte-identical seed data.
    """
    rows: list[dict] = []
    for day in actuals:
        if not day.get("usable_as_history") or not day.get("covers"):
            continue
        for dish_id, qty in (day.get("sales") or {}).items():
            rows.append(
                {
                    "date": day["date"],
                    "dish_id": dish_id,
                    "qty_sold": int(round(float(qty))),
                    "covers": int(day["covers"]),
                }
            )
    rows.sort(key=lambda row: (row["date"], row["dish_id"]))
    return rows


# --- Was the forecast any good? ------------------------------------------


def forecast_error(actuals: Iterable[dict]) -> dict:
    """Signed forecast error overall and per weekday.

    Signed, not absolute: a kitchen needs to know it runs *hot* on Fridays, and
    an absolute error would hide the direction. A weekday with fewer than
    ``MIN_OBSERVATIONS_FOR_BIAS`` closed days is reported with its count and no
    claim of bias — this reports, it never corrects.
    """
    days = [day for day in actuals if day.get("variance_pct") is not None]
    by_weekday: dict[str, list[float]] = {}
    for day in days:
        by_weekday.setdefault(day["weekday"], []).append(float(day["variance_pct"]))

    weekdays = []
    for name in WEEKDAY_NAMES:
        errors = by_weekday.get(name)
        if not errors:
            continue
        mean = round(sum(errors) / len(errors), 4)
        weekdays.append(
            {
                "weekday": name,
                "observations": len(errors),
                "mean_error_pct": mean,
                "enough_to_judge": len(errors) >= MIN_OBSERVATIONS_FOR_BIAS,
            }
        )

    overall = (
        round(sum(float(d["variance_pct"]) for d in days) / len(days), 4) if days else None
    )
    absolute = (
        round(sum(abs(float(d["variance_pct"])) for d in days) / len(days), 4) if days else None
    )
    return {
        "closed_days": len(days),
        "mean_error_pct": overall,
        "mean_absolute_error_pct": absolute,
        "by_weekday": weekdays,
        "min_observations_for_bias": MIN_OBSERVATIONS_FOR_BIAS,
    }


# --- What was thrown, and why --------------------------------------------


def validate_waste(payload: dict, *, recorded_at: str, ingredients=None) -> dict:
    """A recorded disposal. The reason is the point, not the quantity."""
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()

    item_id = str(payload.get("item_id", "")).strip()
    if item_id not in ingredients:
        raise WasteRejected(f"unknown item_id: {item_id or '(missing)'}")

    reason = str(payload.get("reason", "")).strip()
    if reason not in WASTE_REASONS:
        raise WasteRejected(f"reason must be one of {', '.join(WASTE_REASONS)}")

    raw = payload.get("qty")
    if raw is None or raw == "":
        raise WasteRejected("qty is required")
    try:
        qty = _quantity(raw, "qty")
    except CloseRejected as exc:
        raise WasteRejected(str(exc)) from None
    if qty <= 0:
        raise WasteRejected("qty must be greater than zero")

    return {
        "item_id": item_id,
        "qty": qty,
        "unit": ingredients[item_id]["unit"],
        "reason": reason,
        "recorded_at": recorded_at,
        "recorded_by": str(payload.get("recorded_by") or "kitchen")[:60],
        "note": str(payload.get("note") or "")[:200],
    }


def waste_summary(records: Iterable[dict], costing_module=None) -> dict:
    """Recorded waste grouped by reason, with what it cost.

    Expiry waste is reported separately from the rest on purpose: an expired
    batch is a rotation problem, and a bin full of prepped food at closing time
    is a production problem. Averaging them together hides both.
    """
    by_reason: dict[str, dict] = {}
    for record in records:
        entry = by_reason.setdefault(
            record["reason"], {"reason": record["reason"], "records": 0, "value": 0.0, "items": {}}
        )
        entry["records"] += 1
        entry["items"][record["item_id"]] = round(
            entry["items"].get(record["item_id"], 0.0) + record["qty"], 3
        )
        if costing_module is not None:
            entry["value"] = round(
                entry["value"] + record["qty"] * costing_module.ingredient_cost(record["item_id"]), 2
            )

    reasons = sorted(by_reason.values(), key=lambda entry: (-entry["value"], entry["reason"]))
    return {
        "reasons": reasons,
        "total_records": sum(entry["records"] for entry in reasons),
        "total_value": round(sum(entry["value"] for entry in reasons), 2),
        "overprepped_value": round(
            sum(e["value"] for e in reasons if e["reason"] == "overprepped"), 2
        ),
    }


def reconcile(plan: dict, closed: dict | None, waste_records: Iterable[dict]) -> list[dict]:
    """Produced minus sold minus thrown. The remainder is unexplained.

    This is the honest scoreboard for the prep plan: if a station produced eight
    kilos, the day ate five and nobody threw anything, three kilos are somewhere
    nobody has accounted for.
    """
    menu = menu_da.menu_by_id()
    sold = (closed or {}).get("sales") or {}

    consumed: dict[str, float] = {}
    for dish_id, qty in sold.items():
        for item_id, per_portion in menu[dish_id]["recipe"].items():
            consumed[item_id] = consumed.get(item_id, 0.0) + per_portion * float(qty)

    thrown: dict[str, float] = {}
    for record in waste_records:
        thrown[record["item_id"]] = thrown.get(record["item_id"], 0.0) + record["qty"]

    rows = []
    for record in (plan.get("production_actuals") or {}).values():
        item_id = record["item_id"]
        produced = round(float(record.get("produced_qty", 0)), 3)
        eaten = round(consumed.get(item_id, 0.0), 3)
        binned = round(thrown.get(item_id, 0.0), 3)
        rows.append(
            {
                "item_id": item_id,
                "unit": record.get("unit"),
                "produced_qty": produced,
                "consumed_qty": eaten,
                "wasted_qty": binned,
                "unexplained_qty": round(produced - eaten - binned, 3),
            }
        )
    rows.sort(key=lambda row: (-abs(row["unexplained_qty"]), row["item_id"]))
    return rows
