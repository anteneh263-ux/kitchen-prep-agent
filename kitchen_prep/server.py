"""FastAPI service for Cloud Run.

Endpoints:
  POST /runs/daily   -> start the idempotent daily run (optional {"date": ...})
  GET  /plans/latest -> latest published plan (simple mobile view)
  GET  /healthz      -> liveness

Authentication is enforced at the edge: Cloud Scheduler invokes Cloud Run with an
OIDC token and the service is deployed with --no-allow-unauthenticated (see
deploy/scheduler.md). The run date defaults to today's date in Europe/Oslo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .orchestrator import run_daily_prep, today_oslo
from .data_access import store as store_da
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
