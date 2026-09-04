"""Closing the day: the loop that was missing across midnight.

Before this the system forecast from a seeded history file that never grew, so a
run past the seed window read weeks-old data forever, and nothing ever compared
the morning forecast to what the day actually sold. These tests pin both ends,
and pin the two places the system refuses to guess.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import costing, dayclose


DATE = "2026-08-14"
SOLD = {"classic_burger": 30, "bacon_burger": 18, "bbq_ribs": 13,
        "bbq_wings": 16, "chicken_wrap": 11, "chicken_plate": 9}


def _plan(tmp_store, date=DATE):
    return run_daily_prep(date, store=tmp_store, client=OfflineClient())


def _close(tmp_store, plan, sales=None, covers=90):
    payload = {"sales": sales or SOLD}
    if covers is not None:
        payload["covers"] = covers
    actuals = dayclose.validate_close(payload, plan, recorded_at="t")
    tmp_store.record_day_close(plan["date"], actuals)
    return actuals


# --- Scoring the forecast ------------------------------------------------


def test_closing_scores_every_dish_against_the_morning_forecast(tmp_store):
    plan = _plan(tmp_store)
    actuals = _close(tmp_store, plan)

    forecast = {d["dish_id"]: d["expected_qty"] for d in plan["forecast"]["dishes"]}
    by_dish = {row["dish_id"]: row for row in actuals["dish_variance"]}

    assert set(by_dish) == set(forecast)
    for dish_id, row in by_dish.items():
        assert row["forecast_qty"] == forecast[dish_id]
        assert row["variance_qty"] == round(SOLD[dish_id] - forecast[dish_id], 3)
    assert actuals["actual_total"] == sum(SOLD.values())
    assert actuals["variance_total"] == round(actuals["actual_total"] - actuals["forecast_total"], 3)


def test_a_dish_that_sold_nothing_is_still_scored(tmp_store):
    plan = _plan(tmp_store)
    actuals = _close(tmp_store, plan, sales={"classic_burger": 5})
    ribs = next(row for row in actuals["dish_variance"] if row["dish_id"] == "bbq_ribs")

    assert ribs["actual_qty"] == 0
    assert ribs["variance_qty"] == -ribs["forecast_qty"]


@pytest.mark.parametrize(
    "payload",
    [
        {"sales": {}},
        {"sales": {"not_a_dish": 4}},
        {"sales": {"bbq_ribs": -3}},
        {"sales": {"bbq_ribs": "noen"}},
        {"sales": "tolv"},
        {"sales": {"bbq_ribs": 3}, "covers": 0},
        {"sales": {"bbq_ribs": 3}, "covers": -10},
    ],
)
def test_a_malformed_close_is_refused(tmp_store, payload):
    plan = _plan(tmp_store)
    with pytest.raises(dayclose.CloseRejected):
        dayclose.validate_close(payload, plan, recorded_at="t")


# --- Covers are never invented -------------------------------------------


def test_a_day_without_covers_is_scored_but_teaches_nothing(tmp_store):
    """A per-cover ratio needs a real denominator, or the forecast judges itself."""
    plan = _plan(tmp_store)
    actuals = _close(tmp_store, plan, covers=None)

    assert actuals["covers"] is None
    assert actuals["usable_as_history"] is False
    assert actuals["variance_pct"] is not None, "it is still scored"
    assert dayclose.history_rows([actuals]) == []


def test_a_day_with_covers_becomes_history(tmp_store):
    plan = _plan(tmp_store)
    actuals = _close(tmp_store, plan, covers=90)
    rows = dayclose.history_rows([actuals])

    assert actuals["usable_as_history"] is True
    assert len(rows) == len(SOLD)
    assert all(row["covers"] == 90 and row["date"] == DATE for row in rows)
    assert {row["dish_id"] for row in rows} == set(SOLD)


def test_the_forecast_covers_are_never_used_as_the_denominator(tmp_store):
    plan = _plan(tmp_store)
    actuals = _close(tmp_store, plan, covers=90)

    assert plan["expected_covers"] != 90, "the test needs the two to differ"
    assert actuals["expected_covers"] == plan["expected_covers"]
    assert all(row["covers"] == 90 for row in dayclose.history_rows([actuals]))


# --- The history actually reaches tomorrow's forecast --------------------


def test_a_closed_day_changes_the_next_same_weekday_forecast(tmp_store):
    """The point of the whole feature, stated as a test."""
    plan = _plan(tmp_store, "2026-08-14")           # a Friday
    before = {d["dish_id"]: d["expected_qty"] for d in
              run_daily_prep("2026-08-21", store=tmp_store, client=OfflineClient())["forecast"]["dishes"]}

    # Close the Friday with a very heavy burger day and re-plan the next Friday.
    tmp_store.plans.pop("2026-08-21", None) if hasattr(tmp_store, "plans") else None
    _close(tmp_store, plan, sales={**SOLD, "classic_burger": 120}, covers=90)
    after = {d["dish_id"]: d["expected_qty"] for d in
             run_daily_prep("2026-08-21", store=tmp_store, client=OfflineClient(), force=True)["forecast"]["dishes"]}

    assert after["classic_burger"] > before["classic_burger"]


def test_the_seeded_history_file_is_never_written_to(tmp_store):
    """A fresh clone must still generate byte-identical seed data."""
    before = config.SALES_HISTORY_PATH.read_bytes()
    plan = _plan(tmp_store)
    _close(tmp_store, plan)
    run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())

    assert config.SALES_HISTORY_PATH.read_bytes() == before


def test_the_run_log_records_how_much_history_it_had(tmp_store):
    plan = _plan(tmp_store)
    _close(tmp_store, plan)
    run_daily_prep("2026-08-15", store=tmp_store, client=OfflineClient())
    log = (tmp_store.logs_dir / "2026-08-15.jsonl").read_text(encoding="utf-8")

    assert "recorded_history" in log


# --- Measuring the error, not correcting it ------------------------------


def test_error_is_signed_so_the_direction_survives():
    days = [
        {"weekday": "Friday", "variance_pct": 0.20},
        {"weekday": "Friday", "variance_pct": 0.10},
        {"weekday": "Monday", "variance_pct": -0.30},
    ]
    error = dayclose.forecast_error(days)

    friday = next(w for w in error["by_weekday"] if w["weekday"] == "Friday")
    assert friday["mean_error_pct"] == pytest.approx(0.15)
    assert error["mean_error_pct"] == pytest.approx(0.0, abs=1e-9), "signed errors cancel"
    assert error["mean_absolute_error_pct"] == pytest.approx(0.20)


def test_too_few_observations_is_reported_as_such_not_as_a_bias():
    days = [{"weekday": "Tuesday", "variance_pct": 0.4}]
    tuesday = dayclose.forecast_error(days)["by_weekday"][0]

    assert tuesday["observations"] == 1
    assert tuesday["enough_to_judge"] is False


def test_enough_observations_flips_the_judgement():
    days = [{"weekday": "Tuesday", "variance_pct": 0.1}] * dayclose.MIN_OBSERVATIONS_FOR_BIAS
    assert dayclose.forecast_error(days)["by_weekday"][0]["enough_to_judge"] is True


def test_nothing_closed_means_nothing_claimed():
    error = dayclose.forecast_error([])
    assert error["closed_days"] == 0
    assert error["mean_error_pct"] is None and error["by_weekday"] == []


# --- What was thrown, and why -------------------------------------------


def test_a_waste_record_carries_its_reason_and_unit():
    record = dayclose.validate_waste(
        {"item_id": "chicken_wings", "qty": 2.4, "reason": "overprepped", "note": "for mye"},
        recorded_at="t",
    )
    assert record["reason"] == "overprepped"
    assert record["unit"] == menu_da.ingredients_by_id()["chicken_wings"]["unit"]
    assert record["qty"] == 2.4


@pytest.mark.parametrize(
    "payload",
    [
        {"item_id": "chicken_wings", "qty": 1},
        {"item_id": "chicken_wings", "qty": 1, "reason": "fordi"},
        {"item_id": "not_a_thing", "qty": 1, "reason": "expired"},
        {"item_id": "chicken_wings", "reason": "expired"},
        {"item_id": "chicken_wings", "qty": 0, "reason": "expired"},
        {"item_id": "chicken_wings", "qty": -1, "reason": "expired"},
    ],
)
def test_a_malformed_waste_record_is_refused(payload):
    with pytest.raises(dayclose.WasteRejected):
        dayclose.validate_waste(payload, recorded_at="t")


def test_over_prepping_is_reported_apart_from_spoilage():
    """An expired batch is a rotation problem; a full bin at close is a plan problem."""
    records = [
        dayclose.validate_waste({"item_id": "chicken_wings", "qty": 2, "reason": "overprepped"}, recorded_at="t"),
        dayclose.validate_waste({"item_id": "tomato", "qty": 1, "reason": "expired"}, recorded_at="t"),
    ]
    summary = dayclose.waste_summary(records, costing_module=costing)

    assert summary["total_records"] == 2
    assert summary["overprepped_value"] == round(2 * costing.ingredient_cost("chicken_wings"), 2)
    assert summary["total_value"] > summary["overprepped_value"]
    assert {entry["reason"] for entry in summary["reasons"]} == {"overprepped", "expired"}


def test_the_waste_log_is_append_only(tmp_store):
    plan = _plan(tmp_store)
    for qty in (1, 2):
        tmp_store.record_waste(
            DATE,
            dayclose.validate_waste(
                {"item_id": "tomato", "qty": qty, "reason": "spillage"}, recorded_at="t"
            ),
        )
    assert [r["qty"] for r in tmp_store.get_plan(DATE)["waste_records"]] == [1, 2]


def test_waste_for_a_date_with_no_plan_is_refused(tmp_store):
    assert tmp_store.record_waste("2026-01-01", {"item_id": "tomato"}) is None


# --- Reconciliation ------------------------------------------------------


def test_produced_minus_sold_minus_thrown_is_what_nobody_accounted_for(tmp_store):
    from kitchen_prep.pipeline import production

    plan = _plan(tmp_store)
    task = next(t for t in plan["station_tasks"] if t["item_id"] == "chicken_wings")
    tmp_store.record_production(
        DATE,
        production.validate_production(
            {"task_id": task["task_id"], "produced_qty": 8.0}, plan["station_tasks"], recorded_at="t"
        ),
    )
    waste = dayclose.validate_waste(
        {"item_id": "chicken_wings", "qty": 1.0, "reason": "overprepped"}, recorded_at="t"
    )
    tmp_store.record_waste(DATE, waste)
    actuals = _close(tmp_store, plan)

    rows = dayclose.reconcile(tmp_store.get_plan(DATE), actuals, [waste])
    wings = next(row for row in rows if row["item_id"] == "chicken_wings")

    # 16 wing portions at 0.40 kg each.
    assert wings["consumed_qty"] == pytest.approx(6.4, abs=1e-3)
    assert wings["wasted_qty"] == 1.0
    assert wings["unexplained_qty"] == round(8.0 - wings["consumed_qty"] - 1.0, 3)


def test_the_biggest_gap_is_listed_first(tmp_store):
    from kitchen_prep.pipeline import production

    plan = _plan(tmp_store)
    for task in plan["station_tasks"][:3]:
        tmp_store.record_production(
            DATE,
            production.validate_production(
                {"task_id": task["task_id"], "produced_qty": 50}, plan["station_tasks"], recorded_at="t"
            ),
        )
    rows = dayclose.reconcile(tmp_store.get_plan(DATE), _close(tmp_store, plan), [])
    gaps = [abs(row["unexplained_qty"]) for row in rows]
    assert gaps == sorted(gaps, reverse=True)


# --- Dashboard -----------------------------------------------------------


def test_the_dashboard_names_over_prepping_as_the_plans_own_score(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = _plan(tmp_store)
    waste = dayclose.validate_waste(
        {"item_id": "chicken_wings", "qty": 3, "reason": "overprepped"}, recorded_at="t"
    )
    tmp_store.record_waste(DATE, waste)
    view = {
        "actuals": None,
        "waste": dayclose.waste_summary([waste], costing_module=costing),
        "reconciliation": [],
    }
    page = render_home(tmp_store.get_plan(DATE), language="no", interactive=True, day_close=view)

    assert 'id="day-close"' in page and 'href="#day-close"' in page
    assert "Overpreppet" in page and "overprepping" in page
    assert 'action="/waste"' in page

    read_only = render_home(tmp_store.get_plan(DATE), language="no", day_close=view)
    assert 'action="/waste"' not in read_only
