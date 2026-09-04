"""Explode a per-dish forecast into authoritative ingredient requirements."""
from __future__ import annotations

from ..contracts import Forecast
from ..data_access import menu as menu_da


def explode_to_ingredients(forecast: Forecast) -> dict[str, float]:
    menu = menu_da.menu_by_id()
    required: dict[str, float] = {}
    for dish in forecast.dishes:
        recipe = menu[dish.dish_id]["recipe"]
        for item_id, per_portion in recipe.items():
            required[item_id] = required.get(item_id, 0.0) + per_portion * dish.expected_qty
    return {k: round(v, 3) for k, v in required.items()}
