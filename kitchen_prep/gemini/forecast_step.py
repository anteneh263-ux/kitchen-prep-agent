"""Gemini step 1: propose a demand forecast (raw). Validation happens downstream.

The context is the model's only source of truth. It carries the authoritative
forecast date and expected covers, the exact allowed dish IDs from menu data and
the last four same-weekday sales quantities per dish (rows strictly before the
forecast date). Without the allowed IDs the model invents dishes — production saw
``chilled_gazpacho_soup`` — which the validator then has to reject.
"""
from __future__ import annotations

from typing import Any

from ..data_access import menu as menu_da
from ..data_access import sales as sales_da

HISTORY_WEEKS = 4

WEEKDAY_NAMES = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


def build_context(date: str, expected_covers: int, weather: dict, weekday: int) -> dict:
    allowed_dish_ids = list(menu_da.dish_ids())
    return {
        "forecast_date": date,
        "expected_covers": expected_covers,
        "weekday": weekday,
        "weekday_name": WEEKDAY_NAMES[weekday],
        "weather": weather,
        "allowed_dish_ids": allowed_dish_ids,
        "history_weeks": HISTORY_WEEKS,
        # Only rows strictly before ``date`` — same_weekday_observations enforces
        # the strict `<` comparison, so no target-date or future leakage. Each
        # observation carries covers and qty_per_cover so the model can normalize
        # instead of copying absolute quantities from higher-cover days.
        "same_weekday_observations": {
            dish_id: [
                {
                    "date": o["date"],
                    "qty_sold": o["qty_sold"],
                    "covers": o["covers"],
                    "qty_per_cover": round(o["qty_per_cover"], 4),
                }
                for o in sales_da.same_weekday_observations(
                    date, dish_id, weeks=HISTORY_WEEKS
                )
            ]
            for dish_id in allowed_dish_ids
        },
    }


def propose(client, context: dict[str, Any]) -> dict:
    """Delegate to the model client. Raises GeminiUnavailable when offline/failed."""
    return client.propose_forecast(context)
