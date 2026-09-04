"""Read generated sales history."""
from __future__ import annotations

import csv
from datetime import date as _date

from .. import config


def load_sales_rows(extra_rows: list[dict] | None = None) -> list[dict]:
    """Seeded history, optionally extended with recorded days.

    Recorded days are passed in rather than read from a store, so the caller
    that owns the store decides what history a forecast sees. A hidden global
    would make a test and a production run disagree about the past.
    """
    rows: list[dict] = []
    with open(config.SALES_HISTORY_PATH, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(
                {
                    "date": r["date"],
                    "dish_id": r["dish_id"],
                    "qty_sold": int(r["qty_sold"]),
                    "covers": int(r["covers"]),
                    "weather_code": int(r["weather_code"]),
                    "temperature_c": float(r["temperature_c"]),
                    "precipitation_mm": float(r["precipitation_mm"]),
                }
            )
    for row in extra_rows or []:
        rows.append(
            {
                "date": row["date"],
                "dish_id": row["dish_id"],
                "qty_sold": int(row["qty_sold"]),
                "covers": int(row["covers"]),
                "weather_code": int(row.get("weather_code", 0)),
                "temperature_c": float(row.get("temperature_c", 0.0)),
                "precipitation_mm": float(row.get("precipitation_mm", 0.0)),
            }
        )
    return rows


def daily_covers_before(target_date: str, extra_rows: list[dict] | None = None) -> dict[str, int]:
    """Return ``{date: covers}`` for history dates strictly before ``target_date``.

    Sales history holds one row per dish per date, so the covers figure repeats
    within a date; it is collapsed to a single observation per date. Non-positive
    covers are dropped as unusable rather than averaged in. The strict ``<``
    comparison is what guards against target-date and future leakage.
    """
    target = _date.fromisoformat(target_date)
    out: dict[str, int] = {}
    for row in load_sales_rows(extra_rows):
        if _date.fromisoformat(row["date"]) >= target:
            continue
        if row["covers"] > 0:
            out[row["date"]] = row["covers"]
    return out


def same_weekday_observations(
    target_date: str, dish_id: str, weeks: int = 4, extra_rows: list[dict] | None = None
) -> list[dict]:
    """Return the last ``weeks`` same-weekday observations for ``dish_id``,
    strictly before ``target_date`` (guards against future leakage).

    Each observation carries ``date``, ``qty_sold``, ``covers`` and the
    normalized ``qty_per_cover``. Absolute qty_sold is not comparable across days
    — a 150-cover Friday sells more of everything than a 135-cover Friday — so
    demand must be reasoned about per cover and rescaled to the target day.

    Rows with non-positive covers carry no usable per-cover signal and are
    dropped rather than averaged in.
    """
    target = _date.fromisoformat(target_date)
    weekday = target.weekday()
    hits: list[dict] = []
    for row in load_sales_rows(extra_rows):
        if row["dish_id"] != dish_id or row["covers"] <= 0:
            continue
        row_date = _date.fromisoformat(row["date"])
        if row_date >= target or row_date.weekday() != weekday:
            continue
        hits.append(
            {
                "date": row["date"],
                "qty_sold": row["qty_sold"],
                "covers": row["covers"],
                "qty_per_cover": row["qty_sold"] / row["covers"],
            }
        )
    hits.sort(key=lambda o: o["date"])
    return hits[-weeks:]


def same_weekday_history(target_date: str, dish_id: str, weeks: int = 4) -> list[int]:
    """Absolute qty_sold for the same observations. Kept for reporting/checks;
    forecasting must use :func:`same_weekday_observations` and normalize."""
    return [o["qty_sold"] for o in same_weekday_observations(target_date, dish_id, weeks)]
