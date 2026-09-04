"""FastAPI service for Cloud Run.

Endpoints:
  POST /runs/daily            -> start the idempotent daily run (optional {"date": ...})
  GET  /plans/latest          -> latest published plan (simple mobile view)
  POST /inventory/receipts    -> record what a delivery actually contained
  POST /inventory/counts      -> record what a physical count actually found
  GET  /inventory/adjustments -> recorded physical events for a date
  GET  /healthz               -> liveness

The two inventory endpoints accept either JSON (machine callers) or an
HTML form post (the kitchen screen), so the page needs no JavaScript and the
service needs no form-parsing dependency.

Authentication is enforced at the edge: Cloud Scheduler invokes Cloud Run with an
OIDC token and the service is deployed with --no-allow-unauthenticated (see
deploy/scheduler.md). The run date defaults to today's date in Europe/Oslo.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .orchestrator import run_daily_prep, today_oslo
from .data_access import menu as menu_da
from .data_access import store as store_da
from .pipeline import receiving
from .render.html import render_home

app = FastAPI(title="Kitchen Prep Agent")
_HERO_IMAGE = Path(__file__).parent / "assets" / "food-hero.webp"


class RunRequest(BaseModel):
    date: str | None = None
    force: bool = False


@app.get("/", response_class=HTMLResponse)
def home(lang: str = "no", date: str | None = None) -> HTMLResponse:
    """Mobile-friendly server-rendered view of the latest published plan."""
    store = store_da.get_store()
    plans = store.list_plans(limit=14)
    plan = store.get_plan(date) if date else (plans[0] if plans else None)
    return HTMLResponse(content=render_home(plan, language=lang, available_plans=plans, interactive=True))


@app.get("/assets/food-hero.webp", include_in_schema=False)
def food_hero() -> FileResponse:
    return FileResponse(_HERO_IMAGE, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs/daily")
def runs_daily(req: RunRequest | None = None) -> dict[str, Any]:
    req = req or RunRequest()
    date = req.date or today_oslo()
    plan = run_daily_prep(date=date, force=req.force)
    # Idempotent summary; the full plan is available via /plans/latest.
    return {
        "date": plan["date"],
        "expected_covers": plan["expected_covers"],
        "prep_tasks": len(plan["prep_tasks"]),
        "prep_shortfalls": len(plan["prep_shortfalls"]),
        "replenishment_orders": len(plan["replenishment_orders"]),
        "waste_flagged": len(plan["waste_flagged"]),
        "forecast_source": plan["forecast"]["forecast_source"],
        "forecast_note": plan.get("forecast_note", "unavailable"),
    }


@app.get("/plans/latest")
def plans_latest() -> dict[str, Any]:
    plan = store_da.get_store().get_latest_plan()
    if plan is None:
        return {"detail": "no plans yet"}
    return plan


@app.post("/plans/{date}/actions/{item_id}/{status}")
def update_plan_action(
    date: str,
    item_id: str,
    status: Literal["approved", "resolved", "reopened"],
    lang: str = "no",
) -> RedirectResponse:
    """Record an authenticated operator decision with an append-only audit event."""
    store = store_da.get_store()
    plan = store.get_plan(date)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan not found")
    actionable_items = {
        str(item.get("item_id"))
        for key in ("prep_shortfalls", "replenishment_orders")
        for item in plan.get(key, [])
        if item.get("item_id")
    }
    if item_id not in actionable_items:
        raise HTTPException(status_code=404, detail="action item not found")
    occurred_at = datetime.now(timezone.utc).isoformat()
    store.record_plan_action(date, item_id, status, occurred_at)
    return RedirectResponse(url=f"/?lang={lang}&date={date}#critical-actions", status_code=303)


# --- Physical inventory: goods receipt and stock counts -------------------


async def _request_payload(request: Request) -> tuple[dict[str, Any], bool]:
    """Return ``(payload, from_form)`` for a JSON body or an HTML form post."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    raw = await request.body()

    if content_type == "application/x-www-form-urlencoded":
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        return {key: values[-1] for key, values in parsed.items()}, True

    if not raw:
        return dict(request.query_params), False

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    return data, False


async def _record_adjustment(request: Request, kind: str):
    payload, from_form = await _request_payload(request)
    payload["type"] = kind
    store = store_da.get_store()

    try:
        # A day whose input is already frozen cannot be changed, so the event
        # rolls forward to the next open planning date instead of being lost.
        effective_date = receiving.resolve_effective_date(
            str(payload.get("effective_date") or today_oslo()),
            store.inventory_snapshot_exists,
        )
        adjustment = receiving.validate_adjustment(
            payload,
            menu_da.ingredients_by_id(),
            adjustment_id=uuid4().hex[:12],
            effective_date=effective_date,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
    except ValueError as exc:  # AdjustmentRejected and malformed dates
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    store.append_inventory_adjustment(adjustment)

    if from_form:
        lang = "en" if str(payload.get("lang", "no")) == "en" else "no"
        return RedirectResponse(url=f"/?lang={lang}#inventory-receiving", status_code=303)
    return JSONResponse(content=adjustment, status_code=201)


@app.post("/inventory/receipts")
async def record_receipt(request: Request):
    """Record what a delivery actually contained.

    Body: ``item_id``, ``qty_received``, and optionally ``order_by_date`` (to
    correct the assumed arrival), ``expiry_date``, ``note``, ``recorded_by``.
    """
    return await _record_adjustment(request, "receipt")


@app.post("/inventory/counts")
async def record_count(request: Request):
    """Record what a physical count actually found.

    Body: ``item_id``, ``counted_qty``, and optionally ``note``, ``recorded_by``.
    """
    return await _record_adjustment(request, "count")


@app.get("/inventory/adjustments")
def list_adjustments(date: str | None = None) -> dict[str, Any]:
    """Recorded physical events awaiting (or applied to) a planning date."""
    store = store_da.get_store()
    effective_date = date or today_oslo()
    return {
        "effective_date": effective_date,
        "adjustments": store.list_inventory_adjustments(effective_date),
    }
