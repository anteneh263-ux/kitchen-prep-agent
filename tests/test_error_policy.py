"""Programming errors must crash; only model-side failures use fallback."""
import logging

import pytest

from kitchen_prep import config
from kitchen_prep.data_access.bookings import CoversUnavailable
from kitchen_prep.gemini.client import GeminiUnavailable
from kitchen_prep.orchestrator import run_daily_prep


class UnavailableClient:
    def propose_forecast(self, context):
        raise GeminiUnavailable("simulated outage")

    def propose_briefing(self, plan):
        raise GeminiUnavailable("simulated outage")


class BuggyClient:
    def propose_forecast(self, context):
        raise KeyError("programming bug in model glue")

    def propose_briefing(self, plan):
        return {}


def test_model_unavailable_uses_fallback(tmp_store, caplog):
    caplog.set_level(logging.WARNING, logger="kitchen_prep.orchestrator")
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=UnavailableClient())
    assert plan["forecast"]["forecast_source"] == "deterministic_fallback"
    assert plan["forecast_note"] == "fallback:model_unavailable:simulated outage"
    assert plan["briefing_source"] == "deterministic_fallback"
    assert "forecast fallback: model unavailable: simulated outage" in caplog.text


def test_programming_error_propagates(tmp_store):
    with pytest.raises(KeyError):
        run_daily_prep(config.DEMO_DATE, store=tmp_store, client=BuggyClient())


def test_unresolvable_covers_crashes(tmp_store):
    # A missing booking row now falls back to history, but 2020-01-01 predates all
    # history too. No booking and nothing to estimate from is a data error, not a
    # model failure -> must crash rather than invent a number.
    with pytest.raises(CoversUnavailable):
        run_daily_prep("2020-01-01", store=tmp_store, client=UnavailableClient())


def test_run_log_written_on_failure(tmp_store):
    with pytest.raises(CoversUnavailable):
        run_daily_prep("2020-01-01", store=tmp_store, client=UnavailableClient())
    logs = list((tmp_store.logs_dir).glob("*.jsonl"))
    assert logs, "run log must be written even on failure"
    assert "error" in logs[0].read_text()
