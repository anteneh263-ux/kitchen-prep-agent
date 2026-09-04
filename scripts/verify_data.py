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

    # 1c. Every recipe ingredient resolves to a supported unit.
    for dish in menu_da.load_menu():
        for item_id in dish["recipe"]:
            unit = ingredients.get(item_id, {}).get("unit")
            if item_id in ing_ids and unit not in SUPPORTED_UNITS:
                errors.append(
                    f"recipe {dish['id']} ingredient {item_id!r} does not resolve to a "
                    f"supported unit (got {unit!r})"
                )

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
