"""Explode a per-dish forecast into authoritative ingredient requirements.

A recipe quantity is *prepared* weight — what actually reaches the plate. Stock,
par levels and supplier orders are all in *purchased* units. Between the two sits
the yield: 1 kg of whole potato is not 1 kg of peeled potato.

Leaving that gap implicit is how a kitchen quietly under-orders every single day.
So the conversion is explicit and one-directional:

    purchased = prepared / yield_factor
    trim_loss = purchased - prepared

``explode_to_ingredients`` returns the purchased requirement, because that is the
figure every downstream step needs: FEFO consumes purchased stock, shortfalls
compare against purchased stock, and replenishment orders purchased units.
``explode_detail`` additionally exposes the prepared figure and the trim loss, so
the loss is reported rather than absorbed.
"""
from __future__ import annotations

from ..contracts import Forecast
from ..data_access import menu as menu_da


class YieldUnavailable(ValueError):
    """An ingredient has no usable yield factor, so no requirement can be derived."""


def yield_factor(item_id: str, ingredients: dict[str, dict] | None = None) -> float:
    """Usable fraction of one purchased unit. Missing or absurd values raise.

    A silent default of 1.0 would be the dangerous choice here: it looks correct
    and under-orders forever.
    """
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()
    meta = ingredients.get(item_id)
    if meta is None:
        raise YieldUnavailable(f"unknown ingredient id {item_id!r}")
    raw = meta.get("yield_factor")
    if raw is None:
        raise YieldUnavailable(f"ingredient {item_id!r} has no yield_factor")
    try:
        factor = float(raw)
    except (TypeError, ValueError) as exc:
        raise YieldUnavailable(f"ingredient {item_id!r} has non-numeric yield_factor {raw!r}") from exc
    if not 0 < factor <= 1:
        raise YieldUnavailable(
            f"ingredient {item_id!r} has yield_factor {factor!r}; it must be greater than 0 "
            "and at most 1 (a purchased unit cannot yield more than itself)"
        )
    return factor


def explode_detail(forecast: Forecast) -> dict[str, dict]:
    """Per ingredient: prepared requirement, purchased requirement and trim loss."""
    menu = menu_da.menu_by_id()
    ingredients = menu_da.ingredients_by_id()

    prepared: dict[str, float] = {}
    for dish in forecast.dishes:
        recipe = menu[dish.dish_id]["recipe"]
        for item_id, per_portion in recipe.items():
            prepared[item_id] = prepared.get(item_id, 0.0) + per_portion * dish.expected_qty

    detail: dict[str, dict] = {}
    for item_id, value in prepared.items():
        factor = yield_factor(item_id, ingredients)
        prepared_qty = round(value, 3)
        purchased_qty = round(value / factor, 3)
        detail[item_id] = {
            "prepared": prepared_qty,
            "purchased": purchased_qty,
            "trim_loss": round(purchased_qty - prepared_qty, 3),
            "yield_factor": factor,
            "unit": ingredients[item_id]["unit"],
        }
    return detail


def explode_to_ingredients(forecast: Forecast) -> dict[str, float]:
    """Authoritative purchased requirement per ingredient."""
    return {item_id: values["purchased"] for item_id, values in explode_detail(forecast).items()}


def trim_losses(detail: dict[str, dict]) -> list[dict]:
    """Only the ingredients that actually lose something, sorted by item id.

    Trim loss is a separate category from expiry waste: it is planned and
    unavoidable, where an expired batch is neither. Conflating them would hide
    the one number a kitchen can act on.
    """
    return [
        {
            "item_id": item_id,
            "prepared": values["prepared"],
            "purchased": values["purchased"],
            "trim_loss": values["trim_loss"],
            "yield_factor": values["yield_factor"],
            "unit": values["unit"],
        }
        for item_id, values in sorted(detail.items())
        if values["trim_loss"] > 1e-9
    ]
