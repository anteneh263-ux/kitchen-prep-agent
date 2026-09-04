"""Deterministic baseline forecast.

Per dish: mean of qty_sold/covers over the last 4 same-weekday observations
strictly before the target date, scaled to today's expected covers. Absolute
historical quantities are never copied — a 150-cover Friday would otherwise
inflate an 80-cover Friday and push the plan outside the dishes-per-cover band.
"""
from __future__ import annotations

from ..contracts import DishForecast, Forecast
from ..data_access import menu as menu_da
from ..data_access import sales as sales_da

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

HISTORY_WEEKS = 4


def baseline_forecast(date: str, expected_covers: int) -> Forecast:
    from datetime import date as _date

    weekday = _date.fromisoformat(date).weekday()
    dishes: list[DishForecast] = []
    for dish_id in menu_da.dish_ids():
        observations = sales_da.same_weekday_observations(date, dish_id, weeks=HISTORY_WEEKS)
        if observations:
            per_cover = sum(o["qty_per_cover"] for o in observations) / len(observations)
            qty = round(per_cover * expected_covers)
        else:
            per_cover = 0.0
            qty = 0
        dishes.append(
            DishForecast(
                dish_id=dish_id,
                expected_qty=int(qty),
                confidence="medium",
                reasoning=(
                    f"Deterministic baseline: mean of the last {len(observations)} "
                    f"{WEEKDAY_NAMES[weekday]} observations = {per_cover:.4f} per guest "
                    f"x {expected_covers} guests = {qty}"
                ),
            )
        )
    return Forecast(
        forecast_date=date,
        expected_covers=expected_covers,
        dishes=dishes,
        drivers=["deterministic_baseline"],
        forecast_source="deterministic_fallback",
    )
