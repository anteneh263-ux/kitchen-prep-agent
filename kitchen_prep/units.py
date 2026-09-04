"""Authoritative measurement units for display.

``data/ingredients.json`` is the single authority for an ingredient's unit. This
module is the only place that knows which units exist and how they are labelled,
so every surface — HTML, Markdown, the deterministic briefing and the data
verification script — resolves a unit through the same path and cannot disagree.

Nothing here guesses: an unknown ingredient id, a missing unit or a unit outside
``SUPPORTED_UNITS`` raises ``UnitResolutionError`` rather than silently falling
back to a piece count.

This module is presentation-neutral and deliberately depends only on the
ingredient master, never on a renderer.
"""
from __future__ import annotations

from .data_access import menu as menu_da

# The authoritative units an ingredient may declare.
SUPPORTED_UNITS = ("stk", "kg", "l")

# "stk" is the Norwegian piece unit; it is labelled "pcs" in English. Mass and
# volume units are written identically in both languages.
_UNIT_LABELS = {
    "stk": {"en": "pcs", "no": "stk"},
    "kg": {"en": "kg", "no": "kg"},
    "l": {"en": "l", "no": "l"},
}

# A prep task quantity counts portions of a dish, not pieces of an ingredient.
_PORTION_LABELS = {"en": "portions", "no": "porsjoner"}


class UnitResolutionError(ValueError):
    """Raised when an authoritative unit cannot be resolved for display."""


def _lang(language: str) -> str:
    return "en" if language == "en" else "no"


def portion_label(language: str) -> str:
    """Localised label for a dish-portion quantity."""
    return _PORTION_LABELS[_lang(language)]


def unit_label(unit: object, language: str) -> str:
    """Localised label for an authoritative unit. Unsupported units raise."""
    labels = _UNIT_LABELS.get(unit) if isinstance(unit, str) else None
    if labels is None:
        raise UnitResolutionError(
            f"unsupported unit {unit!r}; supported units are {list(SUPPORTED_UNITS)}"
        )
    return labels[_lang(language)]


def ingredient_unit(item_id: object, language: str) -> str:
    """Localised authoritative unit for ``item_id`` from the ingredient master.

    Raises ``UnitResolutionError`` for an unknown ingredient id or an ingredient
    whose unit is missing or outside the supported set.
    """
    ingredients = menu_da.ingredients_by_id()
    if not isinstance(item_id, str) or item_id not in ingredients:
        raise UnitResolutionError(
            f"unknown ingredient id {item_id!r}; not present in the ingredient master"
        )
    try:
        return unit_label(ingredients[item_id].get("unit"), language)
    except UnitResolutionError as exc:
        raise UnitResolutionError(f"ingredient {item_id!r}: {exc}") from None
