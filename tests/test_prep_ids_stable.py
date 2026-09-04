from kitchen_prep.contracts import DishForecast, Forecast
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.pipeline import prep as prep_pipe


def _forecast():
    dishes = [DishForecast(dish_id=d, expected_qty=20) for d in menu_da.dish_ids()]
    return Forecast(forecast_date="2026-08-14", expected_covers=80, dishes=dishes)


def test_prep_ids_are_prefixed_and_complete():
    tasks = prep_pipe.build_prep_tasks(_forecast())
    ids = {t["task_id"] for t in tasks}
    assert ids == {f"prep_{d}" for d in menu_da.dish_ids()}
    assert "prep_bbq_wings" in ids
    assert "prep_buffalo_wings" not in ids


def test_prep_ordering_is_deterministic():
    a = [t["task_id"] for t in prep_pipe.build_prep_tasks(_forecast())]
    b = [t["task_id"] for t in prep_pipe.build_prep_tasks(_forecast())]
    assert a == b
    # Priorities are a contiguous 1..N ranking.
    prios = sorted(t["priority"] for t in prep_pipe.build_prep_tasks(_forecast()))
    assert prios == list(range(1, len(prios) + 1))
