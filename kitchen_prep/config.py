"""Central configuration and locked constants for Kitchen Prep Agent."""
from __future__ import annotations

import os
from pathlib import Path

# --- Model (fixed, do not change) ---
MODEL_ID = "gemini-3.5-flash"

# --- Location (Oslo) for weather ---
RESTAURANT_LAT = float(os.environ.get("RESTAURANT_LAT", "59.91"))
RESTAURANT_LON = float(os.environ.get("RESTAURANT_LON", "10.75"))
TIMEZONE = "Europe/Oslo"

# --- Forecast validation band (B6) ---
DISHES_PER_COVER = 1.034
RATIO_MIN = 0.724
RATIO_MAX = 1.344  # +/-30% band around the per-cover ratio

# --- Sales-history generation baselines (B7) ---
BASE_QTY = {
    "classic_burger": 30,
    "bacon_burger": 18,
    "bbq_ribs": 14,
    "bbq_wings": 16,
    "chicken_wrap": 12,
    "chicken_plate": 10,
}

# Weekday multipliers (Mon=0 .. Sun=6). Fri/Sat ~1.4, Mon/Tue ~0.7.
WEEKDAY_FACTOR = {0: 0.70, 1: 0.70, 2: 0.85, 3: 1.00, 4: 1.40, 5: 1.40, 6: 1.10}

# --- Money ---
# Food cost as a share of menu price. A dish above this is flagged for a human;
# nothing in the system re-prices anything by itself.
TARGET_FOOD_COST_RATIO = 0.32
CURRENCY = "NOK"

# --- Intraday re-planning ---
# Below this much of the day's sales, the pace signal is too thin to rescale
# from: three covers at 11:05 must not imply a 400-cover day.
SERVICE_MIN_SHARE_FOR_REVISION = 0.15

# The revised day total is clamped to this band around the morning forecast. A
# strange hour may bend the plan; it may not rewrite it.
SERVICE_REVISION_BAND = 0.30

# --- Prep stations ---
# Stations come from the ingredient master; these are only the display labels.
# An ingredient naming a station that is not listed here still produces a task —
# it is labelled from its id rather than dropped, because a missing label must
# never silently remove work from the prep list.
STATION_LABELS = {
    "cold": {"no": "Kaldkjøkken", "en": "Cold station"},
    "butchery": {"no": "Kjøttdisk", "en": "Butchery"},
    "hot": {"no": "Varmkjøkken", "en": "Hot station"},
    "bakery": {"no": "Bakeri", "en": "Bakery"},
}

# Demonstration / reference run date.
DEMO_DATE = "2026-08-14"

# Sales-history generation window (must end before any forecast date -> no leakage).
SALES_HISTORY_START = "2026-06-15"
SALES_HISTORY_END = "2026-08-13"

# --- Paths ---
PKG_DIR = Path(__file__).resolve().parent
DATA_DIR = PKG_DIR / "data"
MENU_PATH = DATA_DIR / "menu.json"
INGREDIENTS_PATH = DATA_DIR / "ingredients.json"
BOOKINGS_PATH = DATA_DIR / "bookings.csv"
BATCHES_PATH = DATA_DIR / "inventory_batches.json"
SALES_HISTORY_PATH = DATA_DIR / "sales_history.csv"
SERVICE_CURVE_PATH = DATA_DIR / "service_curve.json"

# Local dev store root (never committed).
OUT_DIR = Path(os.environ.get("KP_OUT_DIR", PKG_DIR.parent / "out"))


def gemini_enabled() -> bool:
    """Real Gemini is used only when an API key is present; otherwise offline/mock."""
    return bool(os.environ.get("GOOGLE_API_KEY"))
