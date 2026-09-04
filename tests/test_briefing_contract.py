import pytest

from kitchen_prep.contracts import validate_briefing
from kitchen_prep.data_access import menu as menu_da
from kitchen_prep.gemini.briefing_step import build_deterministic_briefing
from kitchen_prep.render import markdown as md_render
from kitchen_prep.units import UnitResolutionError


def _plan():
    return {
        "date": "2026-08-14",
        "expected_covers": 80,
        "forecast": {"forecast_source": "deterministic_fallback"},
        "prep_tasks": [
            {"task_id": "prep_bbq_ribs", "dish_id": "bbq_ribs", "qty": 20, "prep_minutes": 80.0, "priority": 1},
            {"task_id": "prep_classic_burger", "dish_id": "classic_burger", "qty": 42, "prep_minutes": 63.0, "priority": 2},
        ],
        "prep_shortfalls": [
            {"item_id": "beef_patty", "required": 67, "available": 40, "shortfall": 27}
        ],
        "replenishment_orders": [{"item_id": "beef_patty"}],
        "waste_flagged": [
            {"batch_id": "b06", "item_id": "pork_ribs", "qty": 7, "expiry_date": "2026-08-13"}
        ],
    }


def test_deterministic_briefing_matches_contract():
    b = build_deterministic_briefing(_plan())
    validate_briefing(b)  # raises on any contract violation
    assert b["priority_task_ids"] == ["prep_bbq_ribs", "prep_classic_burger"]
    assert b["shortfall_actions"][0]["item_id"] == "beef_patty"
    assert b["shortfall_actions"][0]["requires_human_approval"] is True
    assert "Today's shortfall" in b["shortfall_actions"][0]["recommended_action"]
    assert "guests expected" in b["summary"]
    assert any("b06" in w for w in b["warnings"])


def test_validate_briefing_rejects_bad_types():
    with pytest.raises(ValueError):
        validate_briefing({"summary": "", "priority_task_ids": [], "shortfall_actions": [], "warnings": []})
    with pytest.raises(ValueError):
        validate_briefing({"summary": "x", "priority_task_ids": "no", "shortfall_actions": [], "warnings": []})


def _plan_non_piece_units():
    plan = _plan()
    plan["planning_basis"] = "today_consumption_plus_par"
    plan["prep_tasks"] = [
        {"task_id": "prep_bbq_wings", "dish_id": "bbq_wings", "qty": 16,
         "prep_minutes": 32.0, "priority": 1},
    ]
    plan["prep_shortfalls"] = [
        {"item_id": "bbq_sauce", "required": 6.2, "available": 4.0, "shortfall": 2.2},
        {"item_id": "chicken_wings", "required": 11.5, "available": 9.0, "shortfall": 2.5},
    ]
    plan["replenishment_orders"] = [
        {"item_id": "bbq_sauce", "order_qty": 5, "unit": "l", "supplier": "DryGoods",
         "delivery_date": "2026-08-17"},
    ]
    plan["waste_flagged"] = [
        {"batch_id": "b10", "item_id": "bbq_sauce", "qty": 1.5, "expiry_date": "2026-08-13"},
    ]
    return plan


def test_deterministic_briefing_uses_authoritative_units():
    actions = build_deterministic_briefing(_plan_non_piece_units())["shortfall_actions"]
    by_item = {a["item_id"]: a["recommended_action"] for a in actions}
    assert "Today's shortfall: 2.2 l." in by_item["bbq_sauce"]
    assert "Today's shortfall: 2.5 kg." in by_item["chicken_wings"]


def test_briefing_markdown_shortfalls_and_waste_carry_units():
    plan = _plan_non_piece_units()
    plan["briefing"] = build_deterministic_briefing(plan)
    text = md_render.render(plan)
    # Shortfalls: required, available and shortfall all carry the unit.
    assert "- bbq_sauce: requires 6.2 l, available 4.0 l → shortfall 2.2 l" in text
    assert "- chicken_wings: requires 11.5 kg, available 9.0 kg → shortfall 2.5 kg" in text
    # Waste.
    assert "- b10 bbq_sauce (1.5 l) expired 2026-08-13" in text
    # Orders keep the unit persisted on the order record.
    assert "- bbq_sauce: order 5 l from DryGoods" in text


def test_briefing_markdown_prep_quantities_are_portions():
    plan = _plan_non_piece_units()
    plan["briefing"] = build_deterministic_briefing(plan)
    text = md_render.render(plan)
    assert "1. bbq_wings — 16 portions (32.0 min)" in text
    assert " stk " not in text


def test_briefing_markdown_piece_ingredients_render_as_pcs():
    plan = _plan()
    plan["planning_basis"] = "today_consumption_plus_par"
    plan["replenishment_orders"] = [
        {"item_id": "beef_patty", "order_qty": 60, "unit": "stk", "supplier": "MeatCo",
         "delivery_date": "2026-08-15"},
    ]
    plan["briefing"] = build_deterministic_briefing(plan)
    text = md_render.render(plan)
    # The Markdown briefing is English throughout, so "stk" is labelled "pcs".
    assert "- beef_patty: requires 67 pcs, available 40 pcs → shortfall 27 pcs" in text
    assert "- b06 pork_ribs (7 kg) expired 2026-08-13" in text
    assert "- beef_patty: order 60 pcs from MeatCo" in text


# --- Deterministic briefing units ---------------------------------------
# The briefing sentence and the dashboard card describe the same shortfall, so
# they must resolve the unit through the same authoritative lookup.


def _briefing_actions(shortfalls, waste=()):
    plan = _plan()
    plan["prep_shortfalls"] = list(shortfalls)
    plan["waste_flagged"] = list(waste)
    return build_deterministic_briefing(plan)


def test_english_briefing_labels_stk_as_pcs():
    b = _briefing_actions([{"item_id": "beef_patty", "required": 71, "available": 40,
                           "shortfall": 31}])
    action = b["shortfall_actions"][0]["recommended_action"]
    assert action == "Today's shortfall: 31 pcs. Prep or source before service."
    assert "stk" not in action


def test_english_briefing_uses_litres_for_bbq_sauce():
    b = _briefing_actions([{"item_id": "bbq_sauce", "required": 6.2, "available": 4.0,
                            "shortfall": 2.2}])
    assert "Today's shortfall: 2.2 l." in b["shortfall_actions"][0]["recommended_action"]


def test_english_briefing_uses_kilograms_for_mass_ingredients():
    shortfalls = [
        {"item_id": "chicken_wings", "required": 11.5, "available": 9.0, "shortfall": 2.5},
        {"item_id": "cheddar", "required": 3.6, "available": 2.8, "shortfall": 0.8},
        {"item_id": "pork_ribs", "required": 8.5, "available": 6.0, "shortfall": 2.5},
    ]
    by_item = {
        a["item_id"]: a["recommended_action"]
        for a in _briefing_actions(shortfalls)["shortfall_actions"]
    }
    assert "Today's shortfall: 2.5 kg." in by_item["chicken_wings"]
    assert "Today's shortfall: 0.8 kg." in by_item["cheddar"]
    assert "Today's shortfall: 2.5 kg." in by_item["pork_ribs"]


def test_waste_warnings_carry_the_authoritative_unit():
    waste = [
        {"batch_id": "b09", "item_id": "cheddar", "qty": 2.8, "expiry_date": "2026-08-13"},
        {"batch_id": "b10", "item_id": "bbq_sauce", "qty": 1.5, "expiry_date": "2026-08-13"},
        {"batch_id": "b01", "item_id": "beef_patty", "qty": 6, "expiry_date": "2026-08-13"},
    ]
    warnings = _briefing_actions([], waste)["warnings"]
    assert "Batch b09 (cheddar, 2.8 kg) expired 2026-08-13 — discard." in warnings
    assert "Batch b10 (bbq_sauce, 1.5 l) expired 2026-08-13 — discard." in warnings
    assert "Batch b01 (beef_patty, 6 pcs) expired 2026-08-13 — discard." in warnings


def test_briefing_wording_and_contract_are_otherwise_unchanged():
    b = build_deterministic_briefing(_plan())
    validate_briefing(b)
    assert b["shortfall_actions"][0]["recommended_action"].endswith(
        ". Prep or source before service."
    )
    assert b["shortfall_actions"][0]["requires_human_approval"] is True
    assert b["priority_task_ids"] == ["prep_bbq_ribs", "prep_classic_burger"]
    assert b["summary"] == (
        "80 guests expected on 2026-08-14. 2 prep tasks, 1 shortfalls today, "
        "1 replenishment orders."
    )
    assert b["warnings"][-1] == "Forecast used the deterministic fallback (no model output)."


def test_briefing_fails_clearly_for_an_unknown_ingredient():
    with pytest.raises(UnitResolutionError, match="unknown ingredient id 'unicorn_steak'"):
        _briefing_actions([{"item_id": "unicorn_steak", "required": 1, "available": 0,
                            "shortfall": 1}])


def test_briefing_fails_clearly_for_an_unsupported_unit(monkeypatch):
    broken = {k: dict(v) for k, v in menu_da.ingredients_by_id().items()}
    broken["cheddar"]["unit"] = "lbs"
    monkeypatch.setattr(menu_da, "ingredients_by_id", lambda: broken)
    with pytest.raises(UnitResolutionError, match="ingredient 'cheddar'"):
        _briefing_actions([{"item_id": "cheddar", "required": 1, "available": 0,
                            "shortfall": 1}])
