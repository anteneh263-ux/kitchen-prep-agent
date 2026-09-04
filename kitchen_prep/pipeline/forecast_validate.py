"""Validation of a proposed (Gemini) forecast against the locked rules.

The caller supplies the authoritative forecast date and expected covers; the
model may only echo them back, never change them.

Reject when: forecast_date or expected_covers differ from the authoritative
values, a menu dish is missing, an unknown dish_id appears (production saw
``chilled_gazpacho_soup``), a dish_id is duplicated, expected_qty is negative or
not an integer, confidence is outside the allowed set, reasoning or drivers are
empty, or the total dishes-per-cover ratio falls outside the +/-30% band. On
rejection the caller switches to the deterministic baseline.
"""
from __future__ import annotations

from .. import config
from ..contracts import CONFIDENCE_VALUES, DishForecast, Forecast
from ..data_access import menu as menu_da


class ForecastRejected(ValueError):
    """Raised when a proposed forecast violates the contract."""


def validate_and_build(
    proposed: dict,
    *,
    forecast_date: str,
    expected_covers: int,
) -> Forecast:
    menu_ids = set(menu_da.dish_ids())

    if not isinstance(proposed, dict):
        raise ForecastRejected("forecast is not an object")

    if not isinstance(expected_covers, int) or isinstance(expected_covers, bool) or expected_covers <= 0:
        raise ForecastRejected("authoritative expected_covers must be a positive integer")

    if proposed.get("forecast_date") != forecast_date:
        raise ForecastRejected(
            f"forecast_date changed: {proposed.get('forecast_date')!r} != {forecast_date!r}"
        )

    covers = proposed.get("expected_covers")
    if isinstance(covers, bool) or not isinstance(covers, int):
        raise ForecastRejected("expected_covers must be a positive integer")
    if covers != expected_covers:
        raise ForecastRejected(f"expected_covers changed: {covers!r} != {expected_covers!r}")

    raw_dishes = proposed.get("dishes")
    if not isinstance(raw_dishes, list):
        raise ForecastRejected("dishes must be a list")

    seen: dict[str, int] = {}
    dish_objs: list[DishForecast] = []
    for d in raw_dishes:
        if not isinstance(d, dict):
            raise ForecastRejected("each dish must be an object")
        did = d.get("dish_id")
        qty = d.get("expected_qty")
        if did not in menu_ids:
            raise ForecastRejected(f"unknown dish_id: {did!r}")
        if did in seen:
            raise ForecastRejected(f"duplicate dish_id: {did!r}")
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise ForecastRejected(f"expected_qty for {did} must be an integer")
        if qty < 0:
            raise ForecastRejected(f"expected_qty for {did} is negative")
        confidence = d.get("confidence", "medium")
        if confidence not in CONFIDENCE_VALUES:
            raise ForecastRejected(f"invalid confidence for {did}: {confidence!r}")
        reasoning = d.get("reasoning", "")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ForecastRejected(f"reasoning for {did} must be a non-empty string")
        seen[did] = qty
        dish_objs.append(
            DishForecast(
                dish_id=did,
                expected_qty=qty,
                confidence=confidence,
                reasoning=reasoning,
            )
        )

    missing = menu_ids - set(seen)
    if missing:
        raise ForecastRejected(f"missing dishes: {sorted(missing)}")

    drivers = proposed.get("drivers")
    if not isinstance(drivers, list) or not drivers:
        raise ForecastRejected("drivers must be a non-empty list")
    if not all(isinstance(x, str) and x.strip() for x in drivers):
        raise ForecastRejected("drivers must be non-empty strings")

    total = sum(seen.values())
    ratio = total / covers
    if not (config.RATIO_MIN <= ratio <= config.RATIO_MAX):
        raise ForecastRejected(
            f"dishes-per-cover ratio {ratio:.3f} outside "
            f"[{config.RATIO_MIN}, {config.RATIO_MAX}]"
        )

    return Forecast(
        forecast_date=forecast_date,
        expected_covers=expected_covers,
        dishes=dish_objs,
        drivers=list(drivers),
        forecast_source="gemini",
    )
