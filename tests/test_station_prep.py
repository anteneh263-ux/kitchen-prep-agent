"""Component prep is grouped by station, not by dish.

A dish task is the wrong axis for a cook: BBQ sauce goes into ribs and wings —
one pot, not two jobs — and tomato into four of the six dishes. These tests pin
the aggregation, the two quantities every task carries, and the ordering.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.contracts import DishForecast, Forecast
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import ingredients as ingredients_pipe
from kitchen_prep.pipeline import prep as prep_pipe


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


def _tasks(**dishes: int) -> dict[str, dict]:
    detail = ingredients_pipe.explode_detail(_forecast(**dishes))
    return {task["item_id"]: task for task in prep_pipe.build_station_tasks(detail)}


# --- Aggregation ---------------------------------------------------------


def test_one_ingredient_used_by_two_dishes_becomes_one_job():
    """bbq_sauce: 0.08 l per rib portion, 0.06 l per wing portion — one pot."""
    tasks = prep_pipe.build_station_tasks(
        ingredients_pipe.explode_detail(_forecast(bbq_ribs=10, bbq_wings=10))
    )
    sauce = [task for task in tasks if task["item_id"] == "bbq_sauce"]

    assert len(sauce) == 1
    assert sauce[0]["prepare_qty"] == 1.4
    assert sauce[0]["station"] == "hot"


def test_an_ingredient_used_by_four_dishes_becomes_one_job():
    tasks = _tasks(classic_burger=10, bacon_burger=10, chicken_wrap=10, chicken_plate=10)
    assert tasks["tomato"]["prepare_qty"] == 1.5
    assert tasks["tomato"]["station"] == "cold"


def test_an_item_bought_ready_to_use_produces_no_component_task():
    tasks = _tasks(classic_burger=20)
    for item_id in ("beef_patty", "burger_bun", "fries"):
        assert item_id not in tasks, f"{item_id} is used as bought and needs no prep job"


# --- The two quantities --------------------------------------------------


def test_a_task_carries_both_what_to_produce_and_what_to_draw():
    """They are different numbers, and a cook needs both."""
    task = _tasks(chicken_plate=10)["potato_fresh"]

    assert task["prepare_qty"] == 2.5
    assert task["draw_qty"] == round(2.5 / 0.81, 3)
    assert task["draw_qty"] > task["prepare_qty"]
    assert task["trim_loss"] == round(task["draw_qty"] - task["prepare_qty"], 3)


def test_labour_is_costed_on_what_the_cook_handles():
    """Peeling time scales with the potatoes picked up, not with what survives."""
    task = _tasks(chicken_plate=10)["potato_fresh"]
    rate = menu_da.ingredients_by_id()["potato_fresh"]["prep_min_per_purchased_unit"]

    assert task["prep_minutes"] == round(rate * task["draw_qty"], 1)
    assert task["prep_minutes"] > round(rate * task["prepare_qty"], 1)


# --- Ordering ------------------------------------------------------------


def test_longest_jobs_come_first_and_ordering_is_deterministic():
    tasks = prep_pipe.build_station_tasks(
        ingredients_pipe.explode_detail(_forecast(bbq_ribs=12, classic_burger=25, chicken_plate=8))
    )
    minutes = [task["prep_minutes"] for task in tasks]

    assert minutes == sorted(minutes, reverse=True)
    assert [task["priority"] for task in tasks] == list(range(1, len(tasks) + 1))

    again = prep_pipe.build_station_tasks(
        ingredients_pipe.explode_detail(_forecast(bbq_ribs=12, classic_burger=25, chicken_plate=8))
    )
    assert [task["task_id"] for task in again] == [task["task_id"] for task in tasks]


def test_task_ids_are_stable_and_name_the_station():
    tasks = _tasks(chicken_plate=10)
    assert tasks["potato_fresh"]["task_id"] == "prep_cold_potato_fresh"


# --- Station summary -----------------------------------------------------


def test_the_summary_puts_the_busiest_station_first():
    tasks = prep_pipe.build_station_tasks(
        ingredients_pipe.explode_detail(_forecast(bbq_ribs=20, classic_burger=10))
    )
    summary = prep_pipe.station_summary(tasks)
    loads = [station["prep_minutes"] for station in summary]

    assert loads == sorted(loads, reverse=True)
    assert sum(station["tasks"] for station in summary) == len(tasks)
    assert round(sum(station["prep_minutes"] for station in summary), 1) == round(
        sum(task["prep_minutes"] for task in tasks), 1
    )


def test_an_unlabelled_station_is_still_named_rather_than_dropped():
    """A missing label must never silently remove work from the prep list."""
    assert prep_pipe.station_label("pastry_corner", "no") == "Pastry corner"
    assert prep_pipe.station_label("cold", "en") == "Cold station"
    assert prep_pipe.station_label("cold", "no") == "Kaldkjøkken"


def test_a_station_declared_in_the_master_produces_tasks_without_a_code_change():
    """Stations are data. Adding a bakery is a data change, not a code change."""
    ingredients = dict(menu_da.ingredients_by_id())
    ingredients["burger_bun"] = dict(
        ingredients["burger_bun"],
        station="bakery",
        prep_action="Del og stek",
        prep_min_per_purchased_unit=0.4,
    )
    detail = ingredients_pipe.explode_detail(_forecast(classic_burger=20))
    tasks = prep_pipe.build_station_tasks(detail, ingredients)

    bakery = [task for task in tasks if task["station"] == "bakery"]
    assert [task["item_id"] for task in bakery] == ["burger_bun"]
    assert bakery[0]["prep_minutes"] == 8.0


# --- End to end ----------------------------------------------------------


def test_the_plan_publishes_station_tasks_and_the_summary(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())

    assert plan["station_tasks"], "the day has component prep"
    assert {station["station"] for station in plan["stations"]} == {
        task["station"] for task in plan["station_tasks"]
    }
    # Dish tasks stay: assembly and component prep are two different axes.
    assert plan["prep_tasks"]
    assert {task["task_id"] for task in plan["station_tasks"]}.isdisjoint(
        {task["task_id"] for task in plan["prep_tasks"]}
    )


def test_the_run_log_records_the_station_workload(tmp_store):
    run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    log = (tmp_store.logs_dir / f"{config.DEMO_DATE}.jsonl").read_text(encoding="utf-8")
    assert "station_prep" in log and "prep_minutes" in log


def test_the_dashboard_groups_the_jobs_by_station(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    page = render_home(plan, language="no", interactive=True)

    assert 'id="station-prep"' in page and 'href="#station-prep"' in page
    assert "Kaldkjøkken" in page and "Kjøttdisk" in page
    assert "hent" in page, "the draw quantity is shown next to the produce quantity"

    english = render_home(plan, language="en", interactive=True)
    assert "Cold station" in english and "draw" in english
