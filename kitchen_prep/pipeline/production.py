"""Recorded production: what the kitchen actually made against what was planned.

The pipeline otherwise assumes the prep plan was executed. It never was, not
every day: a station runs out of time, a pot scorches, someone goes home sick.
Nothing in the published plan said so, and the first anyone knew was service.

**Why this does not touch inventory.** A goods receipt and a stock count both
correct *stock*, so they are applied while the day's inventory input is frozen.
Production is different. If the hot station made 6 of the planned 8 litres, the
raw material it did not use is still in the walk-in — but nothing here knows
which batch it came back to or what expiry it now carries, and inventing that
would be exactly the kind of guessing the rest of the system refuses. So a
production record states execution, and the stock consequence is corrected by a
stock count, which is the truthful instrument for it.

That makes this an execution log, not an inventory adjustment: it is recorded
against the plan for the day being worked, append-only, and a forced replay
carries it forward untouched.
"""
from __future__ import annotations

from typing import Any, Iterable

_EPS = 1e-9


class ProductionRejected(ValueError):
    """A recorded production figure is malformed or names no planned job."""


def _as_qty(value: Any) -> float:
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductionRejected("produced_qty must be a number") from exc
    if qty != qty or qty in (float("inf"), float("-inf")):
        raise ProductionRejected("produced_qty must be finite")
    if qty < 0:
        raise ProductionRejected("produced_qty must not be negative")
    return round(qty, 3)


def find_task(task_id: str, station_tasks: Iterable[dict]) -> dict:
    """The planned job this record is about. An unknown job is refused."""
    for task in station_tasks:
        if task.get("task_id") == task_id:
            return task
    raise ProductionRejected(f"unknown prep task: {task_id or '(missing)'}")


def validate_production(payload: dict, station_tasks: Iterable[dict], *, recorded_at: str) -> dict:
    """Return a stored-shape production record, or raise ``ProductionRejected``.

    The planned quantity is read from the plan, never from the payload: a client
    cannot move the target it is being measured against.
    """
    task = find_task(str(payload.get("task_id", "")).strip(), station_tasks)

    raw = payload.get("produced_qty", payload.get("qty"))
    if raw is None or raw == "":
        raise ProductionRejected("produced_qty is required")
    produced = _as_qty(raw)

    planned = round(float(task.get("prepare_qty", 0)), 3)
    return {
        "task_id": task["task_id"],
        "item_id": task["item_id"],
        "station": task["station"],
        "unit": task["unit"],
        "planned_qty": planned,
        "produced_qty": produced,
        "variance_qty": round(produced - planned, 3),
        "recorded_at": recorded_at,
        "recorded_by": str(payload.get("recorded_by") or "kitchen")[:60],
        "note": str(payload.get("note") or "")[:200],
    }


def completion(station_tasks: Iterable[dict], actuals: dict[str, dict] | None) -> dict:
    """How the day's prep actually went, per station task.

    A job with no record is *unrecorded*, not complete and not short. Treating
    silence as success is how a missed prep job reaches service unnoticed; so is
    treating it as a failure, which would drown the real shortfalls in noise.
    """
    actuals = actuals or {}
    tasks = list(station_tasks)
    shortfalls: list[dict] = []
    recorded = 0

    for task in tasks:
        record = actuals.get(task["task_id"])
        if record is None:
            continue
        recorded += 1
        if float(record.get("variance_qty", 0)) < -_EPS:
            shortfalls.append(
                {
                    "task_id": task["task_id"],
                    "item_id": task["item_id"],
                    "station": task["station"],
                    "unit": task["unit"],
                    "planned_qty": record["planned_qty"],
                    "produced_qty": record["produced_qty"],
                    "variance_qty": record["variance_qty"],
                    "note": record.get("note", ""),
                }
            )

    shortfalls.sort(key=lambda item: item["task_id"])
    return {
        "planned_tasks": len(tasks),
        "recorded_tasks": recorded,
        "unrecorded_tasks": len(tasks) - recorded,
        "short_tasks": len(shortfalls),
        "shortfalls": shortfalls,
    }
