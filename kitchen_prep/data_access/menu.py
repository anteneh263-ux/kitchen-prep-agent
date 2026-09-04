"""Read menu + ingredient master data."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .. import config


@lru_cache(maxsize=1)
def load_menu() -> list[dict[str, Any]]:
    with open(config.MENU_PATH, encoding="utf-8") as fh:
        return json.load(fh)["dishes"]


@lru_cache(maxsize=1)
def load_ingredients() -> list[dict[str, Any]]:
    with open(config.INGREDIENTS_PATH, encoding="utf-8") as fh:
        return json.load(fh)["ingredients"]


@lru_cache(maxsize=1)
def menu_by_id() -> dict[str, dict[str, Any]]:
    return {d["id"]: d for d in load_menu()}


@lru_cache(maxsize=1)
def ingredients_by_id() -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in load_ingredients()}


def dish_ids() -> list[str]:
    return [d["id"] for d in load_menu()]
