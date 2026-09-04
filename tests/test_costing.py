"""Plate cost, margin and menu engineering.

Cost has the same shape as the yield problem and fails in the same direction:
you pay for the bone-in ribs that yield the meat on the plate. These tests pin
that the cost is taken on purchased quantity, that a missing price is refused
rather than defaulted to zero, and that the system flags a thin margin without
ever acting on it.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import costing
from kitchen_prep.pipeline.baseline import baseline_forecast


# --- Costed on what you buy ----------------------------------------------


def test_a_portion_is_costed_on_purchased_quantity_not_prepared():
    """The whole point: 0.45 kg of ribs on the plate is 0.577 kg off the invoice."""
    breakdown = costing.portion_cost("bbq_ribs")
    ribs = next(line for line in breakdown["lines"] if line["item_id"] == "pork_ribs")

    assert ribs["prepared_qty"] == 0.45
    assert ribs["purchased_qty"] == pytest.approx(0.45 / 0.78, abs=1e-4)
    assert ribs["cost"] == pytest.approx(ribs["purchased_qty"] * ribs["cost_per_unit"], abs=0.01)


def test_ignoring_yield_would_understate_every_trimmed_dish():
    ingredients = menu_da.ingredients_by_id()
    menu = menu_da.menu_by_id()
    honest = costing.portion_cost("chicken_plate")["cost"]
    naive = round(
        sum(
            qty * ingredients[item_id]["cost_per_unit"]
            for item_id, qty in menu["chicken_plate"]["recipe"].items()
        ),
        2,
    )
    assert honest > naive, "costing on prepared weight always flatters the dish"


def test_an_untrimmed_ingredient_costs_exactly_its_recipe_quantity():
    breakdown = costing.portion_cost("classic_burger")
    patty = next(line for line in breakdown["lines"] if line["item_id"] == "beef_patty")

    assert patty["prepared_qty"] == patty["purchased_qty"] == 1
    assert patty["cost"] == costing.ingredient_cost("beef_patty")


def test_the_breakdown_leads_with_the_biggest_line():
    lines = costing.portion_cost("bbq_ribs")["lines"]
    assert [line["cost"] for line in lines] == sorted((l["cost"] for l in lines), reverse=True)
    assert lines[0]["item_id"] == "pork_ribs"


def test_the_portion_cost_is_the_sum_of_its_lines():
    breakdown = costing.portion_cost("bacon_burger")
    assert breakdown["cost"] == pytest.approx(sum(l["cost"] for l in breakdown["lines"]), abs=0.02)


# --- Refusing to guess ---------------------------------------------------


@pytest.mark.parametrize("cost", [None, 0, -5, "gratis", float("inf")])
def test_an_unusable_ingredient_cost_is_refused(cost):
    with pytest.raises(costing.PriceUnavailable):
        costing.ingredient_cost("x", {"x": {"unit": "kg", "cost_per_unit": cost}})


@pytest.mark.parametrize("price", [None, 0, -1, "dyrt"])
def test_an_unusable_dish_price_is_refused(price):
    with pytest.raises(costing.PriceUnavailable):
        costing.dish_price("x", {"x": {"price": price}})


def test_every_shipped_dish_and_ingredient_is_priced():
    ingredients = menu_da.ingredients_by_id()
    for item_id in ingredients:
        assert costing.ingredient_cost(item_id, ingredients) > 0
    menu = menu_da.menu_by_id()
    for dish_id in menu:
        assert costing.dish_price(dish_id, menu) > 0


# --- Margin --------------------------------------------------------------


def test_margin_and_food_cost_are_two_views_of_the_same_number():
    item = costing.dish_economics("classic_burger")
    assert item["margin"] == round(item["price"] - item["cost"], 2)
    assert item["food_cost_ratio"] == round(item["cost"] / item["price"], 4)


def test_a_dish_over_the_target_is_flagged_and_one_under_it_is_not():
    ribs = costing.dish_economics("bbq_ribs")
    plate = costing.dish_economics("chicken_plate")

    assert ribs["food_cost_ratio"] > config.TARGET_FOOD_COST_RATIO
    assert ribs["over_target"] is True
    assert plate["food_cost_ratio"] < config.TARGET_FOOD_COST_RATIO
    assert plate["over_target"] is False


def test_an_alert_is_a_flag_for_a_human_never_an_instruction():
    alerts = costing.margin_alerts(costing.menu_economics())
    assert alerts, "the shipped menu has one dish over target"
    for alert in alerts:
        assert alert["requires_human_approval"] is True
        assert alert["biggest_cost_line"]
        assert "new_price" not in alert and "action" not in alert


def test_alerts_are_ordered_worst_first():
    economics = costing.menu_economics()
    for item in economics:  # force every dish over the target
        item["over_target"] = True
    alerts = costing.margin_alerts(economics)
    ratios = [alert["food_cost_ratio"] for alert in alerts]
    assert ratios == sorted(ratios, reverse=True)


# --- Menu engineering ----------------------------------------------------


def test_classification_needs_the_whole_menu_not_one_dish():
    forecast = baseline_forecast(config.DEMO_DATE, 80).to_dict()
    economics = costing.menu_economics(forecast)
    classes = {item["dish_id"]: item["classification"] for item in economics}

    assert set(classes) == set(menu_da.menu_by_id())
    assert set(classes.values()) <= {"star", "plowhorse", "puzzle", "dog"}
    # A star is both popular and above the average margin.
    average = sum(i["margin"] for i in economics) / len(economics)
    for item in economics:
        if item["classification"] == "star":
            assert item["margin"] >= average - 1e-9


def test_popularity_is_measured_against_an_even_share():
    forecast = baseline_forecast(config.DEMO_DATE, 80).to_dict()
    economics = costing.menu_economics(forecast)
    even = 1 / len(economics)

    for item in economics:
        popular = item["classification"] in ("star", "plowhorse")
        assert popular == (item["popularity_share"] >= even * costing.POPULARITY_THRESHOLD - 1e-9)


def test_without_a_forecast_nothing_is_classified():
    economics = costing.menu_economics(None)
    assert all(item["classification"] == "unforecast" for item in economics)
    assert all(item["contribution"] == 0 for item in economics)


def test_contribution_is_margin_times_the_forecast():
    forecast = baseline_forecast(config.DEMO_DATE, 80).to_dict()
    expected = {d["dish_id"]: d["expected_qty"] for d in forecast["dishes"]}
    economics = costing.menu_economics(forecast)

    for item in economics:
        assert item["contribution"] == round(item["margin"] * expected[item["dish_id"]], 2)
    assert costing.plan_contribution(economics) == round(
        sum(i["contribution"] for i in economics), 2
    )


def test_the_menu_is_listed_by_what_it_actually_earns():
    economics = costing.menu_economics(baseline_forecast(config.DEMO_DATE, 80).to_dict())
    contributions = [item["contribution"] for item in economics]
    assert contributions == sorted(contributions, reverse=True)


# --- Orders and waste ----------------------------------------------------


def test_orders_are_valued_at_the_purchase_price():
    orders = [{"item_id": "beef_patty", "order_qty": 10}, {"item_id": "tomato", "order_qty": 2.5}]
    valued, total = costing.value_orders(orders)

    assert valued[0]["order_value"] == round(10 * costing.ingredient_cost("beef_patty"), 2)
    assert total == round(sum(o["order_value"] for o in valued), 2)


def test_waste_is_stated_in_money_because_kilos_are_easy_to_nod_at():
    waste = [{"item_id": "pork_ribs", "qty": 7}]
    assert costing.waste_value(waste) == round(7 * costing.ingredient_cost("pork_ribs"), 2)
    assert costing.waste_value([]) == 0


# --- End to end ----------------------------------------------------------


def test_the_plan_publishes_the_money(tmp_store):
    plan = run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())

    assert plan["currency"] == config.CURRENCY
    assert plan["plan_contribution"] > 0
    assert plan["order_value_total"] > 0
    assert plan["waste_value"] >= 0
    assert [a["dish_id"] for a in plan["margin_alerts"]] == ["bbq_ribs"]
    assert all("order_value" in order for order in plan["replenishment_orders"])


def test_the_briefing_names_the_dish_that_is_eating_the_margin(tmp_store):
    plan = run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())
    warnings = " ".join(plan["briefing"]["warnings"])

    assert "bbq_ribs" in warnings and "food cost" in warnings and "pork_ribs" in warnings


def test_the_dashboard_shows_the_money_and_the_alert(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())
    page = render_home(plan, language="no", interactive=True)

    assert 'id="money"' in page and 'href="#money"' in page
    assert "Over matkostmålet" in page and "NOK" in page
    assert "klass--star" in page

    english = render_home(plan, language="en", interactive=True)
    assert "Above the food cost target" in english and "Star" in english
