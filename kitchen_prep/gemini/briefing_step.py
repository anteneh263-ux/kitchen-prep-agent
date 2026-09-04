"""Gemini step 2: produce a validated JSON briefing.

The model only narrates. When offline or the model is unavailable, a deterministic
builder produces the same JSON contract. Python renders Markdown from this JSON;
the model never returns the authoritative plan as free-form Markdown.
"""
from __future__ import annotations

from ..contracts import validate_briefing
from ..gemini.client import GeminiUnavailable
from ..units import ingredient_unit


def build_deterministic_briefing(plan: dict) -> dict:
    prep_tasks = plan["prep_tasks"]
    shortfalls = plan["prep_shortfalls"]
    waste = plan["waste_flagged"]

    priority_task_ids = [t["task_id"] for t in sorted(prep_tasks, key=lambda t: t["priority"])]

    shortfall_actions = []
    for s in shortfalls:
        unit = ingredient_unit(s["item_id"], "en")
        shortfall_actions.append(
            {
                "item_id": s["item_id"],
                "recommended_action": (
                    f"Today's shortfall: {s['shortfall']} {unit}. Prep or source before service."
                ),
                "requires_human_approval": True,
            }
        )

    warnings = [
        f"Batch {b['batch_id']} ({b['item_id']}, {b['qty']} "
        f"{ingredient_unit(b['item_id'], 'en')}) expired {b['expiry_date']} — discard."
        for b in waste
    ]
    if plan["forecast"]["forecast_source"] == "deterministic_fallback":
        warnings.append("Forecast used the deterministic fallback (no model output).")

    summary = (
        f"{plan['expected_covers']} guests expected on {plan['date']}. "
        f"{len(prep_tasks)} prep tasks, {len(shortfalls)} shortfalls today, "
        f"{len(plan['replenishment_orders'])} replenishment orders."
    )

    briefing = {
        "summary": summary,
        "priority_task_ids": priority_task_ids,
        "shortfall_actions": shortfall_actions,
        "warnings": warnings,
    }
    validate_briefing(briefing)
    return briefing


def make_briefing(client, plan: dict) -> tuple[dict, str]:
    """Return (briefing_json, briefing_source)."""
    try:
        raw = client.propose_briefing(plan)
        validate_briefing(raw)
        return raw, "gemini"
    except (GeminiUnavailable, ValueError):
        return build_deterministic_briefing(plan), "deterministic_fallback"
