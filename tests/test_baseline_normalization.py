"""Regression tests for per-cover normalization of historical demand.

The baseline used to average absolute qty_sold across same-weekday history. Those
Fridays ran 135-150 covers, so the demo date (80 covers) inherited 144 dishes —
a ratio of 1.80, far outside the locked [0.724, 1.344] band. Demand is now
normalized to qty_sold/covers and rescaled to the target day's covers.
"""
from datetime import date as _date

import pytest

from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.data_access import sales as sales_da
from kitchen_prep.gemini import forecast_step
from kitchen_prep.pipeline.baseline import baseline_forecast

DATE = config.DEMO_DATE  # 2026-08-14, a Friday
WEATHER = {"weather_code": 2, "temperature_c": 18.0, "precipitation_mm": 0.0}


def _rows(specs):
    """Build sales rows as the reader returns them: one row per dish per date."""
    return [
        {
            "date": day,
            "dish_id": "classic_burger",
            "qty_sold": qty,
            "covers": covers,
            "weather_code": 1,
            "temperature_c": 20.0,
            "precipitation_mm": 0.0,
        }
        for day, qty, covers in specs
    ]


@pytest.fixture()
def fake_history(monkeypatch):
    def _install(specs):
        monkeypatch.setattr(sales_da, "load_sales_rows", lambda: _rows(specs))

    return _install


# --- the demo date scales to its own covers -----------------------------------
def test_demo_baseline_scales_to_80_covers():
    f = baseline_forecast(DATE, 80)
    assert f.forecast_date == DATE
    assert f.expected_covers == 80
    assert f.forecast_source == "deterministic_fallback"
    assert [d.dish_id for d in f.dishes] == menu_da.dish_ids()

    for dish in f.dishes:
        obs = sales_da.same_weekday_observations(DATE, dish.dish_id, weeks=4)
        per_cover = sum(o["qty_per_cover"] for o in obs) / len(obs)
        assert dish.expected_qty == round(per_cover * 80)
        # The old bug: absolute quantities from 135-150 cover Fridays.
        assert dish.expected_qty < max(o["qty_sold"] for o in obs)


def test_demo_baseline_ratio_is_inside_the_configured_band():
    f = baseline_forecast(DATE, 80)
    total = sum(d.expected_qty for d in f.dishes)
    ratio = total / 80
    assert config.RATIO_MIN <= ratio <= config.RATIO_MAX, (total, ratio)


def test_baseline_satisfies_the_locked_forecast_contract():
    """The deterministic fallback must meet the same contract Gemini output does."""
    from kitchen_prep.pipeline.forecast_validate import validate_and_build

    f = baseline_forecast(DATE, 80)
    rebuilt = validate_and_build(f.to_dict(), forecast_date=DATE, expected_covers=80)
    assert [d.dish_id for d in rebuilt.dishes] == menu_da.dish_ids()
    # validate_and_build stamps "gemini"; the fallback itself must not.
    assert f.forecast_source == "deterministic_fallback"


# --- covers drive the quantities proportionally --------------------------------
def test_changing_expected_covers_changes_quantities_proportionally(fake_history):
    # One same-weekday observation: 0.25 dishes per cover.
    fake_history([("2026-08-07", 25, 100)])
    for covers, expected in ((80, 20), (160, 40), (40, 10)):
        f = baseline_forecast(DATE, covers)
        dish = next(d for d in f.dishes if d.dish_id == "classic_burger")
        assert dish.expected_qty == expected, covers


def test_higher_cover_history_is_not_copied_through(fake_history):
    # 60 sold on a 200-cover Friday is 0.30 per cover -> 24 on an 80-cover day.
    fake_history([("2026-08-07", 60, 200)])
    dish = next(
        d for d in baseline_forecast(DATE, 80).dishes if d.dish_id == "classic_burger"
    )
    assert dish.expected_qty == 24


# --- no future or target-date leakage ------------------------------------------
def test_target_date_and_future_rows_are_excluded(fake_history):
    fake_history(
        [
            ("2026-08-07", 20, 100),  # the only usable observation -> 0.20/cover
            (DATE, 5000, 100),        # the target date itself
            ("2026-08-21", 9000, 100),  # a later Friday
        ]
    )
    obs = sales_da.same_weekday_observations(DATE, "classic_burger", weeks=4)
    assert [o["date"] for o in obs] == ["2026-08-07"]
    assert all(_date.fromisoformat(o["date"]) < _date.fromisoformat(DATE) for o in obs)

    dish = next(
        d for d in baseline_forecast(DATE, 80).dishes if d.dish_id == "classic_burger"
    )
    assert dish.expected_qty == 16


def test_non_positive_covers_rows_are_dropped(fake_history):
    # The zero-cover row is unusable and must not divide by zero or skew the mean.
    fake_history([("2026-07-31", 99, 0), ("2026-08-07", 20, 100)])
    obs = sales_da.same_weekday_observations(DATE, "classic_burger", weeks=4)
    assert [o["date"] for o in obs] == ["2026-08-07"]
    dish = next(
        d for d in baseline_forecast(DATE, 80).dishes if d.dish_id == "classic_burger"
    )
    assert dish.expected_qty == 16


# --- the model gets the same normalization inputs -------------------------------
def test_gemini_context_observations_carry_qty_sold_covers_and_qty_per_cover():
    ctx = forecast_step.build_context(DATE, 80, WEATHER, 4)
    observations = ctx["same_weekday_observations"]
    assert set(observations) == set(menu_da.dish_ids())

    for dish_id, obs in observations.items():
        assert len(obs) == 4, dish_id
        for o in obs:
            assert set(o) == {"date", "qty_sold", "covers", "qty_per_cover"}
            assert o["covers"] > 0
            assert o["date"] < DATE
            assert o["qty_per_cover"] == pytest.approx(
                o["qty_sold"] / o["covers"], abs=1e-4
            )


def test_context_observations_reproduce_the_baseline():
    """The model can derive exactly what the deterministic path derives."""
    ctx = forecast_step.build_context(DATE, 80, WEATHER, 4)
    baseline = {d.dish_id: d.expected_qty for d in baseline_forecast(DATE, 80).dishes}

    for dish_id, obs in ctx["same_weekday_observations"].items():
        per_cover = sum(o["qty_per_cover"] for o in obs) / len(obs)
        assert round(per_cover * ctx["expected_covers"]) == baseline[dish_id], dish_id
