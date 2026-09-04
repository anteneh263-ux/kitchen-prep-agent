"""Local end-to-end run for the demo date, offline (no network, no API key)."""
from kitchen_prep import config
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep


def test_full_pipeline_offline(tmp_store):
    plan = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())

    # Structure
    for key in [
        "date", "expected_covers", "planning_basis", "forecast", "ingredient_requirements",
        "prep_tasks", "fefo_consumption", "prep_shortfalls", "replenishment_orders",
        "waste_flagged", "remaining_stock", "briefing", "briefing_markdown",
    ]:
        assert key in plan, key

    assert plan["planning_basis"] == "today_consumption_plus_par"
    assert plan["forecast"]["forecast_source"] == "deterministic_fallback"
    assert plan["briefing_source"] == "deterministic_fallback"
    assert plan["expected_covers"] == 80

    # b06 (pork_ribs, expired 08-13) must be flagged as waste.
    assert any(w["batch_id"] == "b06" for w in plan["waste_flagged"])

    # FEFO: for beef_patty, b01 is consumed before b02.
    beef = [c for c in plan["fefo_consumption"] if c["item_id"] == "beef_patty"]
    if len(beef) >= 2:
        assert beef[0]["batch_id"] == "b01"

    assert plan["briefing_markdown"].startswith("# Kitchen briefing")
    assert "Expected guests" in plan["briefing_markdown"]


def test_idempotent_no_duplicate(tmp_store):
    p1 = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    p2 = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert p1["generated_at"] == p2["generated_at"]  # same stored plan, not regenerated
    plan_files = list(tmp_store.plans_dir.glob("*.json"))
    assert len(plan_files) == 1


def test_force_replaces_existing_plan(tmp_store):
    original = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())
    assert original["forecast"]["forecast_source"] == "deterministic_fallback"
    tmp_store.record_plan_action(
        config.DEMO_DATE,
        "beef_patty",
        "approved",
        "2026-08-14T08:00:00+00:00",
    )

    class ValidForecastClient(OfflineClient):
        def propose_forecast(self, context):
            return {
                "forecast_date": context["forecast_date"],
                "expected_covers": context["expected_covers"],
                "dishes": [
                    {
                        "dish_id": dish_id,
                        "expected_qty": 14,
                        "confidence": "medium",
                        "reasoning": "same-weekday history",
                    }
                    for dish_id in menu_da.dish_ids()
                ],
                "drivers": ["same-weekday history"],
            }

    replaced = run_daily_prep(
        config.DEMO_DATE,
        store=tmp_store,
        client=ValidForecastClient(),
        force=True,
    )

    assert replaced["forecast"]["forecast_source"] == "gemini"
    assert replaced["operational_actions"]["beef_patty"]["status"] == "approved"
    assert replaced["action_history"][-1]["status"] == "approved"
    assert tmp_store.get_plan(config.DEMO_DATE)["forecast"]["forecast_source"] == "gemini"
    assert len(list(tmp_store.plans_dir.glob("*.json"))) == 1
