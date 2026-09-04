"""Physical inventory corrections: goods receipt and stock counts.

Without this module the planner is blind to reality in two specific ways: it
assumes every ordered quantity arrives in full on its delivery date, and it
assumes theoretical FEFO consumption is what physically happened. Both
assumptions compound silently down the snapshot chain.

Two recorded event types correct them:

``receipt``
    What a delivery actually contained. A receipt may replace the assumed
    arrival batch, so a partial or late delivery reduces stock instead of
    inflating it.

``count``
    What a physical count actually found. The counted quantity becomes the
    truth for that item and the batch quantities are reconciled to it.

Rules (locked):
  1. Adjustments are applied while a date's inventory input is frozen, never
     afterwards — a frozen input stays replay-safe.
  2. Receipts are applied before counts, so a count reconciles post-delivery
     stock.
  3. A count shortage is removed earliest-expiry-first: missing stock is most
     likely the oldest, and removing it there is the conservative choice.
  4. A count surplus is added as a single batch carrying the item's latest
     known expiry, never an invented later one.
  5. Nothing here reads or writes storage, and nothing here calls a model.
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Any, Callable, Iterable

ADJUSTMENT_TYPES = ("receipt", "count")

# Receipts land before counts; within a type, ordering is by item then id.
_TYPE_RANK = {"receipt": 0, "count": 1}

_EPS = 1e-9
_MAX_ROLL_FORWARD_DAYS = 365


class AdjustmentRejected(ValueError):
    """A recorded adjustment is malformed and must not enter the chain."""


def _as_qty(value: Any, field: str) -> float:
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise AdjustmentRejected(f"{field} must be a number") from exc
    if qty != qty or qty in (float("inf"), float("-inf")):
        raise AdjustmentRejected(f"{field} must be finite")
    if qty < 0:
        raise AdjustmentRejected(f"{field} must not be negative")
    return round(qty, 3)


def _as_date(value: Any, field: str) -> str:
    try:
        return _date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise AdjustmentRejected(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def validate_adjustment(
    payload: dict,
    ingredients: dict[str, dict],
    *,
    adjustment_id: str,
    effective_date: str,
    recorded_at: str,
) -> dict:
    """Return a stored-shape adjustment, or raise ``AdjustmentRejected``.

    The caller owns identity and time: the id, the effective date and the
    recording timestamp are passed in rather than read from the payload, so a
    client cannot backdate an adjustment into an already-frozen day.
    """
    kind = str(payload.get("type", "")).strip()
    if kind not in ADJUSTMENT_TYPES:
        raise AdjustmentRejected(f"type must be one of {', '.join(ADJUSTMENT_TYPES)}")

    item_id = str(payload.get("item_id", "")).strip()
    if item_id not in ingredients:
        raise AdjustmentRejected(f"unknown item_id: {item_id or '(missing)'}")

    qty_field = "qty_received" if kind == "receipt" else "counted_qty"
    raw_qty = payload.get(qty_field, payload.get("qty"))
    if raw_qty is None or raw_qty == "":
        raise AdjustmentRejected(f"{qty_field} is required")
    qty = _as_qty(raw_qty, qty_field)

    adjustment: dict[str, Any] = {
        "adjustment_id": adjustment_id,
        "type": kind,
        "item_id": item_id,
        "qty": qty,
        "unit": ingredients[item_id]["unit"],
        "effective_date": _as_date(effective_date, "effective_date"),
        "recorded_at": recorded_at,
        "recorded_by": str(payload.get("recorded_by") or "kitchen")[:60],
        "note": str(payload.get("note") or "")[:200],
    }

    if kind == "receipt":
        expiry = payload.get("expiry_date")
        adjustment["expiry_date"] = _as_date(expiry, "expiry_date") if expiry else None
        replaces = payload.get("replaces_batch_id")
        order_by_date = payload.get("order_by_date")
        if not replaces and order_by_date:
            # The arrival batch id the orchestrator generates for an ordered delivery.
            replaces = f"delivery-{_as_date(order_by_date, 'order_by_date')}-{item_id}"
        adjustment["replaces_batch_id"] = str(replaces) if replaces else None

    return adjustment


def resolve_effective_date(from_date: str, snapshot_exists: Callable[[str], bool]) -> str:
    """First date at or after ``from_date`` whose inventory input is not frozen.

    A delivery received after this morning's run cannot change a day that is
    already planned, so it rolls forward to the next open day instead of being
    silently dropped.
    """
    current = _date.fromisoformat(from_date)
    for _ in range(_MAX_ROLL_FORWARD_DAYS):
        if not snapshot_exists(current.isoformat()):
            return current.isoformat()
        current += timedelta(days=1)
    raise AdjustmentRejected("no open inventory date within a year")


def _default_expiry(item_id: str, run_date: str, ingredients: dict[str, dict]) -> str:
    shelf_life = int(ingredients[item_id]["shelf_life_days"])
    return (_date.fromisoformat(run_date) + timedelta(days=shelf_life)).isoformat()


def _sort_key(adjustment: dict) -> tuple:
    return (
        _TYPE_RANK.get(adjustment.get("type", ""), 99),
        str(adjustment.get("item_id", "")),
        str(adjustment.get("adjustment_id", "")),
    )


def _apply_receipt(
    batches: list[dict],
    adjustment: dict,
    run_date: str,
    ingredients: dict[str, dict],
) -> tuple[list[dict], dict, dict | None]:
    item_id = adjustment["item_id"]
    replaces = adjustment.get("replaces_batch_id")

    expected = 0.0
    kept: list[dict] = []
    for batch in batches:
        if replaces and batch["batch_id"] == replaces:
            expected += float(batch["qty"])
            continue
        kept.append(batch)
    expected = round(expected, 3)

    qty = adjustment["qty"]
    if qty > _EPS:
        kept.append(
            {
                "batch_id": f"receipt-{adjustment['adjustment_id']}",
                "item_id": item_id,
                "qty": qty,
                "expiry_date": adjustment.get("expiry_date")
                or _default_expiry(item_id, run_date, ingredients),
            }
        )

    effect = {
        "expected_qty": expected,
        "actual_qty": qty,
        "replaced_batch_id": replaces if (replaces and expected > _EPS) else None,
    }
    variance = None
    if abs(qty - expected) > _EPS and (replaces or expected > _EPS):
        variance = {
            "adjustment_id": adjustment["adjustment_id"],
            "type": "receipt",
            "item_id": item_id,
            "unit": adjustment["unit"],
            "expected_qty": expected,
            "actual_qty": qty,
            "variance_qty": round(qty - expected, 3),
        }
    return kept, effect, variance


def _apply_count(
    batches: list[dict],
    adjustment: dict,
    run_date: str,
    ingredients: dict[str, dict],
) -> tuple[list[dict], dict, dict | None]:
    item_id = adjustment["item_id"]
    counted = adjustment["qty"]

    item_batches = [b for b in batches if b["item_id"] == item_id]
    others = [b for b in batches if b["item_id"] != item_id]
    theoretical = round(sum(float(b["qty"]) for b in item_batches), 3)
    difference = round(counted - theoretical, 3)

    if abs(difference) <= _EPS:
        effect = {"expected_qty": theoretical, "actual_qty": counted, "variance_qty": 0.0}
        return batches, effect, None

    item_batches.sort(key=lambda b: (b["expiry_date"], b["batch_id"]))

    if difference < 0:
        # Remove the shortage earliest-expiry-first.
        missing = -difference
        reconciled: list[dict] = []
        for batch in item_batches:
            if missing <= _EPS:
                reconciled.append(batch)
                continue
            available = float(batch["qty"])
            taken = min(available, missing)
            missing = round(missing - taken, 3)
            left = round(available - taken, 3)
            if left > _EPS:
                reconciled.append({**batch, "qty": left})
        item_batches = reconciled
    else:
        latest_expiry = (
            max(b["expiry_date"] for b in item_batches)
            if item_batches
            else _default_expiry(item_id, run_date, ingredients)
        )
        item_batches.append(
            {
                "batch_id": f"count-{adjustment['adjustment_id']}",
                "item_id": item_id,
                "qty": difference,
                "expiry_date": latest_expiry,
            }
        )

    effect = {
        "expected_qty": theoretical,
        "actual_qty": counted,
        "variance_qty": difference,
    }
    variance = {
        "adjustment_id": adjustment["adjustment_id"],
        "type": "count",
        "item_id": item_id,
        "unit": adjustment["unit"],
        "expected_qty": theoretical,
        "actual_qty": counted,
        "variance_qty": difference,
    }
    return others + item_batches, effect, variance


def apply_adjustments(
    batches: Iterable[dict],
    adjustments: Iterable[dict],
    run_date: str,
    ingredients: dict[str, dict],
) -> dict:
    """Apply recorded physical events to a day's inventory input batches.

    Returns ``{"batches", "applied", "variances"}``. ``batches`` is the
    corrected input; ``applied`` records what each adjustment did; ``variances``
    holds only the entries where physical reality differed from the plan.
    """
    working = [dict(batch) for batch in batches]
    applied: list[dict] = []
    variances: list[dict] = []

    for adjustment in sorted((dict(a) for a in adjustments), key=_sort_key):
        kind = adjustment.get("type")
        if kind == "receipt":
            working, effect, variance = _apply_receipt(working, adjustment, run_date, ingredients)
        elif kind == "count":
            working, effect, variance = _apply_count(working, adjustment, run_date, ingredients)
        else:  # An unknown type never silently mutates stock.
            continue
        applied.append({**adjustment, "effect": effect})
        if variance is not None:
            variances.append(variance)

    if applied:
        # Only a day that actually recorded events is reordered; a quiet day
        # must hand the snapshot chain back exactly as it received it.
        working.sort(key=lambda b: (b["item_id"], b["expiry_date"], b["batch_id"]))
    return {"batches": working, "applied": applied, "variances": variances}
