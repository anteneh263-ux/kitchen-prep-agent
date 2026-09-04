"""run_daily_prep: the single orchestrated, idempotent daily pipeline.

Flow: covers -> weather -> forecast (Gemini step 1, validated, deterministic
fallback) -> ingredient requirements -> FEFO consumption + prep shortfalls ->
replenishment orders -> briefing (Gemini step 2, deterministic fallback) ->
markdown -> persist. The run log is always written (finally), even on failure.
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from . import config
from .contracts import Forecast
from .data_access import bookings as bookings_da
from .data_access import menu as menu_da
from .data_access import store as store_da
from .data_access import weather as weather_da
from .gemini import briefing_step, forecast_step
from .gemini.client import GeminiUnavailable, get_client
from .pipeline import costing as costing_pipe
from .pipeline import ingredients as ingredients_pipe
from .pipeline import prep as prep_pipe
from .pipeline import receiving as receiving_pipe
from .pipeline import replenishment as replen_pipe
from .pipeline.baseline import baseline_forecast
from .pipeline.forecast_validate import ForecastRejected, validate_and_build
from .render import markdown as md_render


logger = logging.getLogger(__name__)


def _now_oslo() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).isoformat(timespec="seconds")


def today_oslo() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).date().isoformat()


def _resolve_forecast(date: str, covers: int, weather: dict, client) -> tuple[Forecast, str]:
    """Return (forecast, note). Only model-side failures trigger the baseline."""
    weekday = _date.fromisoformat(date).weekday()
    context = forecast_step.build_context(date, covers, weather, weekday)
    try:
        raw = forecast_step.propose(client, context)
        # The authoritative date/covers are passed in, not read back from the model.
        forecast = validate_and_build(raw, forecast_date=date, expected_covers=covers)
        return forecast, "gemini_ok"
    except GeminiUnavailable as exc:
        logger.warning("forecast fallback: model unavailable: %s", exc)
        return baseline_forecast(date, covers), f"fallback:model_unavailable:{exc}"
    except ForecastRejected as exc:
        logger.warning("forecast fallback: model response rejected: %s", exc)
        return baseline_forecast(date, covers), f"fallback:rejected:{exc}"


def _prior_orders(store: store_da.BaseStore, run_date: str) -> list[dict]:
    """Return orders from earlier plans that arrive on or after ``run_date``."""
    orders: list[dict] = []
    epoch = os.environ.get("KP_INVENTORY_EPOCH")
    for plan in store.list_plans(limit=365):
        plan_date = plan.get("date", run_date)
        if plan_date >= run_date or (epoch and plan_date < epoch):
            continue
        orders.extend(
            dict(order)
            for order in plan.get("replenishment_orders", [])
            if order.get("delivery_date", "") >= run_date
        )
    return orders


def _arrival_batches(run_date: str, orders: list[dict]) -> list[dict]:
    """Convert orders due today into stable, dated inventory batches."""
    ingredients = menu_da.ingredients_by_id()
    delivered = _date.fromisoformat(run_date)
    arrivals: list[dict] = []
    for order in orders:
        if order["delivery_date"] != run_date:
            continue
        item_id = order["item_id"]
        expiry = delivered + timedelta(days=int(ingredients[item_id]["shelf_life_days"]))
        arrivals.append(
            {
                "batch_id": f"delivery-{order['order_by_date']}-{item_id}",
                "item_id": item_id,
                "qty": order["order_qty"],
                "expiry_date": expiry.isoformat(),
            }
        )
    return sorted(arrivals, key=lambda batch: batch["batch_id"])


def _epoch_seed_batches(run_date: str) -> list[dict]:
    """Build fresh synthetic par stock for an explicitly configured epoch."""
    received = _date.fromisoformat(run_date)
    batches: list[dict] = []
    for item_id, meta in menu_da.ingredients_by_id().items():
        expiry = received + timedelta(days=int(meta["shelf_life_days"]))
        batches.append(
            {
                "batch_id": f"epoch-{run_date}-{item_id}",
                "item_id": item_id,
                "qty": meta["par_level"],
                "expiry_date": expiry.isoformat(),
            }
        )
    return sorted(batches, key=lambda batch: batch["batch_id"])


def run_daily_prep(
    date: str | None = None,
    store: store_da.BaseStore | None = None,
    client: Any | None = None,
    force: bool = False,
) -> dict:
    date = date or today_oslo()
    store = store or store_da.get_store()
    client = client or get_client()
    run_id = date
    steps: list[dict] = []

    def log(step: str, **info: Any) -> None:
        steps.append({"ts": _now_oslo(), "step": step, **info})

    status = "ok"
    error: str | None = None
    try:
        # Idempotency: same date never produces a duplicate plan.
        if not force and store.plan_exists(date):
            log("idempotent_hit")
            return store.get_plan(date)
        existing_plan = store.get_plan(date) if force else None

        log("start", date=date)
        covers, covers_source = bookings_da.resolve_expected_covers(date)
        log("covers", expected_covers=covers, covers_source=covers_source)

        weather = weather_da.get_weather(date)
        log("weather", **weather)

        forecast, note = _resolve_forecast(date, covers, weather, client)
        log("forecast", source=forecast.forecast_source, note=note)

        requirement_detail = ingredients_pipe.explode_detail(forecast)
        required = {item: values["purchased"] for item, values in requirement_detail.items()}
        trim_loss = ingredients_pipe.trim_losses(requirement_detail)
        log(
            "ingredient_requirements",
            items=len(required),
            trimmed_items=len(trim_loss),
            trim_loss_total=round(sum(item["trim_loss"] for item in trim_loss), 3),
        )

        epoch = os.environ.get("KP_INVENTORY_EPOCH")
        reset_from_seed = date == epoch
        prior_orders = _prior_orders(store, date)
        arrivals = _arrival_batches(date, prior_orders)
        seed_batches = _epoch_seed_batches(date) if reset_from_seed else store_da.load_seed_batches()
        adjustments = store.list_inventory_adjustments(date)

        def _apply_physical_events(input_batches: list[dict]) -> tuple[list[dict], dict]:
            """Correct the frozen input against what the kitchen actually saw."""
            result = receiving_pipe.apply_adjustments(
                input_batches, adjustments, date, menu_da.ingredients_by_id()
            )
            return result["batches"], {
                "applied": result["applied"],
                "variances": result["variances"],
            }

        batches = store.get_or_create_inventory_input(
            date,
            seed_batches,
            arrivals,
            reset_from_seed=reset_from_seed,
            adjust=_apply_physical_events,
        )
        # Read the report back from the snapshot: on a forced replay the input is
        # already frozen, so the adjustments were applied on the first run only.
        snapshot = store.get_inventory_snapshot(date) or {}
        report = snapshot.get("adjustment_report") or {"applied": [], "variances": []}
        applied_adjustments = report.get("applied", [])
        stock_variances = report.get("variances", [])
        log(
            "inventory_input",
            batches=len(batches),
            arrivals=len(arrivals),
            reset_from_seed=reset_from_seed,
            adjustments_applied=len(applied_adjustments),
            stock_variances=len(stock_variances),
        )
        consumption = prep_pipe.consume_today(required, batches, date)
        log(
            "consume_today",
            shortfalls=len(consumption["prep_shortfalls"]),
            waste=len(consumption["waste_flagged"]),
        )

        prep_tasks = prep_pipe.build_prep_tasks(forecast)
        station_tasks = prep_pipe.build_station_tasks(requirement_detail)
        stations = prep_pipe.station_summary(station_tasks)
        log(
            "station_prep",
            tasks=len(station_tasks),
            stations=len(stations),
            prep_minutes=round(sum(task["prep_minutes"] for task in station_tasks), 1),
        )
        pending_orders = [order for order in prior_orders if order["delivery_date"] > date]
        orders = replen_pipe.compute_orders(consumption["remaining_by_item"], date, pending_orders)
        orders, order_value_total = costing_pipe.value_orders(orders)
        log("replenishment", orders=len(orders), order_value=order_value_total)

        economics = costing_pipe.menu_economics(forecast.to_dict())
        alerts = costing_pipe.margin_alerts(economics)
        waste_value = costing_pipe.waste_value(consumption["waste_flagged"])
        log(
            "costing",
            contribution=costing_pipe.plan_contribution(economics),
            margin_alerts=len(alerts),
            waste_value=waste_value,
        )

        remaining_stock = {
            item: round(sum(b["qty"] for b in batch_list), 3)
            for item, batch_list in consumption["remaining_by_item"].items()
        }
        remaining_batches = [
            dict(batch)
            for item_batches in consumption["remaining_by_item"].values()
            for batch in item_batches
        ]

        plan: dict[str, Any] = {
            "date": date,
            "expected_covers": covers,
            "covers_source": covers_source,
            "planning_basis": replen_pipe.PLANNING_BASIS,
            "inventory_basis": (
                "epoch_seed_snapshot" if reset_from_seed else "date_input_output_snapshot"
            ),
            "forecast": forecast.to_dict(),
            "forecast_note": note,
            "ingredient_requirements": required,
            "ingredient_requirements_prepared": {
                item: values["prepared"] for item, values in requirement_detail.items()
            },
            "trim_loss": trim_loss,
            "prep_tasks": prep_tasks,
            "station_tasks": station_tasks,
            "stations": stations,
            "fefo_consumption": consumption["fefo_consumption"],
            "prep_shortfalls": consumption["prep_shortfalls"],
            "replenishment_orders": orders,
            "waste_flagged": consumption["waste_flagged"],
            "currency": config.CURRENCY,
            "dish_economics": economics,
            "margin_alerts": alerts,
            "plan_contribution": costing_pipe.plan_contribution(economics),
            "order_value_total": order_value_total,
            "waste_value": waste_value,
            "inventory_adjustments": applied_adjustments,
            "stock_variances": stock_variances,
            "remaining_stock": remaining_stock,
            "generated_at": _now_oslo(),
        }

        briefing, briefing_source = briefing_step.make_briefing(client, plan)
        plan["briefing"] = briefing
        plan["briefing_source"] = briefing_source
        plan["briefing_markdown"] = md_render.render(plan)
        # Operator decisions and recorded production are audit data: they record
        # what people did, so a recalculation must never erase them.
        if existing_plan:
            for key in (
                "operational_actions",
                "action_history",
                "production_actuals",
                "production_history",
            ):
                if key in existing_plan:
                    plan[key] = existing_plan[key]
        log("briefing", source=briefing_source)

        saved = store.save_plan(date, plan, overwrite=force)
        store.save_inventory_output(date, remaining_batches)
        log("saved")
        return saved
    except Exception as exc:  # noqa: BLE001 - logged then re-raised (error policy)
        status = "error"
        error = repr(exc)
        raise
    finally:
        # Run log is preserved even when the pipeline fails.
        store.append_run_log(
            run_id,
            {"ts": _now_oslo(), "date": date, "status": status, "error": error, "steps": steps},
        )
