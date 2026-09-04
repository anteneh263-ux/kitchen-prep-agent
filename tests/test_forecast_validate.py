import pytest

from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.pipeline.forecast_validate import ForecastRejected, validate_and_build


DATE = "2026-08-14"
COVERS = 80


def _validate(proposed, *, forecast_date=DATE, expected_covers=COVERS):
    return validate_and_build(
        proposed, forecast_date=forecast_date, expected_covers=expected_covers
    )


def _valid_proposed(covers=COVERS, qty=15):
    return {
        "forecast_date": DATE,
        "expected_covers": covers,
        "dishes": [
            {"dish_id": d, "expected_qty": qty, "confidence": "high", "reasoning": "x"}
            for d in menu_da.dish_ids()
        ],
        "drivers": ["weekday_friday"],
    }


def test_valid_forecast_accepted():
    f = _validate(_valid_proposed())
    assert f.forecast_source == "gemini"
    assert len(f.dishes) == 6


def test_missing_dish_rejected():
    p = _valid_proposed()
    p["dishes"].pop()
    with pytest.raises(ForecastRejected):
        _validate(p)


def test_unknown_dish_rejected():
    p = _valid_proposed()
    p["dishes"][0]["dish_id"] = "sushi"
    with pytest.raises(ForecastRejected):
        _validate(p)


def test_negative_qty_rejected():
    p = _valid_proposed()
    p["dishes"][0]["expected_qty"] = -1
    with pytest.raises(ForecastRejected):
        _validate(p)


def test_non_integer_qty_rejected():
    p = _valid_proposed()
    p["dishes"][0]["expected_qty"] = 12.5
    with pytest.raises(ForecastRejected):
        _validate(p)


def test_ratio_out_of_band_rejected():
    # 6 dishes * 60 = 360 vs 80 covers -> ratio 4.5, far outside band.
    with pytest.raises(ForecastRejected):
        _validate(_valid_proposed(qty=60))
