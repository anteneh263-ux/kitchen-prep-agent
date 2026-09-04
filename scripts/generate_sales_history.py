"""Generate a deterministic synthetic sales_history.csv with built-in signals.

Signals: per-dish baseline (BASE_QTY), weekday factor (Fri/Sat high, Mon/Tue low),
rain factor (+~20% burgers/wings, salad-based slightly down), heat factor (>25C
dampens ribs), and 5-10% noise. Covers are derived so dishes-per-cover stays near
the configured ratio. Seeded for reproducibility (no wall-clock/random-at-import).
"""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitchen_prep import config  # noqa: E402

BURGERS_WINGS = {"classic_burger", "bacon_burger", "bbq_wings"}
SALAD_BASED = {"chicken_wrap", "chicken_plate"}

SEED = 42


def dish_weather_factor(dish_id: str, is_rain: bool, temp_c: float) -> float:
    factor = 1.0
    if is_rain and dish_id in BURGERS_WINGS:
        factor *= 1.20
    if is_rain and dish_id in SALAD_BASED:
        factor *= 0.90
    if temp_c > 25.0 and dish_id == "bbq_ribs":
        factor *= 0.85
    return factor


def generate() -> list[dict]:
    rng = random.Random(SEED)
    start = date.fromisoformat(config.SALES_HISTORY_START)
    end = date.fromisoformat(config.SALES_HISTORY_END)
    rows: list[dict] = []

    d = start
    while d <= end:
        # Weather for the day.
        if rng.random() < 0.30:
            precip = round(rng.uniform(1.0, 12.0), 1)
        else:
            precip = 0.0
        is_rain = precip > 0.0
        temp = round(rng.uniform(14.0, 30.0), 1)
        weather_code = rng.choice([61, 63, 80]) if is_rain else rng.choice([0, 1, 2, 3])

        wf = config.WEEKDAY_FACTOR[d.weekday()]
        day_qty: dict[str, int] = {}
        for dish_id, base in config.BASE_QTY.items():
            noise = 1.0 + rng.uniform(-0.08, 0.08)  # 5-10% magnitude
            q = base * wf * dish_weather_factor(dish_id, is_rain, temp) * noise
            day_qty[dish_id] = max(0, round(q))

        covers = max(1, round(sum(day_qty.values()) / config.DISHES_PER_COVER))
        for dish_id, q in day_qty.items():
            rows.append(
                {
                    "date": d.isoformat(),
                    "dish_id": dish_id,
                    "qty_sold": q,
                    "covers": covers,
                    "weather_code": weather_code,
                    "temperature_c": temp,
                    "precipitation_mm": precip,
                }
            )
        d += timedelta(days=1)
    return rows


def main() -> None:
    rows = generate()
    out = config.SALES_HISTORY_PATH
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "date",
                "dish_id",
                "qty_sold",
                "covers",
                "weather_code",
                "temperature_c",
                "precipitation_mm",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    days = len({r["date"] for r in rows})
    print(f"Wrote {len(rows)} rows across {days} days to {out}")


if __name__ == "__main__":
    main()
