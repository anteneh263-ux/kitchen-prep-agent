"""Plate cost, margin and menu engineering.

The whole point of the yield work was that a recipe quantity is what reaches the
plate, while stock is what you bought. Cost has exactly the same shape, and gets
it wrong in exactly the same direction: you pay for the 0.58 kg of bone-in ribs
that yields the 0.45 kg on the plate. Costing a portion on prepared weight
understates every trimmed ingredient, every day, and always in the flattering
direction.

    portion cost = Σ (recipe qty / yield factor) x cost per purchased unit

From there the arithmetic is ordinary and, more importantly, entirely Python's.
Gemini may say *which* dish deserves attention and why; it may never produce the
cost, the margin or the classification. A margin a chef re-prices a menu from is
not something a language model gets to invent.

Menu engineering follows the standard Kasavana–Smith matrix: a dish is popular
when its share of forecast portions clears 70 % of an even share, and profitable
when its contribution margin clears the menu average. The four quadrants get
their usual names — star, plowhorse, puzzle, dog — because a chef already knows
what those mean.
"""
from __future__ import annotations

from .. import config
from ..data_access import menu as menu_da
from . import ingredients as ingredients_pipe

# Kasavana–Smith: a dish carries its weight at 70 % of an even share.
POPULARITY_THRESHOLD = 0.70

_EPS = 1e-9


class PriceUnavailable(ValueError):
    """A dish or ingredient carries no usable price, so no margin can be derived."""


def _positive(value: object, what: str) -> float:
    if value is None:
        raise PriceUnavailable(f"{what} is missing")
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise PriceUnavailable(f"{what} is not a number: {value!r}") from exc
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise PriceUnavailable(f"{what} is not finite")
    if amount <= 0:
        raise PriceUnavailable(f"{what} must be greater than zero, got {amount}")
    return amount


def ingredient_cost(item_id: str, ingredients: dict[str, dict] | None = None) -> float:
    """Cost of one purchased unit. A missing cost raises rather than defaulting to 0."""
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()
    meta = ingredients.get(item_id)
    if meta is None:
        raise PriceUnavailable(f"unknown ingredient id {item_id!r}")
    return _positive(meta.get("cost_per_unit"), f"cost_per_unit for {item_id!r}")


def dish_price(dish_id: str, menu: dict[str, dict] | None = None) -> float:
    menu = menu if menu is not None else menu_da.menu_by_id()
    dish = menu.get(dish_id)
    if dish is None:
        raise PriceUnavailable(f"unknown dish id {dish_id!r}")
    return _positive(dish.get("price"), f"price for {dish_id!r}")


def portion_cost(dish_id: str, menu=None, ingredients=None) -> dict:
    """What one portion costs to put on a plate, and where the money goes.

    Costed on the purchased quantity, because that is what leaves the bank
    account. The per-ingredient breakdown is returned so a chef can see which
    line is carrying the cost rather than being handed a single number.
    """
    menu = menu if menu is not None else menu_da.menu_by_id()
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()

    lines = []
    total = 0.0
    for item_id, per_portion in menu[dish_id]["recipe"].items():
        factor = ingredients_pipe.yield_factor(item_id, ingredients)
        unit_cost = ingredient_cost(item_id, ingredients)
        purchased = per_portion / factor
        line_cost = purchased * unit_cost
        total += line_cost
        lines.append(
            {
                "item_id": item_id,
                "prepared_qty": round(per_portion, 4),
                "purchased_qty": round(purchased, 4),
                "unit": ingredients[item_id]["unit"],
                "cost_per_unit": unit_cost,
                "cost": round(line_cost, 2),
            }
        )
    lines.sort(key=lambda line: (-line["cost"], line["item_id"]))
    return {"dish_id": dish_id, "cost": round(total, 2), "lines": lines}


def dish_economics(dish_id: str, menu=None, ingredients=None) -> dict:
    """Cost, price, margin and food cost ratio for one dish."""
    menu = menu if menu is not None else menu_da.menu_by_id()
    breakdown = portion_cost(dish_id, menu, ingredients)
    cost = breakdown["cost"]
    price = dish_price(dish_id, menu)
    return {
        "dish_id": dish_id,
        "price": price,
        "cost": cost,
        "margin": round(price - cost, 2),
        # The industry states this as food cost percentage, so the system does too.
        "food_cost_ratio": round(cost / price, 4),
        "over_target": cost / price > config.TARGET_FOOD_COST_RATIO + _EPS,
        "cost_lines": breakdown["lines"],
    }


def menu_economics(forecast: dict | None = None, menu=None, ingredients=None) -> list[dict]:
    """Every dish, classified against the menu it sits on.

    Popularity and profitability are relative to the rest of the menu, so this
    cannot be computed one dish at a time — which is the whole idea behind menu
    engineering.
    """
    menu = menu if menu is not None else menu_da.menu_by_id()
    economics = [dish_economics(dish_id, menu, ingredients) for dish_id in sorted(menu)]

    expected = {}
    if forecast:
        expected = {
            str(d["dish_id"]): int(d["expected_qty"]) for d in forecast.get("dishes", [])
        }
    total_portions = sum(expected.values())
    average_margin = (
        round(sum(item["margin"] for item in economics) / len(economics), 2) if economics else 0.0
    )
    even_share = 1 / len(economics) if economics else 0.0

    for item in economics:
        qty = expected.get(item["dish_id"], 0)
        share = (qty / total_portions) if total_portions > 0 else 0.0
        item["expected_qty"] = qty
        item["popularity_share"] = round(share, 4)
        item["contribution"] = round(item["margin"] * qty, 2)
        if not total_portions:
            item["classification"] = "unforecast"
            continue
        popular = share >= even_share * POPULARITY_THRESHOLD - _EPS
        profitable = item["margin"] >= average_margin - _EPS
        item["classification"] = (
            "star" if popular and profitable
            else "plowhorse" if popular
            else "puzzle" if profitable
            else "dog"
        )

    economics.sort(key=lambda item: (-item["contribution"], item["dish_id"]))
    return economics


def margin_alerts(economics: list[dict]) -> list[dict]:
    """Dishes whose food cost has climbed past the target, worst first.

    An alert is a flag for a human, never an instruction: re-pricing a menu or
    changing a portion is a decision with consequences a planner cannot see.
    """
    over = [
        {
            "dish_id": item["dish_id"],
            "price": item["price"],
            "cost": item["cost"],
            "food_cost_ratio": item["food_cost_ratio"],
            "target": config.TARGET_FOOD_COST_RATIO,
            "biggest_cost_line": item["cost_lines"][0]["item_id"] if item["cost_lines"] else None,
            "requires_human_approval": True,
        }
        for item in economics
        if item.get("over_target")
    ]
    over.sort(key=lambda item: (-item["food_cost_ratio"], item["dish_id"]))
    return over


def value_orders(orders: list[dict], ingredients=None) -> tuple[list[dict], float]:
    """Price every replenishment order. Returns (orders with value, total)."""
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()
    valued = []
    total = 0.0
    for order in orders:
        unit_cost = ingredient_cost(order["item_id"], ingredients)
        value = round(order["order_qty"] * unit_cost, 2)
        total += value
        valued.append({**order, "cost_per_unit": unit_cost, "order_value": value})
    return valued, round(total, 2)


def waste_value(waste: list[dict], ingredients=None) -> float:
    """What the expired batches cost when they were bought.

    Waste stated in kilos is easy to nod at. Waste stated in kroner is not.
    """
    ingredients = ingredients if ingredients is not None else menu_da.ingredients_by_id()
    return round(
        sum(batch["qty"] * ingredient_cost(batch["item_id"], ingredients) for batch in waste), 2
    )


def plan_contribution(economics: list[dict]) -> float:
    """Contribution margin the day's forecast is worth, before labour."""
    return round(sum(item.get("contribution", 0.0) for item in economics), 2)
