"""Data contracts (dataclasses) and lightweight validators.

Authoritative quantities live in these structures. Gemini never mutates them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# --- Forecast (Gemini step 1 output, after validation) ---
@dataclass
class DishForecast:
    dish_id: str
    expected_qty: int
    confidence: str = "medium"
    reasoning: str = ""


@dataclass
class Forecast:
    forecast_date: str
    expected_covers: int
    dishes: list[DishForecast]
    drivers: list[str] = field(default_factory=list)
    forecast_source: str = "gemini"  # "gemini" | "deterministic_fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecast_date": self.forecast_date,
            "expected_covers": self.expected_covers,
            "dishes": [asdict(d) for d in self.dishes],
            "drivers": list(self.drivers),
            "forecast_source": self.forecast_source,
        }


CONFIDENCE_VALUES = {"high", "medium", "low"}


def validate_briefing(obj: Any) -> None:
    """Raise ValueError if the briefing does not meet the locked JSON contract."""
    if not isinstance(obj, dict):
        raise ValueError("briefing must be an object")
    if not isinstance(obj.get("summary"), str) or not obj["summary"].strip():
        raise ValueError("briefing.summary must be a non-empty string")
    if not isinstance(obj.get("priority_task_ids"), list) or not all(
        isinstance(x, str) for x in obj["priority_task_ids"]
    ):
        raise ValueError("briefing.priority_task_ids must be a list[str]")
    actions = obj.get("shortfall_actions")
    if not isinstance(actions, list):
        raise ValueError("briefing.shortfall_actions must be a list")
    for a in actions:
        if not isinstance(a, dict):
            raise ValueError("each shortfall_action must be an object")
        if not isinstance(a.get("item_id"), str):
            raise ValueError("shortfall_action.item_id must be a string")
        if not isinstance(a.get("recommended_action"), str):
            raise ValueError("shortfall_action.recommended_action must be a string")
        if not isinstance(a.get("requires_human_approval"), bool):
            raise ValueError("shortfall_action.requires_human_approval must be a bool")
    if not isinstance(obj.get("warnings"), list) or not all(
        isinstance(x, str) for x in obj["warnings"]
    ):
        raise ValueError("briefing.warnings must be a list[str]")
