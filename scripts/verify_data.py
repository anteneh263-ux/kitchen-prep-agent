"""Verify referential integrity and guard against future leakage.

Exits non-zero (with a printed list) if any check fails. This is the check that
catches problems like a recipe ingredient missing from the master list or a
BASE_QTY key that does not match the menu.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitchen_prep import config  # noqa: E402
from kitchen_prep.data_access import bookings as bookings_da  # noqa: E402
from kitchen_prep.data_access import menu as menu_da  # noqa: E402
from kitchen_prep.data_access import sales as sales_da  # noqa: E402
from kitchen_prep.data_access import store as store_da  # noqa: E402
from kitchen_prep.pipeline import costing as costing_pipe  # noqa: E402
from kitchen_prep.pipeline import ingredients as ingredients_pipe  # noqa: E402
from kitchen_prep.pipeline import intraday as intraday_pipe  # noqa: E402
from kitchen_prep.units import SUPPORTED_UNITS  # noqa: E402


def check() -> list[str]:
    errors: list[str] = []
    ingredients = menu_da.ingredients_by_id()
    ing_ids = set(ingredients)
    menu_ids = set(menu_da.dish_ids())

    # 1. Recipe ingredients exist in the master.
    for dish in menu_da.load_menu():
        for item_id in dish["recipe"]:
            if item_id not in ing_ids:
                errors.append(f"recipe {dish['id']} references unknown ingredient {item_id!r}")

    # 1b. Every ingredient carries a supported, renderable unit.
    for item_id, meta in sorted(ingredients.items()):
        unit = meta.get("unit")
        if unit is None:
            errors.append(f"ingredient {item_id!r} has no unit")
        elif unit not in SUPPORTED_UNITS:
            errors.append(
                f"ingredient {item_id!r} has unsupported unit {unit!r} "
                f"(supported: {list(SUPPORTED_UNITS)})"
            )

    # 1b2. Every ingredient declares a usable yield factor. A missing factor is
    # the dangerous case: defaulting it to 1.0 looks correct and under-orders
    # every purchased quantity, forever.
    for item_id in sorted(ingredients):
        try:
            ingredients_pipe.yield_factor(item_id, ingredients)
        except ingredients_pipe.YieldUnavailable as exc:
            errors.append(str(exc))

    # 1b3. Station prep is fully specified or absent — never half-declared. A
    # station without an action or without a rate would produce a task nobody
    # can act on and a duration nobody can trust.
    for item_id, meta in sorted(ingredients.items()):
        station = meta.get("station")
        rate = meta.get("prep_min_per_purchased_unit", 0)
        try:
            rate = float(rate)
        except (TypeError, ValueError):
            errors.append(f"ingredient {item_id!r} has non-numeric prep_min_per_purchased_unit")
            continue
        if rate < 0:
            errors.append(f"ingredient {item_id!r} has negative prep_min_per_purchased_unit")
        if station:
            if not meta.get("prep_action"):
                errors.append(f"ingredient {item_id!r} names station {station!r} but has no prep_action")
            if rate <= 0:
                errors.append(
                    f"ingredient {item_id!r} names station {station!r} but has no prep time"
                )
        elif rate > 0:
            errors.append(
                f"ingredient {item_id!r} has prep time but no station, so the work would be lost"
            )

    # 1b4. Every ingredient and every dish carries a usable price. A missing
    # cost would quietly value a plate at less than it costs to make.
    for item_id in sorted(ingredients):
        try:
            costing_pipe.ingredient_cost(item_id, ingredients)
        except costing_pipe.PriceUnavailable as exc:
            errors.append(str(exc))
    for dish in menu_da.load_menu():
        try:
            costing_pipe.dish_price(dish["id"], menu_da.menu_by_id())
        except costing_pipe.PriceUnavailable as exc:
            errors.append(str(exc))

    # 1c. Every recipe ingredient resolves to a supported unit.
    for dish in menu_da.load_menu():
        for item_id in dish["recipe"]:
            unit = ingredients.get(item_id, {}).get("unit")
            if item_id in ing_ids and unit not in SUPPORTED_UNITS:
                errors.append(
                    f"recipe {dish['id']} ingredient {item_id!r} does not resolve to a "
                    f"supported unit (got {unit!r})"
                )

    # 1d. The menu declares what a recipe quantity means, so the yield
    # conversion is never applied to an undeclared basis.
    basis = menu_da.load_menu_document().get("recipe_basis")
    if basis != "prepared":
        errors.append(
            f"menu.json recipe_basis is {basis!r}; the pipeline only derives purchased "
            "requirements from a 'prepared' basis"
        )

    # 1e. The service curve is a usable cumulative distribution. A curve that
    # goes backwards or never reaches 1 would silently distort every intraday
    # revision for the rest of the day.
    try:
        errors.extend(intraday_pipe.validate_curve(intraday_pipe.load_curve()))
    except (OSError, ValueError) as exc:
        errors.append(f"service curve unreadable: {exc}")

    # 2. BASE_QTY keys match the menu exactly.
    base_ids = set(config.BASE_QTY)
    if base_ids != menu_ids:
        errors.append(f"BASE_QTY keys {sorted(base_ids)} != menu dishes {sorted(menu_ids)}")

    # 3. Batch item_ids exist in the master.
    for b in store_da.load_seed_batches():
        if b["item_id"] not in ing_ids:
            errors.append(f"batch {b['batch_id']} references unknown item {b['item_id']!r}")

    # 4. Bookings covers are positive integers.
    import csv

    with open(config.BOOKINGS_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                if int(row["expected_covers"]) <= 0:
                    errors.append(f"bookings {row['date']} has non-positive covers")
            except ValueError:
                errors.append(f"bookings {row['date']} covers not an integer")

    # 5. Sales history: exists, no future leakage, valid dishes, ratio in band.
    if not config.SALES_HISTORY_PATH.exists():
        errors.append("sales_history.csv missing (run scripts/generate_sales_history.py)")
        return errors

    rows = sales_da.load_sales_rows()
    if not rows:
        errors.append("sales_history.csv is empty")
        return errors

    end = date.fromisoformat(config.SALES_HISTORY_END)
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        if r["dish_id"] not in menu_ids:
            errors.append(f"sales row has unknown dish {r['dish_id']!r}")
        if date.fromisoformat(r["date"]) > end:
            errors.append(f"sales row {r['date']} is after SALES_HISTORY_END (future leakage)")
        by_date.setdefault(r["date"], []).append(r)

    for d, rs in by_date.items():
        covers = rs[0]["covers"]
        ratio = sum(x["qty_sold"] for x in rs) / covers
        if not (config.RATIO_MIN <= ratio <= config.RATIO_MAX):
            errors.append(f"sales {d} dishes-per-cover ratio {ratio:.3f} outside band")

    return errors


def main() -> int:
    errors = check()
    if errors:
        print("DATA VERIFICATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Data verification OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
