"""Isolated interactive demo workflow.

The public demo exercises the real deterministic forecast, recipe, FEFO, prep
and replenishment functions against synthetic data.  It never reads or writes
the production store and never sends a real supplier order.  Session state is
bounded, process-local and short-lived by design.
"""
from __future__ import annotations

import secrets
import time
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from threading import Lock
from typing import Any

from . import config
from .data_access import menu as menu_da
from .data_access import weather as weather_da
from .pipeline import ingredients as ingredients_pipe
from .pipeline import prep as prep_pipe
from .pipeline import replenishment as replen_pipe
from .pipeline.baseline import baseline_forecast

DEMO_DATE = "2026-08-28"
DEMO_COVERS = 139
DEMO_TARGET_ITEM = "chicken_wings"
DEMO_SHORTFALL = 4.0
SESSION_TTL_SECONDS = 15 * 60
MAX_SESSIONS = 60


class DemoSessionError(Exception):
    """Base class for safe, expected demo-session failures."""


class DemoSessionNotFound(DemoSessionError):
    pass


class DemoCapacityReached(DemoSessionError):
    pass


class DemoInvalidTransition(DemoSessionError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _build_demo_batches(required: dict[str, float]) -> list[dict[str, Any]]:
    """Create synthetic valid stock with exactly one controlled shortfall."""
    run_date = _date.fromisoformat(DEMO_DATE)
    ingredients = menu_da.ingredients_by_id()
    batches: list[dict[str, Any]] = []
    for item_id, amount in required.items():
        meta = ingredients[item_id]
        available = float(amount)
        if item_id == DEMO_TARGET_ITEM:
            available = max(0.0, available - DEMO_SHORTFALL)
        expiry = run_date + timedelta(days=max(1, int(meta["shelf_life_days"])))
        batches.append(
            {
                "batch_id": f"demo-{item_id}",
                "item_id": item_id,
                "qty": round(available, 3),
                "expiry_date": expiry.isoformat(),
            }
        )
    # A separate expired batch proves FEFO waste isolation without creating a
    # second shortage: the valid cheddar batch above already covers demand.
    batches.append(
        {
            "batch_id": "demo-expired-cheddar",
            "item_id": "cheddar",
            "qty": 0.8,
            "expiry_date": (run_date - timedelta(days=1)).isoformat(),
        }
    )
    return sorted(batches, key=lambda batch: batch["batch_id"])


def build_demo_plan(emit) -> dict[str, Any]:
    """Run the real deterministic planning functions and emit tool evidence."""
    emit(
        "sales_history_loaded",
        "Loaded four weeks of simulated POS sales history",
        "sales.same_weekday_observations",
    )
    emit(
        "covers_resolved",
        f"Resolved {DEMO_COVERS} expected covers from the demo reservation dataset",
        "bookings.resolve_expected_covers",
    )
    weather = weather_da.get_weather_offline(
        DEMO_DATE,
        config.RESTAURANT_LAT,
        config.RESTAURANT_LON,
    )
    emit(
        "weather_loaded",
        (
            f"Loaded safe weather context: code {weather['weather_code']}, "
            f"{weather['temperature_c']}°C, {weather['precipitation_mm']} mm precipitation"
        ),
        "weather.get_weather_offline",
    )
    forecast = baseline_forecast(DEMO_DATE, DEMO_COVERS)
    emit(
        "forecast_completed",
        f"Validated demand forecast for {len(forecast.dishes)} menu items",
        "baseline_forecast",
    )
    required = ingredients_pipe.explode_to_ingredients(forecast)
    emit(
        "recipes_expanded",
        f"Expanded six recipes into {len(required)} ingredient requirements",
        "ingredients.explode_to_ingredients",
    )
    batches = _build_demo_batches(required)
    consumption = prep_pipe.consume_today(required, batches, DEMO_DATE)
    orders = replen_pipe.compute_orders(consumption["remaining_by_item"], DEMO_DATE)
    emit(
        "inventory_reconciled",
        (
            f"Reconciled {len(batches)} dated batches using FEFO; "
            f"isolated {len(consumption['waste_flagged'])} expired batch"
        ),
        "prep.consume_today + replenishment.compute_orders",
    )
    shortfalls = consumption["prep_shortfalls"]
    if len(shortfalls) != 1 or shortfalls[0]["item_id"] != DEMO_TARGET_ITEM:
        raise RuntimeError("demo fixture must produce exactly one chicken_wings shortfall")
    shortfall = shortfalls[0]
    emit(
        "shortage_detected",
        f"Detected a critical {shortfall['shortfall']} kg chicken-wings service shortfall",
        "prep.consume_today",
    )
    proposal = {
        "action": "emergency_order",
        "item_id": DEMO_TARGET_ITEM,
        "qty": shortfall["shortfall"],
        "unit": "kg",
        "supplier": "Demo Supply Co (simulated)",
        "eta": "15:10",
        "required_before": "16:00",
        "requires_human_approval": True,
        "reason": "Arrives before service and covers the verified shortage.",
    }
    emit(
        "decision_required",
        "Prepared one simulated emergency order and paused for human approval",
        "demo.action_policy",
    )
    return {
        "date": DEMO_DATE,
        "expected_covers": DEMO_COVERS,
        "weather": weather,
        "forecast": forecast.to_dict(),
        "ingredient_requirements": required,
        "prep_tasks": prep_pipe.build_prep_tasks(forecast),
        "fefo_consumption": consumption["fefo_consumption"],
        "prep_shortfalls": shortfalls,
        "waste_flagged": consumption["waste_flagged"],
        "replenishment_orders": orders,
        "proposal": proposal,
    }


class DemoRegistry:
    """Bounded in-memory demo sessions; contains no production plan data."""

    def __init__(
        self,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        max_sessions: int = MAX_SESSIONS,
        event_delay: float = 0.28,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.event_delay = event_delay
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def _cleanup_locked(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if now - session["created_monotonic"] > self.ttl_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]

    def create(self) -> dict[str, Any]:
        with self._lock:
            self._cleanup_locked()
            if len(self._sessions) >= self.max_sessions:
                raise DemoCapacityReached("demo capacity reached; try again shortly")
            session_id = secrets.token_urlsafe(18)
            now = _utc_now()
            self._sessions[session_id] = {
                "session_id": session_id,
                "created_at": now,
                "created_monotonic": time.monotonic(),
                "status": "created",
                "events": [],
                "plan": None,
                "order": None,
                "result": None,
            }
            return self._public_state_locked(self._sessions[session_id])

    def _session_locked(self, session_id: str) -> dict[str, Any]:
        self._cleanup_locked()
        session = self._sessions.get(session_id)
        if session is None:
            raise DemoSessionNotFound("demo session not found or expired")
        return session

    @staticmethod
    def _public_state_locked(session: dict[str, Any]) -> dict[str, Any]:
        return deepcopy({key: value for key, value in session.items() if key != "created_monotonic"})

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_state_locked(self._session_locked(session_id))

    def _append_event(self, session_id: str, event_type: str, detail: str, tool: str) -> dict[str, Any]:
        with self._lock:
            session = self._session_locked(session_id)
            event = {
                "sequence": len(session["events"]) + 1,
                "type": event_type,
                "detail": detail,
                "tool": tool,
                "occurred_at": _utc_now(),
            }
            session["events"].append(event)
            return deepcopy(event)

    def run(self, session_id: str) -> Iterator[dict[str, Any]]:
        with self._lock:
            session = self._session_locked(session_id)
            if session["status"] != "created":
                raise DemoInvalidTransition("demo run has already started")
            session["status"] = "running"

        emitted: list[dict[str, Any]] = []

        def emit(event_type: str, detail: str, tool: str) -> None:
            emitted.append(self._append_event(session_id, event_type, detail, tool))

        try:
            plan = build_demo_plan(emit)
            for event in emitted:
                if self.event_delay:
                    time.sleep(self.event_delay)
                yield event
            with self._lock:
                session = self._session_locked(session_id)
                session["plan"] = plan
                session["status"] = "awaiting_decision"
        except Exception:
            with self._lock:
                session = self._sessions.get(session_id)
                if session is not None:
                    session["status"] = "failed"
            raise

    def decide(self, session_id: str, action: str) -> dict[str, Any]:
        if action not in {"approve", "reject"}:
            raise DemoInvalidTransition("decision must be approve or reject")
        with self._lock:
            session = self._session_locked(session_id)
            if session["status"] != "awaiting_decision":
                raise DemoInvalidTransition("session is not awaiting a decision")
            proposal = session["plan"]["proposal"]
            if action == "reject":
                session["status"] = "rejected"
                session["result"] = "Proposal rejected; the service risk remains open."
                self._append_event_locked(
                    session,
                    "decision_rejected",
                    "Operator rejected the simulated emergency order; no action executed",
                    "demo.decision",
                )
                return self._public_state_locked(session)

            order_id = f"DEMO-{session_id[:6].upper()}"
            session["order"] = {
                "order_id": order_id,
                "item_id": proposal["item_id"],
                "qty": proposal["qty"],
                "unit": proposal["unit"],
                "supplier": proposal["supplier"],
                "eta": proposal["eta"],
                "status": "submitted",
                "simulated": True,
            }
            self._append_event_locked(
                session,
                "decision_approved",
                "Operator approved the simulated emergency order",
                "demo.decision",
            )
            self._append_event_locked(
                session,
                "order_submitted",
                f"Submitted simulated order {order_id}; supplier ETA {proposal['eta']}",
                "demo.supplier_connector",
            )
            session["status"] = "mitigation_scheduled"
            session["result"] = "1 service risk detected; mitigation scheduled before service."
            self._append_event_locked(
                session,
                "mitigation_scheduled",
                "Status changed to mitigation scheduled; production inventory was not modified",
                "demo.state_machine",
            )
            return self._public_state_locked(session)

    @staticmethod
    def _append_event_locked(
        session: dict[str, Any],
        event_type: str,
        detail: str,
        tool: str,
    ) -> None:
        session["events"].append(
            {
                "sequence": len(session["events"]) + 1,
                "type": event_type,
                "detail": detail,
                "tool": tool,
                "occurred_at": _utc_now(),
            }
        )
