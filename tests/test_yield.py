"""Yield: a recipe quantity is prepared weight, stock is purchased weight.

The gap between the two is where a kitchen silently under-orders every day. These
tests pin the conversion, pin that the loss is reported rather than absorbed, and
pin that a missing yield factor is refused instead of defaulted to 1.0.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.contracts import DishForecast, Forecast
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import ingredients as ingredients_pipe


def _forecast(**dishes: int) -> Forecast:
    return Forecast(
        forecast_date=config.DEMO_DATE,
        expected_covers=80,
        dishes=[
            DishForecast(dish_id=dish_id, expected_qty=qty, confidence="medium", reasoning="test")
            for dish_id, qty in dishes.items()
        ],
        drivers=["test"],
        forecast_source="deterministic_fallback",
    )


# --- The conversion ------------------------------------------------------


def test_purchased_requirement_is_prepared_divided_by_yield():
    # chicken_plate: 0.25 kg prepared potato per portion, potato yield 0.81.
    detail = ingredients_pipe.explode_detail(_forecast(chicken_plate=10))
    potato = detail["potato_fresh"]

    assert potato["prepared"] == 2.5
    assert potato["yield_factor"] == 0.81
    assert potato["purchased"] == round(2.5 / 0.81, 3)
    assert potato["purchased"] > potato["prepared"]


def test_trim_loss_is_the_difference_between_the_two():
    detail = ingredients_pipe.explode_detail(_forecast(chicken_plate=10))
    potato = detail["potato_fresh"]

    assert potato["trim_loss"] == round(potato["purchased"] - potato["prepared"], 3)


def test_an_item_bought_ready_to_use_loses_nothing():
    detail = ingredients_pipe.explode_detail(_forecast(classic_burger=20))
    patty = detail["beef_patty"]

    assert patty["yield_factor"] == 1.0
    assert patty["purchased"] == patty["prepared"] == 20
    assert patty["trim_loss"] == 0


def test_the_authoritative_requirement_is_the_purchased_one():
    """Stock, par levels and supplier orders are all purchased units.

    Returning the prepared figure here would under-order every trimmed item.
    """
    forecast = _forecast(chicken_plate=10)
    detail = ingredients_pipe.explode_detail(forecast)
    required = ingredients_pipe.explode_to_ingredients(forecast)

    assert required["potato_fresh"] == detail["potato_fresh"]["purchased"]
    assert required["potato_fresh"] != detail["potato_fresh"]["prepared"]


def test_requirements_from_several_dishes_are_summed_before_the_conversion():
    """Rounding each dish separately and then converting would drift."""
    combined = ingredients_pipe.explode_detail(_forecast(classic_burger=10, chicken_wrap=10))
    # tomato: 0.04 + 0.03 per portion, prepared 0.7 kg, yield 0.91.
    assert combined["tomato"]["prepared"] == 0.7
    assert combined["tomato"]["purchased"] == round(0.7 / 0.91, 3)


# --- Reporting the loss --------------------------------------------------


def test_only_ingredients_that_lose_something_are_reported():
    detail = ingredients_pipe.explode_detail(_forecast(classic_burger=20))
    losses = ingredients_pipe.trim_losses(detail)
    reported = {item["item_id"] for item in losses}

    assert "beef_patty" not in reported and "burger_bun" not in reported and "fries" not in reported
    assert {"lettuce", "tomato", "cheddar"} <= reported
    assert all(item["trim_loss"] > 0 for item in losses)


def test_trim_losses_are_ordered_by_item_id():
    losses = ingredients_pipe.trim_losses(ingredients_pipe.explode_detail(_forecast(classic_burger=20)))
    assert [item["item_id"] for item in losses] == sorted(item["item_id"] for item in losses)


# --- Refusing to guess ---------------------------------------------------


@pytest.mark.parametrize(
    "factor",
    [None, 0, -0.5, 1.5, "0.8", float("nan")],
)
def test_an_unusable_yield_factor_is_refused_rather_than_defaulted(factor):
    ingredients = {"x": {"unit": "kg", "yield_factor": factor}}
    if factor == "0.8":  # a string parses, so it must fail on the range check instead
        ingredients["x"]["yield_factor"] = "not a number"
    with pytest.raises(ingredients_pipe.YieldUnavailable):
        ingredients_pipe.yield_factor("x", ingredients)


def test_a_missing_yield_factor_is_refused():
    with pytest.raises(ingredients_pipe.YieldUnavailable):
        ingredients_pipe.yield_factor("x", {"x": {"unit": "kg"}})


def test_an_unknown_ingredient_is_refused():
    with pytest.raises(ingredients_pipe.YieldUnavailable):
        ingredients_pipe.yield_factor("not_an_ingredient", {})


def test_every_shipped_ingredient_declares_a_usable_factor():
    ingredients = menu_da.ingredients_by_id()
    for item_id in ingredients:
        assert 0 < ingredients_pipe.yield_factor(item_id, ingredients) <= 1


def test_the_menu_declares_what_a_recipe_quantity_means():
    """The conversion is only valid against a declared basis."""
    assert menu_da.load_menu_document()["recipe_basis"] == "prepared"


# --- End to end ----------------------------------------------------------


def test_the_plan_publishes_both_figures_and_the_loss(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())

    assert plan["ingredient_requirements"]["potato_fresh"] > plan["ingredient_requirements_prepared"]["potato_fresh"]
    reported = {item["item_id"] for item in plan["trim_loss"]}
    assert "potato_fresh" in reported
    assert "beef_patty" not in reported


def test_yield_surfaces_a_shortfall_that_prepared_weight_alone_would_hide(tmp_store):
    """The point of the feature, stated as a test.

    Seed stock holds 6 kg of valid pork ribs. Prepared demand is under that, so
    without the yield conversion the day looks covered — but 6 kg of bone-in ribs
    does not yield 6 kg of served ribs, and the kitchen runs out mid-service.
    """
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())

    prepared = plan["ingredient_requirements_prepared"]["pork_ribs"]
    purchased = plan["ingredient_requirements"]["pork_ribs"]
    ribs = next(s for s in plan["prep_shortfalls"] if s["item_id"] == "pork_ribs")

    assert prepared <= ribs["available"], "prepared demand alone would look covered"
    assert purchased > ribs["available"], "purchased demand is what the kitchen must actually have"
    assert ribs["shortfall"] == round(purchased - ribs["available"], 3)


def test_the_run_log_records_the_trim_loss(tmp_store):
    run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    log = (tmp_store.logs_dir / f"{config.DEMO_DATE}.jsonl").read_text(encoding="utf-8")

    assert "trim_loss_total" in log and "trimmed_items" in log


# --- Dashboard -----------------------------------------------------------


def test_the_dashboard_separates_trim_loss_from_expiry_waste(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    page = render_home(plan, language="no", interactive=True)

    assert 'id="trim-loss"' in page and 'href="#trim-loss"' in page
    assert "Renseskjæringstap" in page
    # The two loss categories are separate sections, never one merged number.
    assert "Svinn som krever kontroll" in page
    assert page.index('id="trim-loss"') < page.index("Svinn som krever kontroll")

    english = render_home(plan, language="en", interactive=True)
    assert "Trim loss" in english and "yield" in english


def test_a_plan_without_trim_loss_renders_an_explicit_empty_state():
    from kitchen_prep.render.html import render_home

    plan = {
        "date": config.DEMO_DATE,
        "expected_covers": 80,
        "forecast": {"forecast_source": "gemini", "dishes": [], "drivers": []},
        "prep_tasks": [],
        "prep_shortfalls": [],
        "replenishment_orders": [],
        "waste_flagged": [],
        "trim_loss": [],
        "briefing": {"summary": "", "warnings": []},
        "generated_at": "2026-08-14T07:00:00+02:00",
    }
    page = render_home(plan, language="no")

    assert "Ingen ingrediens i dagens plan taper vekt på rensing." in page
