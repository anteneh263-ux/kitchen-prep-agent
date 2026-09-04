"""Real Gemini integration. Skipped unless GOOGLE_API_KEY is set. Requires network.

Run explicitly with:  pytest -m integration
"""
import os

import pytest

from kitchen_prep import config
from kitchen_prep.orchestrator import run_daily_prep

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("GOOGLE_API_KEY"), reason="no GOOGLE_API_KEY")
def test_real_gemini_run(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store)
    # With a real key, both controlled steps MUST use the model. This test fails
    # if the system silently degraded to deterministic_fallback.
    assert plan["forecast"]["forecast_source"] == "gemini"
    assert plan["briefing_source"] == "gemini"
    assert plan["expected_covers"] == 80
