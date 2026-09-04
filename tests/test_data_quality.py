import pathlib
import re
from datetime import date

from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.data_access import sales as sales_da
from kitchen_prep.units import SUPPORTED_UNITS
from scripts import verify_data


def test_sales_history_no_future_leakage():
    end = date.fromisoformat(config.SALES_HISTORY_END)
    demo = date.fromisoformat(config.DEMO_DATE)
    for r in sales_da.load_sales_rows():
        d = date.fromisoformat(r["date"])
        assert d <= end
        assert d < demo


def test_all_dishes_present_and_known():
    rows = sales_da.load_sales_rows()
    seen = {r["dish_id"] for r in rows}
    assert seen == set(menu_da.dish_ids())


def test_dishes_per_cover_ratio_in_band():
    rows = sales_da.load_sales_rows()
    by_date: dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)
    for d, rs in by_date.items():
        ratio = sum(x["qty_sold"] for x in rs) / rs[0]["covers"]
        assert config.RATIO_MIN <= ratio <= config.RATIO_MAX, (d, ratio)


def test_baseline_has_enough_history_for_demo():
    # At least 4 same-weekday observations before the demo date for each dish.
    for dish_id in menu_da.dish_ids():
        hist = sales_da.same_weekday_history(config.DEMO_DATE, dish_id, weeks=4)
        assert len(hist) == 4


def test_every_ingredient_has_a_supported_unit():
    for item_id, meta in menu_da.ingredients_by_id().items():
        assert meta.get("unit") in SUPPORTED_UNITS, (item_id, meta.get("unit"))


def test_every_recipe_ingredient_resolves_to_a_supported_unit():
    ingredients = menu_da.ingredients_by_id()
    for dish in menu_da.load_menu():
        for item_id in dish["recipe"]:
            assert item_id in ingredients, (dish["id"], item_id)
            assert ingredients[item_id]["unit"] in SUPPORTED_UNITS, (dish["id"], item_id)


def test_authoritative_units_for_the_audited_ingredients():
    ingredients = menu_da.ingredients_by_id()
    assert ingredients["bbq_sauce"]["unit"] == "l"
    assert ingredients["chicken_wings"]["unit"] == "kg"


def test_verify_data_reports_a_missing_unit(monkeypatch):
    broken = {k: dict(v) for k, v in menu_da.ingredients_by_id().items()}
    broken["bbq_sauce"].pop("unit")
    monkeypatch.setattr(menu_da, "ingredients_by_id", lambda: broken)
    errors = verify_data.check()
    assert any("bbq_sauce" in e and "no unit" in e for e in errors), errors


def test_verify_data_reports_an_unsupported_unit(monkeypatch):
    broken = {k: dict(v) for k, v in menu_da.ingredients_by_id().items()}
    broken["chicken_wings"]["unit"] = "lbs"
    monkeypatch.setattr(menu_da, "ingredients_by_id", lambda: broken)
    errors = verify_data.check()
    assert any("chicken_wings" in e and "unsupported unit" in e for e in errors), errors


def test_verify_data_passes_on_the_shipped_data():
    assert verify_data.check() == []


# --- Single source of truth for units ------------------------------------
# kitchen_prep/units.py is the only module allowed to define the unit table or
# the lookup functions. Everything else imports from it.

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_UNITS_MODULE = _ROOT / "kitchen_prep" / "units.py"

_DEFINITION_PATTERNS = (
    re.compile(r"^SUPPORTED_UNITS\s*="),
    re.compile(r"^_UNIT_LABELS\s*="),
    re.compile(r"^_PORTION_LABELS\s*="),
    re.compile(r"^class UnitResolutionError\b"),
    re.compile(r"^def unit_label\b"),
    re.compile(r"^def ingredient_unit\b"),
    re.compile(r"^def portion_label\b"),
)


def _source_files():
    for base in ("kitchen_prep", "scripts"):
        for path in sorted((_ROOT / base).rglob("*.py")):
            yield path


def test_units_are_defined_only_in_kitchen_prep_units():
    offenders = []
    for path in _source_files():
        if path == _UNITS_MODULE:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(pattern.match(line) for pattern in _DEFINITION_PATTERNS):
                offenders.append(f"{path.relative_to(_ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], f"duplicate unit definitions outside units.py: {offenders}"


def test_units_module_defines_the_full_public_surface():
    text = _UNITS_MODULE.read_text(encoding="utf-8")
    for pattern in _DEFINITION_PATTERNS:
        assert any(pattern.match(line) for line in text.splitlines()), pattern.pattern


def test_renderers_and_scripts_import_units_from_the_neutral_module():
    consumers = (
        _ROOT / "kitchen_prep" / "render" / "html.py",
        _ROOT / "kitchen_prep" / "render" / "markdown.py",
        _ROOT / "kitchen_prep" / "gemini" / "briefing_step.py",
        _ROOT / "scripts" / "verify_data.py",
    )
    for path in consumers:
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^from (\.\.units|kitchen_prep\.units) import ", text, re.M), path


def test_no_module_imports_unit_helpers_from_the_html_renderer():
    helpers = ("SUPPORTED_UNITS", "UnitResolutionError", "unit_label",
               "ingredient_unit", "portion_label")
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^from .*html import (.+)$", text, re.M):
            imported = {name.strip() for name in match.group(1).split(",")}
            leaked = imported & set(helpers)
            assert not leaked, f"{path.relative_to(_ROOT)} imports {leaked} from html"


def test_units_module_does_not_depend_on_a_renderer():
    # The dependency runs renderers -> units, never the reverse.
    imports = [
        line.strip()
        for line in _UNITS_MODULE.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    ]
    assert not [line for line in imports if "render" in line], imports
    assert "from .data_access import menu as menu_da" in imports
