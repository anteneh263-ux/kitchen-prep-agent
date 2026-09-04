"""Regression tests for the Gemini forecast contract.

Production evidence: Gemini returned the unknown dish_id "chilled_gazpacho_soup"
because the context never listed the real menu IDs and never carried the promised
sales history. These tests lock the context, the prompt and the validator.
"""
from datetime import date as _date

import pytest

from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.data_access import sales as sales_da
from kitchen_prep.gemini import forecast_step
from kitchen_prep.gemini.client import GeminiUnavailable, build_forecast_prompt
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline.forecast_validate import ForecastRejected, validate_and_build

DATE = config.DEMO_DATE  # 2026-08-14, a Friday
COVERS = 80
WEATHER = {"weather_code": 2, "temperature_c": 18.0, "precipitation_mm": 0.0}


def _context(date=DATE, covers=COVERS):
    return forecast_step.build_context(
        date, covers, WEATHER, _date.fromisoformat(date).weekday()
    )


def _valid_proposed(date=DATE, covers=COVERS, qty=14):
    return {
        "forecast_date": date,
        "expected_covers": covers,
        "dishes": [
            {
                "dish_id": d,
                "expected_qty": qty,
                "confidence": "medium",
                "reasoning": "same-weekday history",
            }
            for d in menu_da.dish_ids()
        ],
        "drivers": ["weekday_friday", "dry_weather"],
    }


def _validate(proposed, *, forecast_date=DATE, expected_covers=COVERS):
    return validate_and_build(
        proposed, forecast_date=forecast_date, expected_covers=expected_covers
    )


# --- 1. the production failure ------------------------------------------------
def test_chilled_gazpacho_soup_is_rejected():
    p = _valid_proposed()
    p["dishes"][0]["dish_id"] = "chilled_gazpacho_soup"
    with pytest.raises(ForecastRejected, match="chilled_gazpacho_soup"):
        _validate(p)


def test_chilled_gazpacho_soup_falls_back_deterministically(tmp_store, caplog):
    class GazpachoClient:
        def propose_forecast(self, context):
            p = _valid_proposed()
            p["dishes"].append(
                {
                    "dish_id": "chilled_gazpacho_soup",
                    "expected_qty": 5,
                    "confidence": "medium",
                    "reasoning": "invented",
                }
            )
            return p

        def propose_briefing(self, plan):
            raise GeminiUnavailable("offline")

    plan = run_daily_prep(DATE, store=tmp_store, client=GazpachoClient())
    assert plan["forecast"]["forecast_source"] == "deterministic_fallback"
    assert "fallback:rejected:unknown dish_id: 'chilled_gazpacho_soup'" == plan["forecast_note"]
    assert "forecast fallback: model response rejected" in caplog.text


# --- 2. context + prompt carry the real menu and the authority ----------------
def test_context_contains_every_real_menu_id_and_authority():
    ctx = _context()
    assert ctx["allowed_dish_ids"] == menu_da.dish_ids()
    assert ctx["forecast_date"] == DATE
    assert ctx["expected_covers"] == COVERS
    assert ctx["weekday"] == 4
    assert ctx["weather"] == WEATHER
    assert set(ctx["same_weekday_observations"]) == set(menu_da.dish_ids())


def test_prompt_lists_every_real_menu_id_and_the_hard_rules():
    prompt = build_forecast_prompt(_context())
    for dish_id in menu_da.dish_ids():
        assert dish_id in prompt, dish_id
    assert "chilled_gazpacho_soup" not in prompt
    lowered = prompt.lower()
    assert "exactly one forecast row" in lowered
    assert "never invent" in lowered
    assert "duplicate" in lowered
    assert "unchanged" in lowered
    assert "only json" in lowered
    # Historical demand must be normalized per cover, not copied through.
    assert "qty_per_cover" in prompt
    assert "never copy" in lowered
    # The existing schema is still what the model is asked for.
    assert "dish_id, expected_qty(int), confidence, reasoning" in prompt
    assert DATE in prompt and str(COVERS) in prompt


# --- 3. history is strictly before the target date ----------------------------
def test_same_weekday_observations_are_strictly_before_target_date():
    # 2026-08-07 is a Friday that HAS rows in the sales history, so a >= bug
    # would leak the target date itself into the context.
    target = "2026-08-07"
    ctx = _context(date=target)
    rows = sales_da.load_sales_rows()

    for dish_id, observations in ctx["same_weekday_observations"].items():
        expected = [
            r["date"]
            for r in sorted(rows, key=lambda r: r["date"])
            if r["dish_id"] == dish_id
            and r["date"] < target
            and _date.fromisoformat(r["date"]).weekday() == 4
        ][-4:]
        assert [o["date"] for o in observations] == expected
        assert len(observations) == 4
        assert all(o["date"] < target for o in observations)

        on_target = [
            r["qty_sold"] for r in rows if r["dish_id"] == dish_id and r["date"] == target
        ]
        assert on_target, "fixture assumption: target date has sales rows"


# --- 4. the authoritative fields cannot be changed ----------------------------
def test_changed_forecast_date_rejected():
    p = _valid_proposed(date="2026-08-15")
    with pytest.raises(ForecastRejected, match="forecast_date"):
        _validate(p)


def test_changed_expected_covers_rejected():
    p = _valid_proposed(covers=120)
    with pytest.raises(ForecastRejected, match="expected_covers"):
        _validate(p)


def test_duplicate_dish_id_rejected():
    p = _valid_proposed()
    p["dishes"].append(dict(p["dishes"][0]))
    with pytest.raises(ForecastRejected, match="duplicate"):
        _validate(p)


def test_invalid_confidence_rejected():
    p = _valid_proposed()
    p["dishes"][0]["confidence"] = "very_high"
    with pytest.raises(ForecastRejected, match="confidence"):
        _validate(p)


def test_empty_reasoning_rejected():
    p = _valid_proposed()
    p["dishes"][0]["reasoning"] = "   "
    with pytest.raises(ForecastRejected, match="reasoning"):
        _validate(p)


def test_empty_drivers_rejected():
    p = _valid_proposed()
    p["drivers"] = []
    with pytest.raises(ForecastRejected, match="drivers"):
        _validate(p)


# --- 5. a compliant model answer is still accepted ----------------------------
def test_valid_model_output_keeps_gemini_source():
    f = _validate(_valid_proposed())
    assert f.forecast_source == "gemini"
    assert f.forecast_date == DATE
    assert f.expected_covers == COVERS
    assert [d.dish_id for d in f.dishes] == menu_da.dish_ids()


def test_valid_model_output_end_to_end_is_gemini(tmp_store):
    class CompliantClient:
        def propose_forecast(self, context):
            assert context["allowed_dish_ids"] == menu_da.dish_ids()
            return {
                "forecast_date": context["forecast_date"],
                "expected_covers": context["expected_covers"],
                # 6 * 14 = 84 vs 80 covers -> ratio 1.05, inside the locked band.
                "dishes": [
                    {
                        "dish_id": d,
                        "expected_qty": 14,
                        "confidence": "medium",
                        "reasoning": f"last {len(h)} same-weekday observations",
                    }
                    for d, h in context["same_weekday_observations"].items()
                ],
                "drivers": ["weekday_friday"],
            }

        def propose_briefing(self, plan):
            raise GeminiUnavailable("offline")

    plan = run_daily_prep(DATE, store=tmp_store, client=CompliantClient())
    assert plan["forecast"]["forecast_source"] == "gemini"
    assert plan["forecast"]["forecast_date"] == DATE
    assert plan["forecast"]["expected_covers"] == plan["expected_covers"]
