"""Deterministic covers fallback for dates with no booking row.

Bookings only cover a fixed window, so a scheduled run past its end used to fail
with KeyError. These tests pin the replacement behaviour: booking rows still win,
the fallback is deterministic and leak-free, and an unusable history is a loud
error rather than a silent zero.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.data_access import bookings as bookings_da
from kitchen_prep.data_access import sales as sales_da

# A Thursday well past the end of bookings.csv (which ends 2026-08-16).
FUTURE_DATE = "2026-08-20"


def _rows(pairs):
    """Build sales rows as the reader returns them: one row per dish per date."""
    out = []
    for day, covers in pairs:
        for dish_id in ("classic_burger", "bbq_wings"):
            out.append(
                {
                    "date": day,
                    "dish_id": dish_id,
                    "qty_sold": 10,
                    "covers": covers,
                    "weather_code": 1,
                    "temperature_c": 20.0,
                    "precipitation_mm": 0.0,
                }
            )
    return out


@pytest.fixture()
def fake_history(monkeypatch):
    """Replace sales history with a controlled series."""

    def _install(pairs):
        monkeypatch.setattr(sales_da, "load_sales_rows", lambda: _rows(pairs))

    return _install


# --- Booking rows take priority -----------------------------------------


def test_exact_booking_row_is_used_unchanged():
    covers, source = bookings_da.resolve_expected_covers(config.DEMO_DATE)
    assert covers == 80
    assert source == bookings_da.COVERS_SOURCE_BOOKING


def test_booking_row_wins_even_when_history_would_disagree(fake_history):
    fake_history([("2026-08-06", 999), ("2026-08-13", 999)])
    covers, source = bookings_da.resolve_expected_covers(config.DEMO_DATE)
    assert covers == 80
    assert source == bookings_da.COVERS_SOURCE_BOOKING


def test_demo_date_pipeline_behaviour_is_unchanged(tmp_store):
    """The committed demo run must produce exactly what it produced before."""
    from kitchen_prep.gemini.client import OfflineClient
    from kitchen_prep.orchestrator import run_daily_prep

    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert plan["expected_covers"] == 80
    assert plan["covers_source"] == bookings_da.COVERS_SOURCE_BOOKING


# --- Fallback for dates past the booking window -------------------------


def test_date_past_the_booking_window_gets_a_positive_fallback():
    covers, source = bookings_da.resolve_expected_covers(FUTURE_DATE)
    assert isinstance(covers, int)
    assert covers > 0
    assert source.startswith("deterministic_history")


def test_expected_covers_contract_stays_an_int():
    assert isinstance(bookings_da.get_expected_covers(config.DEMO_DATE), int)
    assert isinstance(bookings_da.get_expected_covers(FUTURE_DATE), int)


def test_full_run_succeeds_for_a_date_with_no_booking_row(tmp_store):
    from kitchen_prep.gemini.client import OfflineClient
    from kitchen_prep.orchestrator import run_daily_prep

    plan = run_daily_prep(FUTURE_DATE, store=tmp_store, client=OfflineClient())
    assert plan["date"] == FUTURE_DATE
    assert plan["expected_covers"] > 0
    assert plan["covers_source"].startswith("deterministic_history")


# --- Determinism, weekday preference, leakage ---------------------------


def test_same_weekday_history_is_preferred(fake_history):
    # Target 2026-08-20 is a Thursday. Thursdays: 100, 120 -> mean 110.
    # Other weekdays carry a very different level and must not dilute it.
    fake_history(
        [
            ("2026-08-06", 100),  # Thursday
            ("2026-08-13", 120),  # Thursday
            ("2026-08-14", 10),   # Friday
            ("2026-08-15", 10),   # Saturday
        ]
    )
    covers, source = bookings_da.resolve_expected_covers(FUTURE_DATE)
    assert covers == 110
    assert source == bookings_da.COVERS_SOURCE_SAME_WEEKDAY


def test_overall_mean_is_used_only_when_no_same_weekday_data_exists(fake_history):
    # No Thursdays at all -> fall back to the overall mean: (40 + 60) / 2 = 50.
    fake_history([("2026-08-14", 40), ("2026-08-15", 60)])
    covers, source = bookings_da.resolve_expected_covers(FUTURE_DATE)
    assert covers == 50
    assert source == bookings_da.COVERS_SOURCE_OVERALL


def test_fallback_ignores_the_target_date_and_everything_after_it(fake_history):
    # Only 2026-08-06 (Thursday, 100) is strictly before the target. The target's
    # own row and the later Thursday are extreme values that must be excluded.
    fake_history(
        [
            ("2026-08-06", 100),   # Thursday, before target -> the only input
            (FUTURE_DATE, 5000),   # the target date itself
            ("2026-08-27", 9000),  # a later Thursday
        ]
    )
    covers, source = bookings_da.resolve_expected_covers(FUTURE_DATE)
    assert covers == 100
    assert source == bookings_da.COVERS_SOURCE_SAME_WEEKDAY


def test_daily_covers_before_collapses_repeated_rows_and_excludes_the_target(fake_history):
    fake_history([("2026-08-06", 100), ("2026-08-13", 120), (FUTURE_DATE, 5000)])
    series = sales_da.daily_covers_before(FUTURE_DATE)
    assert series == {"2026-08-06": 100, "2026-08-13": 120}


def test_fallback_is_deterministic_across_repeated_calls():
    assert len({bookings_da.resolve_expected_covers(FUTURE_DATE) for _ in range(5)}) == 1


# --- Unusable history fails loudly --------------------------------------


def test_empty_history_raises_instead_of_returning_zero(fake_history):
    fake_history([])
    with pytest.raises(bookings_da.CoversUnavailable):
        bookings_da.resolve_expected_covers(FUTURE_DATE)


def test_history_entirely_after_the_target_raises(fake_history):
    fake_history([(FUTURE_DATE, 90), ("2026-08-27", 90)])
    with pytest.raises(bookings_da.CoversUnavailable):
        bookings_da.resolve_expected_covers(FUTURE_DATE)


def test_non_positive_covers_are_unusable_and_raise(fake_history):
    fake_history([("2026-08-06", 0), ("2026-08-13", -5)])
    with pytest.raises(bookings_da.CoversUnavailable):
        bookings_da.resolve_expected_covers(FUTURE_DATE)


def test_non_positive_rows_are_dropped_rather_than_averaged_in(fake_history):
    # The zero must not drag the mean down; only the usable Thursday counts.
    fake_history([("2026-08-06", 0), ("2026-08-13", 120)])
    covers, _source = bookings_da.resolve_expected_covers(FUTURE_DATE)
    assert covers == 120
