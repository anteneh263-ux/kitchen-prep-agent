"""First-Expired-First-Out inventory handling."""
from __future__ import annotations

from datetime import date as _date


def split_expired(batches: list[dict], run_date: str) -> tuple[list[dict], list[dict]]:
    """Return (valid, waste_flagged). A batch is waste_flagged when it has already
    expired as of the run date (expiry_date < run_date)."""
    d = _date.fromisoformat(run_date)
    valid, waste = [], []
    for b in batches:
        if _date.fromisoformat(b["expiry_date"]) < d:
            waste.append(b)
        else:
            valid.append(b)
    return valid, waste


def fefo_sorted(batches: list[dict]) -> list[dict]:
    """Sort batches by expiry ascending, then batch_id for stable ordering."""
    return sorted(batches, key=lambda b: (b["expiry_date"], b["batch_id"]))


def consume(required: float, batches: list[dict]) -> tuple[list[dict], float, list[dict]]:
    """Consume ``required`` units from ``batches`` in FEFO order.

    Returns (consumption, uncovered, remaining_batches) where consumption is a list
    of {batch_id, item_id, qty_consumed} and remaining_batches carries the leftover
    quantities (batches fully consumed are dropped).
    """
    consumption: list[dict] = []
    remaining: list[dict] = []
    need = required
    for b in fefo_sorted(batches):
        if need <= 1e-9:
            remaining.append(dict(b))
            continue
        take = min(need, b["qty"])
        if take > 0:
            consumption.append(
                {"batch_id": b["batch_id"], "item_id": b["item_id"], "qty_consumed": round(take, 3)}
            )
            need -= take
        leftover = round(b["qty"] - take, 3)
        if leftover > 1e-9:
            nb = dict(b)
            nb["qty"] = leftover
            remaining.append(nb)
    uncovered = round(max(0.0, need), 3)
    return consumption, uncovered, remaining
