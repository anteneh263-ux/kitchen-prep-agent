"""Recorded production: what a station made against what the plan asked for.

The pipeline otherwise assumes the prep plan was executed. These tests pin the
record, pin that it never touches stock, and pin that silence is treated as
unrecorded rather than as either success or failure.
"""
import pytest

from kitchen_prep import config
from kitchen_prep.gemini.client import OfflineClient
from kitchen_prep.orchestrator import run_daily_prep
from kitchen_prep.pipeline import production


RECORDED_AT = "2026-08-14T10:00:00+00:00"


def _plan(tmp_store):
    return run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient())


def _record(tmp_store, task_id, qty, **extra):
    plan = tmp_store.get_plan(config.DEMO_DATE)
    payload = {"task_id": task_id, "produced_qty": qty, **extra}
    record = production.validate_production(
        payload, plan["station_tasks"], recorded_at=RECORDED_AT
    )
    return tmp_store.record_production(config.DEMO_DATE, record), record


# --- The record ----------------------------------------------------------


def test_the_planned_quantity_comes_from_the_plan_not_the_payload(tmp_store):
    """A client cannot move the target it is being measured against."""
    plan = _plan(tmp_store)
    task = plan["station_tasks"][0]

    record = production.validate_production(
        {"task_id": task["task_id"], "produced_qty": 1, "planned_qty": 9999, "prepare_qty": 9999},
        plan["station_tasks"],
        recorded_at=RECORDED_AT,
    )

    assert record["planned_qty"] == task["prepare_qty"]
    assert record["variance_qty"] == round(1 - task["prepare_qty"], 3)


def test_a_record_carries_the_job_identity_from_the_plan(tmp_store):
    plan = _plan(tmp_store)
    task = next(t for t in plan["station_tasks"] if t["station"] == "cold")

    record = production.validate_production(
        {"task_id": task["task_id"], "produced_qty": task["prepare_qty"], "recorded_by": "Ida"},
        plan["station_tasks"],
        recorded_at=RECORDED_AT,
    )

    assert record["item_id"] == task["item_id"]
    assert record["station"] == "cold"
    assert record["unit"] == task["unit"]
    assert record["variance_qty"] == 0
    assert record["recorded_by"] == "Ida"


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "prep_cold_not_a_thing", "produced_qty": 1},
        {"task_id": "", "produced_qty": 1},
        {"produced_qty": 1},
    ],
)
def test_a_record_for_an_unplanned_job_is_refused(tmp_store, payload):
    plan = _plan(tmp_store)
    with pytest.raises(production.ProductionRejected):
        production.validate_production(payload, plan["station_tasks"], recorded_at=RECORDED_AT)


@pytest.mark.parametrize("qty", [None, "", -1, "plenty", float("inf")])
def test_a_malformed_quantity_is_refused(tmp_store, qty):
    plan = _plan(tmp_store)
    task = plan["station_tasks"][0]
    with pytest.raises(production.ProductionRejected):
        production.validate_production(
            {"task_id": task["task_id"], "produced_qty": qty},
            plan["station_tasks"],
            recorded_at=RECORDED_AT,
        )


def test_producing_nothing_is_a_valid_record(tmp_store):
    """"We never got to it" is information the plan must be able to carry."""
    plan = _plan(tmp_store)
    task = plan["station_tasks"][0]

    record = production.validate_production(
        {"task_id": task["task_id"], "produced_qty": 0},
        plan["station_tasks"],
        recorded_at=RECORDED_AT,
    )

    assert record["produced_qty"] == 0
    assert record["variance_qty"] == -task["prepare_qty"]


# --- Silence is not success ---------------------------------------------


def test_an_unrecorded_job_is_neither_complete_nor_short(tmp_store):
    plan = _plan(tmp_store)
    summary = production.completion(plan["station_tasks"], {})

    assert summary["planned_tasks"] == len(plan["station_tasks"])
    assert summary["recorded_tasks"] == 0
    assert summary["unrecorded_tasks"] == len(plan["station_tasks"])
    assert summary["short_tasks"] == 0
    assert summary["shortfalls"] == []


def test_only_under_delivery_counts_as_a_shortfall(tmp_store):
    plan = _plan(tmp_store)
    short, over = plan["station_tasks"][0], plan["station_tasks"][1]

    _record(tmp_store, short["task_id"], round(short["prepare_qty"] / 2, 3))
    updated, _ = _record(tmp_store, over["task_id"], over["prepare_qty"] + 1)

    summary = production.completion(updated["station_tasks"], updated["production_actuals"])
    assert summary["recorded_tasks"] == 2
    assert [item["task_id"] for item in summary["shortfalls"]] == [short["task_id"]]
    assert summary["shortfalls"][0]["variance_qty"] < 0


# --- Persistence ---------------------------------------------------------


def test_the_record_lands_on_the_plan_and_the_history_is_append_only(tmp_store):
    plan = _plan(tmp_store)
    task = plan["station_tasks"][0]

    _record(tmp_store, task["task_id"], 1, note="first")
    updated, _ = _record(tmp_store, task["task_id"], 2, note="corrected")

    # The current state is the latest figure; the history keeps both.
    assert updated["production_actuals"][task["task_id"]]["produced_qty"] == 2
    assert [entry["note"] for entry in updated["production_history"]] == ["first", "corrected"]


def test_recording_production_never_moves_stock(tmp_store):
    """Unused raw material is corrected by a stock count, not invented back into a batch."""
    plan = _plan(tmp_store)
    before_stock = dict(plan["remaining_stock"])
    before_input = tmp_store.get_inventory_snapshot(config.DEMO_DATE)["input_batches"]
    task = plan["station_tasks"][0]

    updated, _ = _record(tmp_store, task["task_id"], 0)

    assert updated["remaining_stock"] == before_stock
    assert tmp_store.get_inventory_snapshot(config.DEMO_DATE)["input_batches"] == before_input
    assert updated["fefo_consumption"] == plan["fefo_consumption"]


def test_a_record_for_a_date_with_no_plan_is_refused(tmp_store):
    assert tmp_store.record_production("2026-01-01", {"task_id": "x"}) is None


def test_a_forced_replay_keeps_what_people_recorded(tmp_store):
    plan = _plan(tmp_store)
    task = plan["station_tasks"][0]
    _record(tmp_store, task["task_id"], 1, note="kept")

    replay = run_daily_prep(config.DEMO_DATE, store=tmp_store, client=OfflineClient(), force=True)

    assert replay["production_actuals"][task["task_id"]]["produced_qty"] == 1
    assert [entry["note"] for entry in replay["production_history"]] == ["kept"]


# --- Dashboard -----------------------------------------------------------


def test_the_station_list_shows_what_was_recorded(tmp_store):
    from kitchen_prep.render.html import render_home

    plan = _plan(tmp_store)
    task = next(t for t in plan["station_tasks"] if t["station"] == "hot")
    updated, _ = _record(tmp_store, task["task_id"], round(task["prepare_qty"] / 2, 3), note="brant seg")

    page = render_home(updated, language="no", interactive=True)
    assert "variance-tag--short" in page
    assert "ikke registrert" in page, "jobs with no record are marked as such"
    assert 'action="/production"' in page

    read_only = render_home(updated, language="no")
    assert 'action="/production"' not in read_only, "an unauthenticated page offers no write control"
