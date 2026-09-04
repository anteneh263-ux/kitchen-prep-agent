"""Intraday re-planning: what to cook now, from what has actually sold.

The morning plan is published at 07:00 and never looks up again. These tests pin
the loop that closes that gap: the pace read, the guards that stop a thin signal
from rewriting the day, and the cook-now list a line cook works from.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import intraday, production


DATE = "2026-08-15"
FULL_DAY = {
    "classic_burger": 34, "bacon_burger": 20, "bbq_ribs": 15,
    "bbq_wings": 17, "chicken_wrap": 13, "chicken_plate": 11,
}


def _plan(tmp_store):
    return run_daily_prep(DATE, store=tmp_store, client=OfflineClient())


def _observation(as_of, sales):
    return intraday.validate_sales({"as_of": as_of, "sales": sales}, recorded_at="t")


# --- The service curve ---------------------------------------------------


def test_the_curve_runs_from_nothing_to_everything():
    assert intraday.service_share("09:00") == 0.0
    assert intraday.service_share("11:00") == 0.0
    assert intraday.service_share("22:00") == 1.0
    assert intraday.service_share("23:30") == 1.0


def test_the_share_only_ever_moves_forward():
    times = [f"{hour:02d}:{minute:02d}" for hour in range(10, 24) for minute in (0, 30)]
    shares = [intraday.service_share(t) for t in times]
    assert shares == sorted(shares)


def test_a_time_between_two_points_is_interpolated():
    # 12:00 is 0.10 and 12:30 is 0.20, so 12:15 must sit halfway.
    assert intraday.service_share("12:15") == pytest.approx(0.15, abs=1e-6)


@pytest.mark.parametrize(
    "curve",
    [
        {"points": [{"time": "11:00", "share": 0.0}, {"time": "10:00", "share": 1.0}]},
        {"points": [{"time": "11:00", "share": 0.0}, {"time": "12:00", "share": 0.5}]},
        {"points": [{"time": "11:00", "share": 0.3}, {"time": "22:00", "share": 1.0}]},
        {"points": [{"time": "11:00", "share": 0.0}, {"time": "12:00", "share": 0.6},
                    {"time": "13:00", "share": 0.4}, {"time": "22:00", "share": 1.0}]},
        {"points": [{"time": "11:00", "share": 0.0}]},
    ],
)
def test_an_unusable_curve_is_refused(curve):
    """A curve that goes backwards or never reaches 1 would distort every hour."""
    assert intraday.validate_curve(curve)
    with pytest.raises(intraday.ServiceCurveError):
        intraday.service_share("15:00", curve)


def test_the_shipped_curve_is_usable():
    assert intraday.validate_curve(intraday.load_curve()) == []


# --- Sales observations --------------------------------------------------


def test_sales_are_cumulative_and_normalised():
    observation = _observation("14:00", {"bbq_ribs": 4, "classic_burger": 9})
    assert observation["sales"] == {"bbq_ribs": 4.0, "classic_burger": 9.0}
    assert observation["as_of"] == "14:00"


@pytest.mark.parametrize(
    "payload",
    [
        {"as_of": "14:00", "sales": {}},
        {"as_of": "14:00", "sales": {"not_a_dish": 1}},
        {"as_of": "14:00", "sales": {"bbq_ribs": -1}},
        {"as_of": "14:00", "sales": {"bbq_ribs": "many"}},
        {"as_of": "14:00", "sales": "twelve"},
        {"as_of": "halv tre", "sales": {"bbq_ribs": 1}},
    ],
)
def test_a_malformed_observation_is_refused(payload):
    with pytest.raises(intraday.SalesRejected):
        intraday.validate_sales(payload, recorded_at="t")


# --- The revision --------------------------------------------------------


def test_a_thin_early_signal_never_rescales_the_day(tmp_store):
    """Three covers at 11:05 must not imply a four-hundred cover day."""
    plan = _plan(tmp_store)
    revision = intraday.revise_day_forecast(plan, _observation("11:05", {"bbq_ribs": 3}))

    assert revision["revision_basis"] == "too_early_to_revise"
    assert revision["revision_factor"] == 1.0
    assert revision["implied_day_total"] is None
    assert revision["revised_dishes"] == {
        d["dish_id"]: d["expected_qty"] for d in plan["forecast"]["dishes"]
    }


def test_a_busy_day_is_revised_upward(tmp_store):
    plan = _plan(tmp_store)
    revision = intraday.revise_day_forecast(plan, _observation("18:00", FULL_DAY))

    assert revision["service_share"] == 0.66
    assert revision["implied_day_total"] > revision["morning_total"]
    assert revision["revision_basis"] == "pace"
    assert 1 < revision["revision_factor"] < 1 + config.SERVICE_REVISION_BAND
    assert revision["revised_total"] > revision["morning_total"]


def test_an_extreme_hour_bends_the_plan_but_cannot_rewrite_it(tmp_store):
    plan = _plan(tmp_store)
    stampede = {dish: qty * 3 for dish, qty in FULL_DAY.items()}
    revision = intraday.revise_day_forecast(plan, _observation("18:00", stampede))

    assert revision["revision_basis"] == "clamped_to_band"
    assert revision["revision_factor"] == pytest.approx(1 + config.SERVICE_REVISION_BAND, abs=1e-4)


def test_a_quiet_day_is_revised_downward_but_clamped(tmp_store):
    plan = _plan(tmp_store)
    quiet = {dish: 1 for dish in FULL_DAY}
    revision = intraday.revise_day_forecast(plan, _observation("18:00", quiet))

    assert revision["revision_factor"] == pytest.approx(1 - config.SERVICE_REVISION_BAND, abs=1e-3)
    assert revision["revised_total"] < revision["morning_total"]


def test_a_day_running_to_plan_is_left_alone(tmp_store):
    plan = _plan(tmp_store)
    morning_total = sum(d["expected_qty"] for d in plan["forecast"]["dishes"])
    on_pace = round(morning_total * intraday.service_share("18:00"))
    observation = _observation("18:00", {"classic_burger": on_pace})

    revision = intraday.revise_day_forecast(plan, observation)
    assert revision["revision_factor"] == pytest.approx(1.0, abs=0.01)
    assert revision["revision_basis"] == "pace"


def test_the_dish_mix_is_not_reinvented(tmp_store):
    """Only the total is revised; mix needs per-dish curves we do not have."""
    plan = _plan(tmp_store)
    morning = {d["dish_id"]: d["expected_qty"] for d in plan["forecast"]["dishes"]}
    revision = intraday.revise_day_forecast(plan, _observation("18:00", {"bbq_ribs": 90}))

    factor = revision["revision_factor"]
    assert revision["revised_dishes"] == {k: max(0, round(v * factor)) for k, v in morning.items()}


def test_what_is_already_sold_is_never_cooked_twice(tmp_store):
    plan = _plan(tmp_store)
    observation = _observation("18:00", FULL_DAY)
    revision = intraday.revise_day_forecast(plan, observation)
    remaining = intraday.remaining_dishes(revision, observation)

    for dish_id, qty in remaining.items():
        assert qty == max(0, round(revision["revised_dishes"][dish_id] - FULL_DAY.get(dish_id, 0)))
    assert all(qty >= 0 for qty in remaining.values())


def test_a_dish_that_outsold_its_revised_total_asks_for_nothing_more(tmp_store):
    plan = _plan(tmp_store)
    observation = _observation("20:00", {"bbq_ribs": 400})
    revision = intraday.revise_day_forecast(plan, observation)

    assert intraday.remaining_dishes(revision, observation)["bbq_ribs"] == 0


# --- Cook now ------------------------------------------------------------


def _with_production(tmp_store, produced: dict[str, float]):
    plan = _plan(tmp_store)
    for task_id, qty in produced.items():
        tmp_store.record_production(
            DATE,
            production.validate_production(
                {"task_id": task_id, "produced_qty": qty}, plan["station_tasks"], recorded_at="t"
            ),
        )
    return tmp_store.get_plan(DATE)


def test_on_hand_is_what_was_made_minus_what_sold(tmp_store):
    plan = _with_production(tmp_store, {"prep_hot_bbq_sauce": 2.56})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    sauce = next(t for t in view["tasks"] if t["item_id"] == "bbq_sauce")

    # 15 ribs at 0.08 l plus 17 wings at 0.06 l.
    assert sauce["consumed_qty"] == pytest.approx(15 * 0.08 + 17 * 0.06, abs=1e-3)
    assert sauce["produced_qty"] == 2.56
    assert sauce["on_hand_qty"] == round(sauce["produced_qty"] - sauce["consumed_qty"], 3)


def test_an_unrecorded_station_reports_less_than_nothing(tmp_store):
    """A line that says it has below zero is telling you the records are wrong."""
    plan = _with_production(tmp_store, {})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    wings = next(t for t in view["tasks"] if t["item_id"] == "chicken_wings")

    assert wings["produced_qty"] == 0
    assert wings["on_hand_qty"] < 0


def test_being_out_now_outranks_everything_with_time_left(tmp_store):
    plan = _with_production(tmp_store, {"prep_hot_bbq_sauce": 2.56, "prep_butchery_pork_ribs": 7.65})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    urgent = view["cook_now_tasks"]

    out_now = [t for t in urgent if t["minutes_left"] == 0]
    has_time = [t for t in urgent if t["minutes_left"]]
    assert out_now and has_time
    assert max(t["priority"] for t in out_now) < min(t["priority"] for t in has_time)


def test_minutes_left_falls_out_of_the_burn_rate(tmp_store):
    plan = _with_production(tmp_store, {"prep_butchery_pork_ribs": 7.65})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    ribs = next(t for t in view["tasks"] if t["item_id"] == "pork_ribs")

    assert view["elapsed_service_minutes"] == 420  # 11:00 to 18:00
    assert ribs["burn_per_minute"] == pytest.approx(ribs["consumed_qty"] / 420, abs=1e-4)
    assert ribs["minutes_left"] == int(ribs["on_hand_qty"] / ribs["burn_per_minute"])


def test_an_item_nobody_has_ordered_has_no_rate_to_project_from(tmp_store):
    plan = _with_production(tmp_store, {"prep_hot_bbq_sauce": 5})
    view = intraday.cook_now(plan, _observation("12:00", {"chicken_plate": 4}))
    sauce = next(t for t in view["tasks"] if t["item_id"] == "bbq_sauce")

    assert sauce["consumed_qty"] == 0
    assert sauce["minutes_left"] is None


def test_a_covered_line_asks_for_nothing(tmp_store):
    plan = _with_production(tmp_store, {task["task_id"]: 500 for task in _plan(tmp_store)["station_tasks"]})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))

    assert view["cook_now_tasks"] == []


def test_the_cook_now_quantity_closes_the_gap_and_no_more(tmp_store):
    plan = _with_production(tmp_store, {"prep_butchery_pork_ribs": 7.65})
    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    ribs = next(t for t in view["tasks"] if t["item_id"] == "pork_ribs")

    assert ribs["cook_now_qty"] == round(max(0.0, ribs["still_needed_qty"] - max(0.0, ribs["on_hand_qty"])), 3)


# --- Rendering -----------------------------------------------------------


def test_the_panel_appears_only_once_something_has_sold(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = _with_production(tmp_store, {"prep_butchery_pork_ribs": 7.65})
    assert 'id="cook-now"' not in render_home(plan, language="no", interactive=True)

    view = intraday.cook_now(plan, _observation("18:00", FULL_DAY))
    page = render_home(plan, language="no", interactive=True, intraday=view)

    assert 'id="cook-now"' in page and "Lag nå" in page
    assert "over morgenprognosen" in page
    assert "tom nå" in page or "tom om" in page

    english = render_home(plan, language="en", interactive=True, intraday=view)
    assert "Cook now" in english and "above the morning forecast" in english
