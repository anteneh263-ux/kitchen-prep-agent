"""Public plan viewer plus an isolated synthetic interactive demo.

Production plans remain read-only: this app imports neither the orchestrator nor
the private server and cannot mutate the configured store.  The ``/demo`` routes
operate only on bounded, process-local synthetic sessions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from .data_access import store as store_da
from .demo import (
    DemoCapacityReached,
    DemoInvalidTransition,
    DemoRegistry,
    DemoSessionNotFound,
)
from .render.demo import render_demo
from .render.html import render_home

app = FastAPI(
    title="Kitchen Prep Viewer",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_HERO_IMAGE = Path(__file__).parent / "assets" / "food-hero.webp"
demo_registry = DemoRegistry()


class DemoDecision(BaseModel):
    action: Literal["approve", "reject"]


def _require_demo_header(value: str | None) -> None:
    if value != "1":
        raise HTTPException(status_code=403, detail="missing demo request header")


def _demo_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DemoSessionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DemoCapacityReached):
        return HTTPException(status_code=429, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    """Stable public summary without inventory or model reasoning payloads."""
    return {
        "date": plan["date"],
        "generated_at": plan.get("generated_at"),
        "expected_covers": plan.get("expected_covers"),
        "forecast_source": plan.get("forecast", {}).get("forecast_source"),
        "briefing_source": plan.get("briefing_source"),
        "prep_tasks": len(plan.get("prep_tasks", [])),
        "prep_shortfalls": len(plan.get("prep_shortfalls", [])),
        "replenishment_orders": len(plan.get("replenishment_orders", [])),
        "waste_flagged": len(plan.get("waste_flagged", [])),
    }


@app.get("/", response_class=HTMLResponse)
def home(lang: str = "en", date: str | None = None) -> HTMLResponse:
    """Mobile-friendly view of the latest published plan."""
    store = store_da.get_store()
    plans = store.list_plans(limit=14)
    plan = store.get_plan(date) if date else (plans[0] if plans else None)
    return HTMLResponse(
        content=render_home(
            plan,
            language=lang,
            available_plans=plans,
            demo_url="/demo",
        )
    )


@app.get("/demo", response_class=HTMLResponse)
def interactive_demo() -> HTMLResponse:
    """Credential-free sandbox; explicitly disconnected from production data."""
    return HTMLResponse(
        content=render_demo(),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/demo/sessions")
def create_demo_session(
    x_demo_request: str | None = Header(default=None, alias="X-Demo-Request"),
) -> dict[str, Any]:
    _require_demo_header(x_demo_request)
    try:
        return demo_registry.create()
    except DemoCapacityReached as exc:
        raise _demo_error(exc) from exc


@app.get("/demo/sessions/{session_id}")
def get_demo_session(session_id: str) -> dict[str, Any]:
    try:
        return demo_registry.get(session_id)
    except DemoSessionNotFound as exc:
        raise _demo_error(exc) from exc


@app.get("/demo/sessions/{session_id}/events")
def stream_demo_session(session_id: str) -> StreamingResponse:
    try:
        state = demo_registry.get(session_id)
        if state["status"] != "created":
            raise DemoInvalidTransition("demo run has already started")
    except (DemoSessionNotFound, DemoInvalidTransition) as exc:
        raise _demo_error(exc) from exc

    def stream():
        for event in demo_registry.run(session_id):
            yield f"event: step\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        yield "event: complete\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/demo/sessions/{session_id}/decision")
def decide_demo_session(
    session_id: str,
    decision: DemoDecision,
    x_demo_request: str | None = Header(default=None, alias="X-Demo-Request"),
) -> dict[str, Any]:
    _require_demo_header(x_demo_request)
    try:
        return demo_registry.decide(session_id, decision.action)
    except (DemoSessionNotFound, DemoInvalidTransition) as exc:
        raise _demo_error(exc) from exc


@app.get("/assets/food-hero.webp", include_in_schema=False)
def food_hero() -> FileResponse:
    return FileResponse(_HERO_IMAGE, media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/plans/latest")
def plans_latest() -> dict[str, Any]:
    """Return the latest published plan, or a stable empty-state response."""
    plan = store_da.get_store().get_latest_plan()
    if plan is None:
        return {"detail": "no plans yet"}
    return plan


@app.get("/plans")
def plans_history(limit: int = 14) -> list[dict[str, Any]]:
    """Return capped plan summaries for read-only history navigation."""
    safe_limit = max(1, min(limit, 31))
    return [_plan_summary(plan) for plan in store_da.get_store().list_plans(safe_limit)]


@app.get("/plans/{date}")
def plan_by_date(date: str) -> dict[str, Any]:
    plan = store_da.get_store().get_plan(date)
    if plan is None:
        return {"detail": "plan not found", "date": date}
    return plan


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
