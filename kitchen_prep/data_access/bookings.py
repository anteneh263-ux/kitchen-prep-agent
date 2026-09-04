"""Read bookings (authoritative expected covers per date).

An exact booking row always wins. When a date has no booking row — which is the
normal case once the scheduled run moves past the end of the booking file — the
covers figure is estimated deterministically from sales history instead of
failing the run. The estimate never reads the target date or any later date, and
it refuses to return a non-positive number: an unusable history is a loud error,
not a silent zero.
"""
from __future__ import annotations

import csv
from datetime import date as _date
from functools import lru_cache

from .. import config
from . import sales as sales_da

# How the covers figure for a run was obtained. Recorded on the plan and the run
# log so a fallback is never invisible.
COVERS_SOURCE_BOOKING = "booking_file"
COVERS_SOURCE_SAME_WEEKDAY = "deterministic_history_same_weekday"
COVERS_SOURCE_OVERALL = "deterministic_history_overall"


class CoversUnavailable(LookupError):
    """No booking row and no usable history from which to estimate covers."""


@lru_cache(maxsize=1)
def _load() -> dict[str, int]:
    out: dict[str, int] = {}
    with open(config.BOOKINGS_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["date"]] = int(row["expected_covers"])
    return out


def _estimate_from_history(date: str) -> tuple[int, str]:
    """Rounded mean of historical daily covers, preferring the same weekday.

    Uses only dates strictly before ``date``. Raises ``CoversUnavailable`` rather
    than returning zero when there is nothing usable to average.
    """
    series = sales_da.daily_covers_before(date)
    if not series:
        raise CoversUnavailable(
            f"No booking for {date} and no usable covers history strictly before it"
        )

    weekday = _date.fromisoformat(date).weekday()
    same_weekday = [
        covers
        for day, covers in series.items()
        if _date.fromisoformat(day).weekday() == weekday
    ]

    if same_weekday:
        basis, source = same_weekday, COVERS_SOURCE_SAME_WEEKDAY
    else:
        basis, source = list(series.values()), COVERS_SOURCE_OVERALL

    covers = int(round(sum(basis) / len(basis)))
    if covers <= 0:
        raise CoversUnavailable(
            f"Estimated covers for {date} is {covers}; history is unusable"
        )
    return covers, source


def resolve_expected_covers(date: str) -> tuple[int, str]:
    """Return ``(expected_covers, covers_source)`` for ``date``.

    An exact booking row is used unchanged. Otherwise the figure is estimated
    from history and the source marks it as a deterministic fallback.
    """
    bookings = _load()
    if date in bookings:
        return bookings[date], COVERS_SOURCE_BOOKING
    return _estimate_from_history(date)


def get_expected_covers(date: str) -> int:
    """Expected covers for ``date`` as an int (booking row, else history estimate)."""
    covers, _source = resolve_expected_covers(date)
    return covers
